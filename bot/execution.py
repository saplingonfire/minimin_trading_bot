"""Translate signals to orders: validation, precision, risk guards, retries."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal

from roostoo.client import RoostooClient
from roostoo.exceptions import RoostooAPIError

from bot.base import CancelOrderSignal, PlaceOrderSignal, Signal

logger = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503)
RETRY_DELAYS = (1.0, 2.0, 4.0)
MAX_RETRIES = 3


def _normalize_pair(pair: str) -> str:
    if "/" in pair:
        return pair
    return f"{pair}/USD"


def _get_pair_info(exchange_info: dict[str, Any] | None, pair: str) -> dict[str, Any] | None:
    if not exchange_info:
        return None
    pairs = exchange_info.get("TradePairs") or exchange_info.get("trade_pairs") or {}
    return pairs.get(pair) or pairs.get(pair.replace("/", "_"))


def _round_quantity(qty: float, pair_info: dict[str, Any] | None) -> float:
    if not pair_info:
        return qty
    prec = pair_info.get("AmountPrecision")
    if prec is None:
        prec = pair_info.get("amount_precision")
    if prec is None:
        return qty
    p = int(prec)
    if p == 0:
        return float(int(qty))
    return round(qty, p)


def _round_price(price: float, pair_info: dict[str, Any] | None) -> float:
    if not pair_info:
        return price
    prec = pair_info.get("PricePrecision")
    if prec is None:
        prec = pair_info.get("price_precision")
    if prec is None:
        return price
    p = int(prec)
    if p == 0:
        return float(int(price))
    return round(price, p)


def _check_mini_order(qty: float, pair_info: dict[str, Any] | None) -> bool:
    if not pair_info:
        return True
    mini = pair_info.get("MiniOrder")
    if mini is None:
        mini = pair_info.get("mini_order")
    if mini is None:
        return True
    return qty >= float(mini)


class Executor:
    """Execute signals via RoostooClient with dry-run, precision, and risk guards."""

    def __init__(
        self,
        client: RoostooClient,
        dry_run: bool = False,
        exchange_info: dict[str, Any] | None = None,
        max_pending_orders: int | None = None,
        max_order_notional: float | None = None,
        order_spacing_sec: float | None = None,
        trades_log_path: str | None = None,
        stale_order_timeout_sec: float | None = None,
    ) -> None:
        self._client = client
        self._dry_run = dry_run
        self._exchange_info = exchange_info
        self._max_pending_orders = max_pending_orders
        self._max_order_notional = max_order_notional
        self._order_spacing_sec = order_spacing_sec
        self._trades_log_path = trades_log_path
        self._stale_order_timeout_sec = stale_order_timeout_sec

    def _append_trade(self, record: dict[str, Any]) -> None:
        """Append one JSONL line to trades_log_path when set."""
        if not self._trades_log_path:
            return
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        try:
            with open(self._trades_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
        except OSError:
            pass

    def execute(
        self,
        signals: list[Signal],
        context_ticker: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute signals in order. Returns list of API responses or error stubs. Never logs secrets."""
        results: list[dict[str, Any]] = []
        ticker = context_ticker or {}
        spacing = self._order_spacing_sec
        place_count = 0

        for sig in signals:
            if isinstance(sig, CancelOrderSignal):
                out = self._execute_cancel(sig)
                results.append(out)
                continue
            if isinstance(sig, PlaceOrderSignal):
                if spacing is not None and spacing > 0 and place_count >= 1 and not self._dry_run:
                    time.sleep(spacing)
                out = self._execute_place(sig, ticker, results)
                results.append(out)
                if "error" not in out:
                    place_count += 1
                continue
            results.append({"error": "unknown_signal_type"})

        return results

    def _execute_cancel(self, sig: CancelOrderSignal) -> dict[str, Any]:
        pair = _normalize_pair(sig.pair) if sig.pair else (sig.pair or "")
        if self._dry_run:
            logger.info(
                "cancel_result order_id=%s pair=%s success=true dry_run=true",
                sig.order_id, sig.pair,
            )
            self._append_trade({
                "action": "cancel", "pair": pair, "order_id": sig.order_id,
                "success": True, "dry_run": True,
            })
            return {"dry_run": True, "cancel": True}

        def _do() -> dict[str, Any]:
            return self._client.cancel_order(order_id=sig.order_id, pair=sig.pair)

        out = self._request_with_retry(_do, "cancel")
        success = "error" not in out
        err = out.get("error", "") if not success else ""
        self._append_trade({
            "action": "cancel", "pair": pair, "order_id": sig.order_id,
            "success": success, "error": err or None, "dry_run": False,
        })
        logger.info(
            "cancel_result order_id=%s pair=%s success=%s%s",
            sig.order_id, sig.pair, success, f" error={err}" if err else "",
        )
        return out

    def _execute_place(
        self,
        sig: PlaceOrderSignal,
        ticker: dict[str, Any],
        previous_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pair = _normalize_pair(sig.pair)
        pair_info = _get_pair_info(self._exchange_info, pair)

        if sig.order_type == "LIMIT" and sig.price is None:
            logger.warning("skip LIMIT order without price pair=%s", pair)
            return {"error": "limit_order_requires_price"}

        qty = _round_quantity(sig.quantity, pair_info)
        price: float | None = None
        if sig.order_type == "LIMIT" and sig.price is not None:
            price = _round_price(sig.price, pair_info)

        if not _check_mini_order(qty, pair_info):
            logger.warning("skip order below min size pair=%s qty=%s", pair, qty)
            return {"error": "below_min_order", "pair": pair}

        if self._max_pending_orders is not None:
            try:
                pc = self._client.get_pending_count()
                count = int(pc.get("Count") or pc.get("count") or 0)
                if count >= self._max_pending_orders:
                    logger.warning(
                        "skip place: max_pending_orders reached current=%s max=%s",
                        count,
                        self._max_pending_orders,
                    )
                    return {"error": "max_pending_orders", "current": count}
            except RoostooAPIError as e:
                logger.warning("pending_count check failed: %s", e.message)
                return {"error": "pending_count_failed", "message": e.message}

        if self._max_order_notional is not None and self._max_order_notional > 0:
            effective_price = price
            if effective_price is None:
                pair_ticker = ticker.get(pair) or ticker
                if isinstance(pair_ticker, dict):
                    effective_price = (
                        pair_ticker.get("LastPrice")
                        or pair_ticker.get("lastPrice")
                        or pair_ticker.get("MinAsk")
                        or 0
                    )
                else:
                    effective_price = 0
            if effective_price and qty * effective_price > self._max_order_notional:
                logger.warning(
                    "skip order: notional %.2f exceeds max %.2f pair=%s",
                    qty * effective_price,
                    self._max_order_notional,
                    pair,
                )
                return {
                    "error": "max_order_notional_exceeded",
                    "notional": qty * effective_price,
                    "max": self._max_order_notional,
                }

        if self._dry_run:
            logger.info(
                "order_result pair=%s side=%s qty=%s success=true dry_run=true",
                pair, sig.side, qty,
            )
            self._append_trade({
                "action": "place", "pair": pair, "side": sig.side, "qty": qty,
                "order_type": sig.order_type, "success": True, "dry_run": True,
            })
            return {"dry_run": True, "place": True}

        def _do() -> dict[str, Any]:
            return self._client.place_order(
                pair=pair,
                side=sig.side,
                quantity=qty,
                order_type=sig.order_type,
                price=price,
            )

        out = self._request_with_retry(_do, "place_order")
        success = "error" not in out
        err = out.get("error", "") if not success else ""
        err_msg = out.get("message", "") if not success else ""
        order_id = out.get("OrderID") or out.get("order_id") if success else None
        self._append_trade({
            "action": "place", "pair": pair, "side": sig.side, "qty": qty,
            "order_type": sig.order_type, "order_id": order_id,
            "success": success, "error": err or None, "dry_run": False,
        })
        logger.info(
            "order_result pair=%s side=%s qty=%s success=%s%s",
            pair, sig.side, qty, success,
            f" error={err} message={err_msg}" if err else "",
        )

        return out

    def _request_with_retry(
        self,
        fn: object,
        action: str,
    ) -> dict[str, Any]:
        assert callable(fn)
        last_err: Exception | None = None
        for i, delay in enumerate(RETRY_DELAYS):
            try:
                return fn()
            except RoostooAPIError as e:
                last_err = e
                status = e.status_code
                if status in RETRY_STATUSES and i < MAX_RETRIES - 1:
                    logger.warning(
                        "retry %s after %s status=%s message=%s",
                        action,
                        delay,
                        status,
                        e.message,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "%s failed status=%s message=%s",
                        action,
                        status,
                        e.message,
                    )
                    return {"error": "api_error", "message": e.message, "status_code": status}
            except Exception as e:
                last_err = e
                logger.exception("%s failed", action)
                return {"error": "exception", "message": str(e)}
        if last_err:
            return {"error": "api_error", "message": str(last_err)}
        return {"error": "unknown"}

    def cancel_stale_orders(self, pairs: list[str], server_time_ms: int) -> int:
        """Cancel pending limit orders older than stale_order_timeout_sec. Returns count cancelled."""
        if self._stale_order_timeout_sec is None or self._stale_order_timeout_sec <= 0:
            return 0
        if self._dry_run:
            return 0
        timeout_ms = self._stale_order_timeout_sec * 1000
        cancelled = 0
        for pair in pairs:
            p = _normalize_pair(pair)
            try:
                result = self._client.query_order(pair=p, pending_only=True)
            except RoostooAPIError as e:
                logger.debug("stale_order query pair=%s failed: %s", p, e.message)
                continue
            orders = result.get("OrderMatched") or result.get("order_matched") or []
            for order in orders:
                oid = order.get("OrderID") or order.get("order_id")
                if not oid:
                    continue
                create_ts = (
                    order.get("CreateTimestamp")
                    or order.get("createTimestamp")
                    or order.get("CreateTime")
                    or order.get("createTime")
                )
                if create_ts is None:
                    continue
                age_ms = server_time_ms - int(create_ts)
                if age_ms < timeout_ms:
                    continue
                try:
                    self._client.cancel_order(order_id=str(oid))
                    cancelled += 1
                    logger.info(
                        "stale_order_cancel pair=%s order_id=%s age_sec=%.0f",
                        p, oid, age_ms / 1000,
                    )
                    self._append_trade({
                        "action": "cancel_stale", "pair": p, "order_id": str(oid),
                        "age_sec": round(age_ms / 1000), "success": True, "dry_run": False,
                    })
                except RoostooAPIError as e:
                    logger.warning(
                        "stale_order_cancel failed pair=%s order_id=%s: %s", p, oid, e.message,
                    )
        return cancelled

    def cancel_orders_for_pairs(self, pairs: list[str]) -> list[dict[str, Any]]:
        """Cancel all pending orders for the given pairs. Used on shutdown."""
        results: list[dict[str, Any]] = []
        for pair in pairs:
            p = _normalize_pair(pair)
            if self._dry_run:
                logger.info("dry_run cancel all pair=%s", p)
                self._append_trade({
                    "action": "cancel", "pair": p, "order_id": None,
                    "success": True, "dry_run": True,
                })
                results.append({"dry_run": True, "pair": p})
                continue
            try:
                out = self._client.cancel_order(pair=p)
                results.append(out)
                self._append_trade({
                    "action": "cancel", "pair": p, "order_id": None,
                    "success": True, "dry_run": False,
                })
            except RoostooAPIError as e:
                logger.warning("cancel pair=%s failed: %s", p, e.message)
                self._append_trade({
                    "action": "cancel", "pair": p, "order_id": None,
                    "success": False, "error": e.message, "dry_run": False,
                })
                results.append({"error": e.message, "pair": p})
        return results
