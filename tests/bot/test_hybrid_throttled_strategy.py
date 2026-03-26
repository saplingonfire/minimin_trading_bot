"""Tests for hybrid_trend_cross_sectional_throttled strategy."""

import tempfile
import uuid
from pathlib import Path

def _cleanup_db(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass  # Windows: SQLite may still have the file open


from bot.base import TradingContext
from bot.price_store import MS_PER_DAY, MS_PER_HOUR, PriceStore
from bot.strategies.hybrid_trend_cross_sectional_throttled import (
    REGIME_RISK_OFF,
    REGIME_RISK_ON_SOFT,
    REGIME_RISK_ON_STRONG,
    HybridTrendCrossSectionalThrottledStrategy,
)


def test_get_target_exposure_prelim() -> None:
    """Prelim mode: strong -> strong_exposure, soft -> soft_exposure, risk_off -> 0."""
    config = {
        "N": 5,
        "regime_filter_enabled": True,
        "regime": {
            "prelim_mode": True,
            "strong_exposure": 0.85,
            "soft_exposure": 0.35,
            "consecutive_below_to_off": 2,
        },
    }
    strat = HybridTrendCrossSectionalThrottledStrategy(config)
    strat.on_start()

    strat._regime = REGIME_RISK_ON_STRONG
    assert strat._get_target_exposure() == 0.85
    strat._regime = REGIME_RISK_ON_SOFT
    assert strat._get_target_exposure() == 0.35
    strat._regime = REGIME_RISK_OFF
    assert strat._get_target_exposure() == 0.0


def test_get_target_exposure_non_prelim() -> None:
    """Non-prelim (Option A): only risk_on_strong gets exposure; soft treated as risk_off."""
    config = {
        "N": 5,
        "regime_filter_enabled": True,
        "regime": {
            "prelim_mode": False,
            "strong_exposure": 0.85,
            "soft_exposure": 0.35,
            "consecutive_below_to_off": 2,
        },
    }
    strat = HybridTrendCrossSectionalThrottledStrategy(config)
    strat.on_start()

    strat._regime = REGIME_RISK_ON_STRONG
    assert strat._get_target_exposure() == 0.85
    strat._regime = REGIME_RISK_ON_SOFT
    assert strat._get_target_exposure() == 0.0
    strat._regime = REGIME_RISK_OFF
    assert strat._get_target_exposure() == 0.0


def test_update_btc_regime_strong() -> None:
    """Last close > MA20 -> risk_on_strong, consecutive_below_ma = 0."""
    db_path = Path(tempfile.gettempdir()) / f"throttled_strong_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        # 20 days at 40k, 2 at 50k -> last close 50k, MA20 of last 20 ~41k
        for i in range(22):
            p = 50000.0 if i >= 20 else 40000.0
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, p)])
        config = {"N": 5, "regime": {"consecutive_below_to_off": 2}}
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        ctx = TradingContext(
            server_time_ms=(1000 + 21) * MS_PER_DAY,
            ticker={"BTC/USD": {"LastPrice": 50000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        strat._update_btc_regime(ctx)
        assert strat._regime == REGIME_RISK_ON_STRONG
        assert strat._consecutive_btc_below_ma == 0
    finally:
        _cleanup_db(db_path)


def test_update_btc_regime_soft() -> None:
    """One close below MA20 -> risk_on_soft, consecutive_below_ma = 1."""
    db_path = Path(tempfile.gettempdir()) / f"throttled_soft_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        # 20 days at 50k, 1 at 40k -> last close 40k, MA20 = 49k
        for i in range(21):
            p = 40000.0 if i == 20 else 50000.0
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, p)])
        config = {"N": 5, "regime": {"consecutive_below_to_off": 2}}
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        ctx = TradingContext(
            server_time_ms=(1000 + 20) * MS_PER_DAY,
            ticker={"BTC/USD": {"LastPrice": 40000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        strat._update_btc_regime(ctx)
        assert strat._regime == REGIME_RISK_ON_SOFT
        assert strat._consecutive_btc_below_ma == 1
    finally:
        _cleanup_db(db_path)


def test_update_btc_regime_risk_off() -> None:
    """Two consecutive daily evaluations below MA20 -> risk_off (consecutive increments per day)."""
    db_path = Path(tempfile.gettempdir()) / f"throttled_off_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        # Day 20: 20 days at 50k, 1 day at 40k -> first eval: below, consecutive=1, soft
        for i in range(21):
            p = 40000.0 if i == 20 else 50000.0
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, p)])
        config = {"N": 5, "regime": {"consecutive_below_to_off": 2}}
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        ctx = TradingContext(
            server_time_ms=(1000 + 20) * MS_PER_DAY,
            ticker={"BTC/USD": {"LastPrice": 40000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        strat._update_btc_regime(ctx)
        assert strat._regime == REGIME_RISK_ON_SOFT
        assert strat._consecutive_btc_below_ma == 1
        # Day 21: add one more day at 40k -> second eval: below, consecutive=2, risk_off
        store.insert_daily_rows([("BTC/USD", (1000 + 21) * MS_PER_DAY, 40000.0)])
        ctx2 = TradingContext(
            server_time_ms=(1000 + 21) * MS_PER_DAY,
            ticker={"BTC/USD": {"LastPrice": 40000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        strat._update_btc_regime(ctx2)
        assert strat._regime == REGIME_RISK_OFF
        assert strat._consecutive_btc_below_ma == 2
    finally:
        _cleanup_db(db_path)


def test_throttled_returns_empty_without_price_store() -> None:
    strat = HybridTrendCrossSectionalThrottledStrategy({"N": 5})
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


def test_throttled_returns_empty_when_risk_force_cash() -> None:
    db_path = Path(tempfile.gettempdir()) / f"throttled_force_cash_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        for i in range(25):
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, 40000.0 + i * 100)])
        strat = HybridTrendCrossSectionalThrottledStrategy({"N": 5})
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
    finally:
        _cleanup_db(db_path)


def test_throttled_smoke_next() -> None:
    """Smoke: throttled strategy with minimal store and two pairs runs without error."""
    db_path = Path(tempfile.gettempdir()) / f"throttled_smoke_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        base = 1000 * MS_PER_DAY
        for i in range(10):
            store.insert_daily_rows([
                ("BTC/USD", base + i * MS_PER_DAY, 40000.0 + i * 500),
                ("ETH/USD", base + i * MS_PER_DAY, 2000.0 + i * 20),
            ])
        strat = HybridTrendCrossSectionalThrottledStrategy({"N": 5, "min_days_history": 3})
        strat.on_start()
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
        assert isinstance(signals, list)
        for s in signals:
            assert hasattr(s, "pair") and hasattr(s, "side")
    finally:
        _cleanup_db(db_path)


# ---------------------------------------------------------------------------
# Sub-daily regime eval
# ---------------------------------------------------------------------------


def test_sub_daily_regime_eval_triggers_at_interval() -> None:
    """With regime_eval_hours=6, regime should re-evaluate every 6h, not daily."""
    db_path = Path(tempfile.gettempdir()) / f"subdaily_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        for i in range(22):
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, 40000.0)])
        config = {
            "N": 5,
            "regime_filter_enabled": True,
            "regime": {"regime_eval_hours": 6, "consecutive_below_to_off": 2},
        }
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()

        base_ms = (1000 + 21) * MS_PER_DAY
        ctx1 = TradingContext(
            server_time_ms=base_ms,
            ticker={"BTC/USD": {"LastPrice": 40000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        assert strat._is_regime_eval_time(base_ms) is True
        strat._compute_regime(ctx1)
        assert strat._is_regime_eval_time(base_ms) is False

        three_hours_later = base_ms + 3 * MS_PER_HOUR
        assert strat._is_regime_eval_time(three_hours_later) is False

        six_hours_later = base_ms + 6 * MS_PER_HOUR
        assert strat._is_regime_eval_time(six_hours_later) is True
    finally:
        _cleanup_db(db_path)


# ---------------------------------------------------------------------------
# Intraday MA (hourly closes in regime eval)
# ---------------------------------------------------------------------------


def test_regime_uses_hourly_closes_when_sub_daily() -> None:
    """When regime_eval_hours < 24, _update_btc_regime uses hourly closes."""
    db_path = Path(tempfile.gettempdir()) / f"hourly_ma_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        base_ms = 1000 * MS_PER_DAY
        for h in range(500):
            store.append_ticker_snapshot(
                {"BTC/USD": {"LastPrice": 40000.0}}, base_ms + h * MS_PER_HOUR,
            )
        store.append_ticker_snapshot(
            {"BTC/USD": {"LastPrice": 50000.0}}, base_ms + 500 * MS_PER_HOUR,
        )
        config = {"N": 5, "regime": {"regime_eval_hours": 6, "ma_window": 20, "consecutive_below_to_off": 2}}
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        ctx = TradingContext(
            server_time_ms=base_ms + 500 * MS_PER_HOUR,
            ticker={"BTC/USD": {"LastPrice": 50000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        strat._update_btc_regime(ctx)
        assert strat._regime == REGIME_RISK_ON_STRONG
    finally:
        _cleanup_db(db_path)


# ---------------------------------------------------------------------------
# Breakout fast-entry
# ---------------------------------------------------------------------------


def test_breakout_fires_when_btc_exceeds_ma20_threshold() -> None:
    """BTC live price > MA20 * 1.02 while risk_off -> risk_on_soft."""
    db_path = Path(tempfile.gettempdir()) / f"breakout_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        for i in range(22):
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, 40000.0)])
        config = {
            "N": 5,
            "regime": {
                "breakout_threshold_pct": 0.02,
                "breakout_exposure": 0.35,
                "breakout_cooldown_min": 60,
                "consecutive_below_to_off": 2,
            },
        }
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        strat._regime = REGIME_RISK_OFF

        ctx = TradingContext(
            server_time_ms=(1000 + 22) * MS_PER_DAY,
            ticker={"BTC/USD": {"LastPrice": 41000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        triggered = strat._check_breakout(ctx)
        assert triggered is True
        assert strat._regime == REGIME_RISK_ON_SOFT
        assert strat._effective_exposure == 0.35
    finally:
        _cleanup_db(db_path)


def test_breakout_does_not_fire_within_threshold() -> None:
    """BTC live price < MA20 * 1.02 -> no breakout."""
    db_path = Path(tempfile.gettempdir()) / f"no_breakout_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        for i in range(22):
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, 40000.0)])
        config = {
            "N": 5,
            "regime": {
                "breakout_threshold_pct": 0.02,
                "breakout_exposure": 0.35,
                "breakout_cooldown_min": 60,
                "consecutive_below_to_off": 2,
            },
        }
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        strat._regime = REGIME_RISK_OFF

        ctx = TradingContext(
            server_time_ms=(1000 + 22) * MS_PER_DAY,
            ticker={"BTC/USD": {"LastPrice": 40500}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        triggered = strat._check_breakout(ctx)
        assert triggered is False
        assert strat._regime == REGIME_RISK_OFF
    finally:
        _cleanup_db(db_path)


def test_breakout_cooldown_prevents_repeated_triggers() -> None:
    """After a breakout fires, another within cooldown window is blocked."""
    db_path = Path(tempfile.gettempdir()) / f"breakout_cd_{uuid.uuid4().hex}.db"
    store = PriceStore(db_path)
    try:
        for i in range(22):
            store.insert_daily_rows([("BTC/USD", (1000 + i) * MS_PER_DAY, 40000.0)])
        config = {
            "N": 5,
            "regime": {
                "breakout_threshold_pct": 0.02,
                "breakout_exposure": 0.35,
                "breakout_cooldown_min": 60,
                "consecutive_below_to_off": 2,
            },
        }
        strat = HybridTrendCrossSectionalThrottledStrategy(config)
        strat.on_start()
        strat._regime = REGIME_RISK_OFF

        base_ms = (1000 + 22) * MS_PER_DAY
        ctx1 = TradingContext(
            server_time_ms=base_ms,
            ticker={"BTC/USD": {"LastPrice": 41000}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        assert strat._check_breakout(ctx1) is True

        strat._regime = REGIME_RISK_OFF
        ctx2 = TradingContext(
            server_time_ms=base_ms + 30 * 60 * 1000,
            ticker={"BTC/USD": {"LastPrice": 41500}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        assert strat._check_breakout(ctx2) is False
        assert strat._regime == REGIME_RISK_OFF

        strat._regime = REGIME_RISK_OFF
        ctx3 = TradingContext(
            server_time_ms=base_ms + 61 * 60 * 1000,
            ticker={"BTC/USD": {"LastPrice": 41500}},
            balance={"USD": {"Free": 10000, "Lock": 0}},
            pending_orders=[],
            exchange_info={"TradePairs": {"BTC/USD": {"CanTrade": True}}},
            price_store=store,
        )
        assert strat._check_breakout(ctx3) is True
        assert strat._regime == REGIME_RISK_ON_SOFT
    finally:
        _cleanup_db(db_path)
