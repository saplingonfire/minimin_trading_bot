"""Run loop: load strategy, build context each tick, execute signals, shutdown cleanly."""

import dataclasses
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

from roostoo.client import RoostooClient

from bot.base import Strategy, TradingContext
from bot.execution import Executor
from bot.market import build_context
from bot.ohlcv import BinanceHistoricalFileProvider
from bot.price_store import PriceStore, warmup_from_binance_klines
from bot.risk import kill_switch_check
from bot.strategies import STRATEGIES
from config.settings import BotSettings

logger = logging.getLogger(__name__)

_shutdown_requested = False

# Strategies that need all tickers and a price store (pair=None, append ticker each cycle)
HYBRID_LIKE_STRATEGIES = {"hybrid_trend_cross_sectional", "hybrid_trend_cross_sectional_throttled"}


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
        "bot start strategy=%s live=%s dry_run=%s tick_seconds=%s cancel_orders_on_stop=%s",
        strategy_name,
        settings.live,
        settings.dry_run,
        settings.tick_seconds,
        settings.cancel_orders_on_stop,
    )

    for _log_path in (settings.trades_log_path, settings.roostoo_api_log_path):
        Path(_log_path).parent.mkdir(parents=True, exist_ok=True)

    client = RoostooClient(
        api_key=settings.api_key,
        secret_key=settings.secret_key,
        base_url=settings.base_url,
        api_log_path=settings.roostoo_api_log_path,
    )

    exchange_info: dict | None = None
    try:
        exchange_info = client.get_exchange_info()
    except Exception as e:
        logger.warning("exchange_info fetch failed: %s", e)

    max_orders_per_cycle = settings.max_orders_per_cycle
    order_spacing_sec = settings.order_spacing_sec
    if strategy_name in HYBRID_LIKE_STRATEGIES:
        if max_orders_per_cycle is None:
            max_orders_per_cycle = (settings.strategy_params or {}).get("max_orders_per_cycle", 2)
        if order_spacing_sec is None:
            order_spacing_sec = (settings.strategy_params or {}).get("order_spacing_sec", 65)

    executor = Executor(
        client=client,
        dry_run=settings.dry_run,
        exchange_info=exchange_info,
        max_pending_orders=settings.max_pending_orders,
        max_order_notional=settings.max_order_notional,
        order_spacing_sec=order_spacing_sec if isinstance(order_spacing_sec, (int, float)) else None,
        trades_log_path=settings.trades_log_path,
        stale_order_timeout_sec=settings.stale_order_timeout_sec,
        retry_delays=settings.retry_delays,
        retry_statuses=settings.retry_statuses,
    )

    strategy_cls = STRATEGIES[strategy_name]
    strategy = strategy_cls(settings.strategy_params)

    ohlcv_provider = None
    data_dir = os.environ.get("BINANCE_DATA_DIR", "").strip()
    if data_dir:
        ohlcv_provider = BinanceHistoricalFileProvider(data_dir)

    price_store: PriceStore | None = None
    if strategy_name in HYBRID_LIKE_STRATEGIES:
        db_path = (
            settings.price_store_path
            or (settings.strategy_params or {}).get("db_path")
            or "prices.db"
        )
        price_store = PriceStore(db_path)
        if price_store.count_days_with_data("BTC/USD") < settings.warmup_min_btc_days:
            warmup_from_binance_klines(price_store, limit=settings.warmup_backfill_days)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    strategy.on_start()

    tick_index = 0
    consecutive_api_errors = 0
    consecutive_db_errors = 0
    risk_config = (settings.strategy_params or {}).get("risk") or {}
    max_errors = risk_config.get("max_consecutive_errors", 5)
    max_db_errors = risk_config.get("max_consecutive_db_errors", 5)
    max_drift_ms = risk_config.get("max_drift_ms", 60_000)
    btc_move_kill = risk_config.get("btc_daily_move_kill", 0.15)
    try:
        while not _shutdown_requested:
            tick_start = time.perf_counter()
            # Hybrid strategy needs all pairs; others may use a single pair from params
            pair = None if strategy_name in HYBRID_LIKE_STRATEGIES else (settings.strategy_params or {}).get("pair")

            try:
                context = build_context(
                    client,
                    pair=pair,
                    exchange_info=exchange_info,
                    ohlcv_provider=ohlcv_provider,
                    price_store=price_store,
                    risk_force_cash=False,
                )
                consecutive_api_errors = 0
            except Exception as e:
                logger.exception("build_context failed")
                consecutive_api_errors += 1
                # Use current time so drift check does not false-trigger when we have no context
                server_time_ms_fallback = int(time.time() * 1000)
                halt, force_cash = kill_switch_check(
                    consecutive_api_errors,
                    server_time_ms_fallback,
                    None,
                    max_consecutive_errors=max_errors,
                    max_drift_ms=max_drift_ms,
                    btc_daily_move_kill=btc_move_kill,
                )
                if halt:
                    logger.critical("KILL SWITCH: halting after API failures")
                    sys.exit(1)
                time.sleep(settings.tick_seconds)
                tick_index += 1
                continue

            btc_row = context.ticker.get("BTC/USD") or context.ticker.get("BTCUSD")
            btc_change: float | None = None
            if isinstance(btc_row, dict):
                btc_change = btc_row.get("Change") or btc_row.get("change")
                if btc_change is not None:
                    btc_change = float(btc_change)
            halt, force_cash = kill_switch_check(
                consecutive_api_errors,
                context.server_time_ms,
                btc_change,
                max_consecutive_errors=max_errors,
                max_drift_ms=max_drift_ms,
                btc_daily_move_kill=btc_move_kill,
            )
            if halt:
                logger.critical("KILL SWITCH: halting")
                sys.exit(1)
            if force_cash:
                context = dataclasses.replace(context, risk_force_cash=True)

            try:
                if price_store is not None and context.ticker:
                    price_store.append_ticker_snapshot(context.ticker, context.server_time_ms)

                managed = strategy.get_managed_pairs()
                if managed:
                    stale_cancelled = executor.cancel_stale_orders(managed, context.server_time_ms)
                    if stale_cancelled:
                        logger.info("stale_order_cleanup cancelled=%s", stale_cancelled)

                build_ms = (time.perf_counter() - tick_start) * 1000

                signals = strategy.next(context)
                if max_orders_per_cycle is not None and len(signals) > 0:
                    cap = int(max_orders_per_cycle) if max_orders_per_cycle else 0
                    if cap > 0 and len(signals) > cap:
                        signals = signals[:cap]
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

                consecutive_db_errors = 0
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                logger.exception("price store or tick DB error: %s", e)
                consecutive_db_errors += 1
                if consecutive_db_errors >= max_db_errors:
                    logger.critical("KILL SWITCH: halting after DB failures")
                    sys.exit(1)
                time.sleep(settings.tick_seconds)
                tick_index += 1
                continue

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
