"""Tests for bot/base: TradingContext, signals, Strategy contract."""

import pytest

from bot.base import (
    CancelOrderSignal,
    PlaceOrderSignal,
    Strategy,
    TradingContext,
)


def test_place_order_signal_normalize_pair_in_executor() -> None:
    sig = PlaceOrderSignal("BTC/USD", "BUY", 0.01, "MARKET", None)
    assert sig.pair == "BTC/USD"
    assert sig.side == "BUY"
    assert sig.quantity == 0.01
    assert sig.order_type == "MARKET"
    assert sig.price is None


def test_cancel_order_signal() -> None:
    sig = CancelOrderSignal(order_id="123")
    assert sig.order_id == "123"
    assert sig.pair is None
    sig2 = CancelOrderSignal(pair="BTC/USD")
    assert sig2.pair == "BTC/USD"
    assert sig2.order_id is None


def test_trading_context_immutable() -> None:
    ctx = TradingContext(
        server_time_ms=1000,
        ticker={"BTC/USD": {"LastPrice": 50000}},
        balance={"USD": {"Free": 1000, "Lock": 0}},
        pending_orders=[],
    )
    assert ctx.server_time_ms == 1000
    with pytest.raises(Exception):  # frozen dataclass
        ctx.server_time_ms = 2000  # type: ignore[misc]


def test_example_strategy_next_returns_signals() -> None:
    from bot.strategies.example import ExampleStrategy

    strat = ExampleStrategy({"pair": "BTC/USD", "size": 0.001, "every_n_ticks": 1})
    ctx = TradingContext(
        server_time_ms=0,
        ticker={"BTC/USD": {"LastPrice": 50000.0}},
        balance={},
        pending_orders=[],
    )
    signals = strat.next(ctx)
    assert len(signals) == 1
    assert isinstance(signals[0], PlaceOrderSignal)
    assert signals[0].side == "BUY"
    assert signals[0].pair == "BTC/USD"
    assert strat.get_managed_pairs() == ["BTC/USD"]
