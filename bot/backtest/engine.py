"""Backtest engine: load data, run strategy day-by-day, simulate fills, record equity and trades."""

from __future__ import annotations

import copy
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.base import FeeSchedule, PlaceOrderSignal, TradingContext
from bot.ohlcv import BinanceHistoricalFileProvider, OHLCVCandle, discover_tradeable_pairs
from bot.price_store import PriceStore
from bot.strategies.utils import get_balance_free, get_price, parse_pair, tradeable_pairs

logger = logging.getLogger(__name__)

# Warmup: need enough days for regime (ma_window+2) and momentum (8)
MIN_DAYS_FOR_MOMENTUM = 8


@dataclass(frozen=True)
class BacktestResult:
    """Result of a backtest run."""

    equity_curve: list[tuple[int, float]]  # (timestamp_ms, equity_usd)
    trades: list[dict[str, Any]]
    end_portfolio: list[dict[str, Any]]  # [{"asset", "quantity", "value_usd"}, ...]


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
    fees: FeeSchedule | None = None,
) -> None:
    """Apply a fill at given price; update balance and append to trades. Deducts trading fees when provided."""
    if price <= 0:
        return
    base, quote = parse_pair(signal.pair)
    fee_rate = fees.rate_for(signal.order_type) if fees else 0.0
    notional = signal.quantity * price

    if signal.side == "BUY":
        free_usd = get_balance_free(balance, "USD") + get_balance_free(balance, "USDT")
        spend = min(notional, free_usd)
        if spend <= 0:
            return
        fee = spend * fee_rate
        qty = (spend - fee) / price
        balance.setdefault("USD", {"Free": 0.0, "Lock": 0.0})["Free"] = (
            get_balance_free(balance, "USD") - spend
        )
        balance.setdefault(base, {"Free": 0.0, "Lock": 0.0})["Free"] = (
            get_balance_free(balance, base) + qty
        )
        cb_cost, cb_qty = cost_basis.get(base, (0.0, 0.0))
        cost_basis[base] = (cb_cost + spend, cb_qty + qty)
        trades.append({"ts_ms": ts_ms, "pair": signal.pair, "side": "BUY", "quantity": qty, "price": price, "notional_usd": spend, "fee": fee})
        logger.info("fill BUY %s qty=%.6g @ %.2f notional=%.2f fee=%.4f USD", signal.pair, qty, price, spend, fee)
    else:
        free_base = get_balance_free(balance, base)
        sell_qty = min(signal.quantity, free_base)
        if sell_qty <= 0:
            return
        gross_proceeds = sell_qty * price
        fee = gross_proceeds * fee_rate
        proceeds = gross_proceeds - fee
        balance.setdefault(base, {"Free": 0.0, "Lock": 0.0})["Free"] = get_balance_free(balance, base) - sell_qty
        balance.setdefault("USD", {"Free": 0.0, "Lock": 0.0})["Free"] = get_balance_free(balance, "USD") + proceeds
        cb_cost, cb_qty = cost_basis.get(base, (0.0, 0.0))
        pnl = None
        if cb_qty > 0:
            cost_alloc = cb_cost * (sell_qty / cb_qty)
            pnl = proceeds - cost_alloc
            cost_basis[base] = (cb_cost - cost_alloc, cb_qty - sell_qty)
        rec: dict[str, Any] = {"ts_ms": ts_ms, "pair": signal.pair, "side": "SELL", "quantity": sell_qty, "price": price, "notional_usd": proceeds, "fee": fee}
        if pnl is not None:
            rec["pnl"] = pnl
        trades.append(rec)
        logger.info("fill SELL %s qty=%.6g @ %.2f notional=%.2f fee=%.4f USD pnl=%s", signal.pair, sell_qty, price, proceeds, fee, pnl if pnl is not None else "n/a")


def run_backtest(
    data_dir: str,
    strategy_name: str,
    strategy_params: dict[str, Any],
    *,
    start_date_ms: int | None = None,
    end_date_ms: int | None = None,
    initial_balance_usd: float = 10_000.0,
) -> tuple[list[tuple[int, float]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run backtest; returns BacktestResult(equity_curve, trades, end_portfolio)."""
    from bot.strategies import STRATEGIES

    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy_name}; available: {list(STRATEGIES.keys())}")

    provider = BinanceHistoricalFileProvider(data_dir)
    pairs = discover_tradeable_pairs(data_dir)
    if not pairs:
        raise ValueError(f"no tradeable pairs found under {data_dir}")

    exclude = strategy_params.get("exclude_pairs")
    if exclude:
        synthetic = {"TradePairs": {p: {"CanTrade": True} for p in pairs}}
        pairs = tradeable_pairs(synthetic, exclude=exclude)
        if not pairs:
            raise ValueError(
                "after exclude_pairs filter, no pairs remain; check config strategy.exclude_pairs or BOT_EXCLUDE_PAIRS"
            )
        logger.info("backtest pairs after exclude_pairs: count=%s", len(pairs))

    exchange_info: dict[str, Any] = {"TradePairs": {p: {"CanTrade": True}} for p in pairs}

    # Load full daily series per pair
    series: dict[str, list[OHLCVCandle]] = {}
    had_candles_before_date_filter = False
    for pair in pairs:
        candles = provider.get_daily_klines_range(pair, end_time_ms=end_date_ms)
        if not candles:
            continue
        had_candles_before_date_filter = True
        if start_date_ms is not None:
            candles = [c for c in candles if c["time"] >= start_date_ms]
        candles.sort(key=lambda c: c["time"])
        if candles:
            series[pair] = candles

    if not series:
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

        fees = FeeSchedule(
            market_rate=float(strategy_params.get("fee_market_rate", 0.001)),
            limit_rate=float(strategy_params.get("fee_limit_rate", 0.0005)),
        )

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
                    _apply_fill(balance, sig, price, day_ts, trades, cost_basis, fees)

            price_store.append_ticker_snapshot(ticker, day_ts)

            eq = _portfolio_value(balance, ticker, pairs)
            equity_curve.append((day_ts, eq))

        strategy.on_stop()

        # Build end-of-period portfolio breakdown (asset -> quantity, value_usd)
        end_portfolio: list[dict[str, Any]] = []
        for asset in sorted(balance.keys()):
            qty = get_balance_free(balance, asset)
            if qty <= 0:
                continue
            if asset in ("USD", "USDT"):
                value_usd = qty
            else:
                pair = f"{asset}/USD"
                price = get_price(ticker, pair) if pair in ticker else 0.0
                value_usd = qty * price if price > 0 else 0.0
            end_portfolio.append({"asset": asset, "quantity": qty, "value_usd": value_usd})

        if equity_curve and initial_balance_usd > 0:
            equity_curve.insert(0, (timeline[warmup], initial_balance_usd))
        elif not equity_curve and initial_balance_usd > 0 and len(timeline) > warmup:
            equity_curve = [(timeline[warmup], initial_balance_usd)]
        return BacktestResult(equity_curve, trades, end_portfolio)
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
