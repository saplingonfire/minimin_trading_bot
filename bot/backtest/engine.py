"""Backtest engine: load data, run strategy day-by-day, simulate fills, record equity and trades."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from bot.base import PlaceOrderSignal, TradingContext
from bot.ohlcv import BinanceHistoricalFileProvider, OHLCVCandle, discover_tradeable_pairs, roostoo_pair_to_binance_ticker
from bot.price_store import PriceStore
from bot.strategies.utils import get_balance_free, get_price, parse_pair

logger = logging.getLogger(__name__)

# #region agent log
_DEBUG_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "debug-760b30.log")
def _debug_log(message: str, data: dict[str, Any], hypothesis_id: str = "no_daily_data") -> None:
    try:
        payload = {"sessionId": "760b30", "hypothesisId": hypothesis_id, "location": "engine.py", "message": message, "data": data, "timestamp": __import__("time").time() * 1000}
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
# #endregion

# Warmup: need enough days for regime (ma_window+2) and momentum (8)
MIN_DAYS_FOR_MOMENTUM = 8


def _default_warmup_days(strategy_params: dict[str, Any]) -> int:
    ma_window = int(strategy_params.get("ma_window", 20))
    return max(ma_window + 2, MIN_DAYS_FOR_MOMENTUM)


def _balance_deep_copy(balance: dict[str, Any]) -> dict[str, Any]:
    """Deep copy balance so we can mutate in backtest."""
    return copy.deepcopy(balance)


def _initial_balance(initial_balance_usd: float) -> dict[str, Any]:
    return {"USD": {"Free": initial_balance_usd, "Lock": 0.0}}


def _portfolio_value(balance: dict[str, Any], ticker: dict[str, Any], pairs: list[str]) -> float:
    pv = get_balance_free(balance, "USD") + get_balance_free(balance, "USDT")
    for pair in pairs:
        base, _ = parse_pair(pair)
        qty = get_balance_free(balance, base)
        price = get_price(ticker, pair)
        if price > 0:
            pv += qty * price
    return pv


def _apply_fill(
    balance: dict[str, Any],
    signal: PlaceOrderSignal,
    price: float,
    ts_ms: int,
    trades: list[dict[str, Any]],
    cost_basis: dict[str, tuple[float, float]],
) -> None:
    """Apply a fill at given price; update balance and append to trades. Optionally compute pnl for sells."""
    if price <= 0:
        return
    base, quote = parse_pair(signal.pair)
    notional = signal.quantity * price

    if signal.side == "BUY":
        free_usd = get_balance_free(balance, "USD") + get_balance_free(balance, "USDT")
        spend = min(notional, free_usd)
        if spend <= 0:
            return
        qty = spend / price
        balance.setdefault("USD", {"Free": 0.0, "Lock": 0.0})["Free"] = (
            get_balance_free(balance, "USD") - spend
        )
        balance.setdefault(base, {"Free": 0.0, "Lock": 0.0})["Free"] = (
            get_balance_free(balance, base) + qty
        )
        # Cost basis: (total_cost, total_qty)
        cb_cost, cb_qty = cost_basis.get(base, (0.0, 0.0))
        cost_basis[base] = (cb_cost + spend, cb_qty + qty)
        trades.append({"ts_ms": ts_ms, "pair": signal.pair, "side": "BUY", "quantity": qty, "price": price, "notional_usd": spend})
    else:
        free_base = get_balance_free(balance, base)
        sell_qty = min(signal.quantity, free_base)
        if sell_qty <= 0:
            return
        proceeds = sell_qty * price
        balance.setdefault(base, {"Free": 0.0, "Lock": 0.0})["Free"] = get_balance_free(balance, base) - sell_qty
        balance.setdefault("USD", {"Free": 0.0, "Lock": 0.0})["Free"] = get_balance_free(balance, "USD") + proceeds
        cb_cost, cb_qty = cost_basis.get(base, (0.0, 0.0))
        pnl = None
        if cb_qty > 0:
            cost_alloc = cb_cost * (sell_qty / cb_qty)
            pnl = proceeds - cost_alloc
            cost_basis[base] = (cb_cost - cost_alloc, cb_qty - sell_qty)
        rec: dict[str, Any] = {"ts_ms": ts_ms, "pair": signal.pair, "side": "SELL", "quantity": sell_qty, "price": price, "notional_usd": proceeds}
        if pnl is not None:
            rec["pnl"] = pnl
        trades.append(rec)


def run_backtest(
    data_dir: str,
    strategy_name: str,
    strategy_params: dict[str, Any],
    *,
    start_date_ms: int | None = None,
    end_date_ms: int | None = None,
    initial_balance_usd: float = 10_000.0,
) -> tuple[list[tuple[int, float]], list[dict[str, Any]]]:
    """Run backtest; return (equity_curve, trades). equity_curve = [(ts_ms, equity)], trades = list of dicts."""
    from bot.strategies import STRATEGIES

    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy_name}; available: {list(STRATEGIES.keys())}")

    # #region agent log
    data_dir_resolved = str(Path(data_dir).resolve())
    data_dir_exists = Path(data_dir).exists() and Path(data_dir).is_dir()
    _debug_log("run_backtest data_dir", {"data_dir": data_dir, "data_dir_resolved": data_dir_resolved, "exists": data_dir_exists, "start_date_ms": start_date_ms, "end_date_ms": end_date_ms}, "H1")
    # #endregion
    provider = BinanceHistoricalFileProvider(data_dir)
    pairs = discover_tradeable_pairs(data_dir)
    # #region agent log
    _debug_log("discover_tradeable_pairs result", {"pairs_count": len(pairs), "pairs_sample": pairs[:10]}, "H2")
    if pairs:
        ticker = roostoo_pair_to_binance_ticker(pairs[0])
        path1 = Path(data_dir).joinpath("data", "spot", "daily", "klines", ticker, "1h")
        path2 = Path(data_dir).joinpath("spot", "daily", "klines", ticker, "1h")
        _debug_log("expected 1h path check", {"path1": str(path1), "path1_exists": path1.exists(), "path2": str(path2), "path2_exists": path2.exists()}, "H2b")
    # #endregion
    if not pairs:
        raise ValueError(f"no tradeable pairs found under {data_dir}")

    exchange_info: dict[str, Any] = {"TradePairs": {p: {"CanTrade": True}} for p in pairs}

    # Load full daily series per pair
    series: dict[str, list[OHLCVCandle]] = {}
    had_candles_before_date_filter = False
    for pair in pairs:
        candles = provider.get_daily_klines_range(pair, end_time_ms=end_date_ms)
        # #region agent log
        before_filter = len(candles)
        # #endregion
        if not candles:
            # #region agent log
            _debug_log("get_daily_klines_range returned empty", {"pair": pair}, "H3")
            # #endregion
            continue
        had_candles_before_date_filter = True
        if start_date_ms is not None:
            candles = [c for c in candles if c["time"] >= start_date_ms]
        candles.sort(key=lambda c: c["time"])
        # #region agent log
        after_filter = len(candles)
        _debug_log("per-pair load", {"pair": pair, "before_date_filter": before_filter, "after_date_filter": after_filter, "added_to_series": bool(candles)}, "H4")
        # #endregion
        if candles:
            series[pair] = candles

    if not series:
        # #region agent log
        _debug_log("no series loaded", {"pairs_tried": len(pairs), "series_count": 0}, "H5")
        # #endregion
        if had_candles_before_date_filter:
            raise ValueError(
                "no daily data falls within the requested date range (--start-date / --end-date). "
                "Try omitting both to use all available data, or ensure your data overlaps the range."
            )
        raise ValueError("no daily data loaded for any pair")

    # Timeline: use first available pair's timestamps (e.g. BTC)
    timeline_pair = next(iter(series))
    timeline = [c["time"] for c in series[timeline_pair]]
    if start_date_ms is not None:
        timeline = [t for t in timeline if t >= start_date_ms]
    if end_date_ms is not None:
        timeline = [t for t in timeline if t <= end_date_ms]
    if not timeline:
        raise ValueError("No trading days in the selected date range.")

    # Per-pair: time -> close, time -> volume
    close_at: dict[str, dict[int, float]] = {}
    volume_at: dict[str, dict[int, float]] = {}
    for p, candles in series.items():
        close_at[p] = {c["time"]: c["close"] for c in candles}
        volume_at[p] = {c["time"]: c["volume"] for c in candles}

    warmup = _default_warmup_days(strategy_params)
    if len(timeline) <= warmup:
        raise ValueError(
            f"Insufficient data: backtest needs at least {warmup + 1} days (warmup={warmup} for regime/momentum), "
            f"but only {len(timeline)} day(s) available. Sync more history with scripts/sync_binance_historical.py."
        )

    # Price store: use temp file so SQLite schema is shared across connections (:memory: would not be)
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    db_path = tmp_db.name
    try:
        price_store = PriceStore(db_path)
    except Exception:
        os.unlink(db_path)
        raise
    try:
        for i in range(warmup):
            day_ts = timeline[i]
            ticker_snap: dict[str, Any] = {}
            for p in pairs:
                if day_ts in close_at.get(p, {}):
                    ticker_snap[p] = {
                        "LastPrice": close_at[p][day_ts],
                        "UnitTradeValue": volume_at.get(p, {}).get(day_ts, 0),
                        "Change": 0.0,
                    }
            if ticker_snap:
                price_store.append_ticker_snapshot(ticker_snap, day_ts)

        strategy_cls = STRATEGIES[strategy_name]
        strategy = strategy_cls(strategy_params)
        strategy.on_start()

        balance = _initial_balance(initial_balance_usd)
        cost_basis: dict[str, tuple[float, float]] = {}
        equity_curve: list[tuple[int, float]] = []
        trades: list[dict[str, Any]] = []

        for i in range(warmup, len(timeline)):
            day_ts = timeline[i]
            ticker = {}
            for p in pairs:
                if day_ts not in close_at.get(p, {}):
                    continue
                prev_closes = price_store.get_daily_closes(p, 2)
                prev = prev_closes[-1] if len(prev_closes) >= 2 else None
                curr = close_at[p][day_ts]
                change = (curr - prev) / prev if prev and prev > 0 else 0.0
                ticker[p] = {
                    "LastPrice": curr,
                    "UnitTradeValue": volume_at.get(p, {}).get(day_ts, 0),
                    "Change": change,
                }

            context = TradingContext(
                server_time_ms=day_ts,
                ticker=ticker,
                balance=_balance_deep_copy(balance),
                pending_orders=[],
                exchange_info=exchange_info,
                ohlcv_provider=None,
                price_store=price_store,
                risk_force_cash=False,
            )

            signals = strategy.next(context)

            for sig in signals:
                if isinstance(sig, PlaceOrderSignal):
                    price = get_price(ticker, sig.pair)
                    _apply_fill(balance, sig, price, day_ts, trades, cost_basis)

            price_store.append_ticker_snapshot(ticker, day_ts)

            eq = _portfolio_value(balance, ticker, pairs)
            equity_curve.append((day_ts, eq))

        strategy.on_stop()

        if equity_curve and initial_balance_usd > 0:
            equity_curve.insert(0, (timeline[warmup], initial_balance_usd))
        elif not equity_curve and initial_balance_usd > 0 and len(timeline) > warmup:
            equity_curve = [(timeline[warmup], initial_balance_usd)]
        return equity_curve, trades
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
