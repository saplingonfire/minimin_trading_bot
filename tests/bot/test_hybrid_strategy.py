"""Tests for hybrid_trend_cross_sectional strategy."""

import tempfile
from pathlib import Path

import pytest

from bot.base import PlaceOrderSignal, TradingContext
from bot.price_store import MS_PER_DAY, MS_PER_HOUR, PriceStore
from bot.strategies.hybrid_trend_cross_sectional import HybridTrendCrossSectionalStrategy


def test_hybrid_returns_empty_without_price_store() -> None:
    strat = HybridTrendCrossSectionalStrategy({"N": 5})
    ctx = TradingContext(
        server_time_ms=0,
        ticker={"BTC/USD": {"LastPrice": 50000}},
        balance={"USD": {"Free": 10000, "Lock": 0}},
        pending_orders=[],
        exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
        price_store=None,
    )
    signals = strat.next(ctx)
    assert signals == []


def test_hybrid_returns_empty_when_risk_force_cash() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        for i in range(25):
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, 40000.0 + i * 100)])
        strat = HybridTrendCrossSectionalStrategy({"N": 5})
        ctx = TradingContext(
            server_time_ms=(1025 * MS_PER_DAY),
            ticker={"BTC/USD": {"LastPrice": 50000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
            risk_force_cash=True,
        )
        signals = strat.next(ctx)
        assert signals == []
        assert strat.get_managed_pairs() is None or strat.get_managed_pairs() == []


def test_hybrid_momentum_score_ordering() -> None:
    """Smoke test: strategy with hourly store data and one tradeable pair computes without error."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        base = 1000 * MS_PER_DAY
        for i in range(30):
            store.insert_daily_rows([
                ("BTC/USD", base + i * MS_PER_HOUR, 40000.0 + i * 50),
                ("ETH/USD", base + i * MS_PER_HOUR, 2000.0 + i * 5),
            ])
        strat = HybridTrendCrossSectionalStrategy({"N": 5, "min_days_history": 1})
        ctx = TradingContext(
            server_time_ms=base + 29 * MS_PER_HOUR,
            ticker={
                "BTC/USD": {"LastPrice": 41500, "UnitTradeValue": 1e9, "Change": 0.01},
                "ETH/USD": {"LastPrice": 2150, "UnitTradeValue": 5e8, "Change": 0.02},
            },
            balance={"USD": {"Free": 50000, "Lock": 0}},
            pending_orders=[],
            exchange_info={
                "TradePairs": {
                    "BTC/USD": {"CanTrade": True},
                    "ETH/USD": {"CanTrade": True},
                }
            },
            price_store=store,
        )
        signals = strat.next(ctx)
        assert isinstance(signals, list)
        for s in signals:
            assert hasattr(s, "pair") and hasattr(s, "side")


def test_hybrid_sells_stale_positions_on_rerank() -> None:
    """When a held asset drops out of _target_weights, a SELL signal is generated."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        base_ts = 1000 * MS_PER_DAY
        for i in range(10):
            store.insert_daily_rows([
                ("BTC/USD", base_ts + i * MS_PER_DAY, 40000.0 + i * 500),
                ("ETH/USD", base_ts + i * MS_PER_DAY, 2000.0 + i * 20),
                ("SOL/USD", base_ts + i * MS_PER_DAY, 100.0 + i * 5),
            ])

        strat = HybridTrendCrossSectionalStrategy({
            "N": 2,
            "min_days_history": 3,
            "min_trade_usd": 10.0,
            "min_volume_usd": 0,
            "pair_cooldown_min": 0,
        })
        strat.on_start()

        # Simulate: strategy currently targets SOL only, but balance holds ETH
        strat._target_weights = {"SOL/USD": 0.40}

        now = base_ts + 9 * MS_PER_DAY
        ctx = TradingContext(
            server_time_ms=now,
            ticker={
                "BTC/USD": {"LastPrice": 45000, "UnitTradeValue": 1e9, "Change": 0.01},
                "ETH/USD": {"LastPrice": 2200, "UnitTradeValue": 5e8, "Change": 0.02},
                "SOL/USD": {"LastPrice": 150, "UnitTradeValue": 3e8, "Change": 0.03},
            },
            balance={
                "USD": {"Free": 5000, "Lock": 0},
                "ETH": {"Free": 5.0, "Lock": 0},
            },
            pending_orders=[],
            exchange_info={
                "TradePairs": {
                    "BTC/USD": {"CanTrade": True},
                    "ETH/USD": {"CanTrade": True},
                    "SOL/USD": {"CanTrade": True},
                }
            },
            price_store=store,
        )

        tradeable = ["BTC/USD", "ETH/USD", "SOL/USD"]
        stale_signals = strat._sell_stale_positions(ctx, tradeable, now)

        assert len(stale_signals) == 1
        sig = stale_signals[0]
        assert isinstance(sig, PlaceOrderSignal)
        assert sig.pair == "ETH/USD"
        assert sig.side == "SELL"
        assert sig.quantity == 5.0


def test_hybrid_does_not_sell_targeted_positions() -> None:
    """Positions that are still in _target_weights should not be sold as stale."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        base_ts = 1000 * MS_PER_DAY
        for i in range(10):
            store.insert_daily_rows([
                ("BTC/USD", base_ts + i * MS_PER_DAY, 40000.0 + i * 500),
                ("ETH/USD", base_ts + i * MS_PER_DAY, 2000.0 + i * 20),
            ])

        strat = HybridTrendCrossSectionalStrategy({
            "N": 2,
            "min_days_history": 3,
            "min_trade_usd": 10.0,
            "min_volume_usd": 0,
            "pair_cooldown_min": 0,
        })
        strat.on_start()

        strat._target_weights = {"ETH/USD": 0.40}

        now = base_ts + 9 * MS_PER_DAY
        ctx = TradingContext(
            server_time_ms=now,
            ticker={
                "BTC/USD": {"LastPrice": 45000, "UnitTradeValue": 1e9, "Change": 0.01},
                "ETH/USD": {"LastPrice": 2200, "UnitTradeValue": 5e8, "Change": 0.02},
            },
            balance={
                "USD": {"Free": 5000, "Lock": 0},
                "ETH": {"Free": 5.0, "Lock": 0},
            },
            pending_orders=[],
            exchange_info={
                "TradePairs": {
                    "BTC/USD": {"CanTrade": True},
                    "ETH/USD": {"CanTrade": True},
                }
            },
            price_store=store,
        )

        tradeable = ["BTC/USD", "ETH/USD"]
        stale_signals = strat._sell_stale_positions(ctx, tradeable, now)

        assert stale_signals == []
