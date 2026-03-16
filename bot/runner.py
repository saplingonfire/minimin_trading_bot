"""Run loop: load strategy, build context each tick, execute signals, shutdown cleanly."""

import logging
import signal
import time

from roostoo.client import RoostooClient

from bot.base import Strategy, TradingContext
from bot.execution import Executor
from bot.market import build_context
from bot.strategies import STRATEGIES
from config.settings import BotSettings

logger = logging.getLogger(__name__)

_shutdown_requested = False


def _shutdown_handler(_signum: int, _frame: object) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("shutdown requested")


def run(settings: BotSettings) -> None:
    """Run the bot: create client, strategy, executor; tick until shutdown. Never log secrets."""
    global _shutdown_requested
    _shutdown_requested = False

    strategy_name = settings.strategy_name
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"unknown strategy strategy={strategy_name} available={list(STRATEGIES.keys())}"
        )

    logger.info(
        "bot start strategy=%s dry_run=%s tick_seconds=%s cancel_orders_on_stop=%s",
        strategy_name,
        settings.dry_run,
        settings.tick_seconds,
        settings.cancel_orders_on_stop,
    )

    client = RoostooClient(
        api_key=settings.api_key,
        secret_key=settings.secret_key,
        base_url=settings.base_url,
    )

    exchange_info: dict | None = None
    try:
        exchange_info = client.get_exchange_info()
    except Exception as e:
        logger.warning("exchange_info fetch failed: %s", e)

    executor = Executor(
        client=client,
        dry_run=settings.dry_run,
        exchange_info=exchange_info,
        max_pending_orders=settings.max_pending_orders,
        max_order_notional=settings.max_order_notional,
    )

    strategy_cls = STRATEGIES[strategy_name]
    strategy = strategy_cls(settings.strategy_params)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    strategy.on_start()

    tick_index = 0
    try:
        while not _shutdown_requested:
            tick_start = time.perf_counter()
            pair = (settings.strategy_params or {}).get("pair")

            try:
                context = build_context(
                    client,
                    pair=pair,
                    exchange_info=exchange_info,
                )
            except Exception as e:
                logger.exception("build_context failed")
                time.sleep(settings.tick_seconds)
                tick_index += 1
                continue

            build_ms = (time.perf_counter() - tick_start) * 1000

            signals = strategy.next(context)
            exec_start = time.perf_counter()
            results = executor.execute(signals, context_ticker=context.ticker)
            exec_ms = (time.perf_counter() - exec_start) * 1000

            logger.info(
                "tick tick_index=%s signals=%s build_context_ms=%.0f execute_ms=%.0f",
                tick_index,
                len(signals),
                build_ms,
                exec_ms,
            )
            if results and logger.isEnabledFor(logging.DEBUG):
                for i, r in enumerate(results):
                    oid = r.get("OrderID") or r.get("order_id")
                    if oid is not None:
                        logger.debug("order result index=%s order_id=%s", i, oid)

            time.sleep(settings.tick_seconds)
            tick_index += 1
    finally:
        if settings.cancel_orders_on_stop:
            managed = strategy.get_managed_pairs()
            if managed:
                executor.cancel_orders_for_pairs(managed)
                logger.info("cancel_orders_on_stop pairs=%s", managed)
        strategy.on_stop()

    logger.info("bot stopped tick_index=%s", tick_index)
