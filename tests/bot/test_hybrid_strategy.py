"""Tests for hybrid_trend_cross_sectional strategy."""

import tempfile
from pathlib import Path

import pytest

from bot.base import TradingContext
from bot.price_store import MS_PER_DAY, PriceStore
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
    with tempfile.TemporaryDirectory() as tmp:
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
    """Smoke test: strategy with minimal store and one tradeable pair computes without error."""
    with tempfile.TemporaryDirectory() as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        # 8+ days for BTC and one other so we have momentum
        base = 1000 * MS_PER_DAY
        for i in range(10):
            store.insert_daily_rows([
                ("BTC/USD", base + i * MS_PER_DAY, 40000.0 + i * 500),
                ("ETH/USD", base + i * MS_PER_DAY, 2000.0 + i * 20),
            ])
        strat = HybridTrendCrossSectionalStrategy({"N": 5, "min_days_history": 3})
        ctx = TradingContext(
            server_time_ms=base + 9 * MS_PER_DAY,
            ticker={
                "BTC/USD": {"LastPrice": 45000, "UnitTradeValue": 1e9, "Change": 0.01},
                "ETH/USD": {"LastPrice": 2200, "UnitTradeValue": 5e8, "Change": 0.02},
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
        # May return 0 or more signals depending on regime and ranks
        assert isinstance(signals, list)
        for s in signals:
            assert hasattr(s, "pair") and hasattr(s, "side")
