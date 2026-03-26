"""Hybrid Trend + Cross-Sectional Momentum (throttled): three-tier BTC regime, soft exposure in prelim."""

from __future__ import annotations

import logging
from typing import Any

from bot.base import TradingContext
from bot.indicators import sma
from bot.price_store import BTC_PAIR
from bot.strategies.hybrid_trend_cross_sectional import HybridTrendCrossSectionalStrategy
from bot.strategies.utils import get_price

logger = logging.getLogger(__name__)

REGIME_RISK_ON_STRONG = "risk_on_strong"
REGIME_RISK_ON_SOFT = "risk_on_soft"
REGIME_RISK_OFF = "risk_off"


def _default_regime_config(config: dict[str, Any]) -> dict[str, Any]:
    r = config.get("regime") or {}
    return {
        "ma_window": int(r.get("ma_window", 10)),
        "prelim_mode": bool(r.get("prelim_mode", True)),
        "strong_exposure": float(r.get("strong_exposure", 1.0)),
        "soft_exposure": float(r.get("soft_exposure", 0.35)),
        "consecutive_below_to_off": int(r.get("consecutive_below_to_off", 2)),
        "regime_eval_hours": int(r.get("regime_eval_hours", 6)),
        "breakout_threshold_pct": float(r.get("breakout_threshold_pct", 0.02)),
        "breakout_exposure": float(r.get("breakout_exposure", 0.35)),
        "breakout_cooldown_min": int(r.get("breakout_cooldown_min", 60)),
        "breakdown_threshold_pct": float(r.get("breakdown_threshold_pct", 0.02)),
    }


class HybridTrendCrossSectionalThrottledStrategy(HybridTrendCrossSectionalStrategy):
    """Same as hybrid_trend_cross_sectional but with three-tier regime and prelim soft exposure.

    Regimes: risk_on_strong (close > MA20), risk_on_soft (close <= MA20 but < 2 consecutive
    closes below), risk_off (>= 2 consecutive daily closes below MA20).
    In prelim_mode: strong -> full exposure, soft -> reduced (e.g. 0.35), risk_off -> 0.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._regime_config = _default_regime_config(config)
        regime_hours = self._regime_config["regime_eval_hours"]
        config_with_eval = {**config, "regime_eval_hours": regime_hours}
        super().__init__(config_with_eval)
        self._regime: str = REGIME_RISK_OFF
        self._consecutive_btc_below_ma: int = 0
        self._last_breakout_ms: int | None = None

    def on_start(self) -> None:
        super().on_start()
        self._regime = REGIME_RISK_OFF if self._regime_filter_enabled else REGIME_RISK_ON_STRONG
        self._consecutive_btc_below_ma = 0
        self._last_breakout_ms = None
        self._effective_exposure = self._get_target_exposure()

    def _update_btc_regime(self, context: TradingContext) -> None:
        """Update regime from BTC close vs MA. Uses hourly closes when eval < 24h, else daily."""
        if not context.price_store:
            return
        ma_window = self._regime_config["ma_window"]
        consecutive_to_off = self._regime_config["consecutive_below_to_off"]
        eval_hours = self._regime_config["regime_eval_hours"]

        if eval_hours < 24:
            needed_hours = ma_window * 24
            btc_closes = context.price_store.get_hourly_closes(BTC_PAIR, needed_hours)
            if len(btc_closes) < ma_window:
                btc_closes = context.price_store.get_daily_closes(BTC_PAIR, ma_window + 2)
        else:
            btc_closes = context.price_store.get_daily_closes(BTC_PAIR, ma_window + 2)

        if len(btc_closes) < ma_window:
            return
        ma_vals = sma(btc_closes, ma_window)
        if not ma_vals:
            return
        last_close = btc_closes[-1]
        last_ma = ma_vals[-1]
        if last_close > last_ma:
            self._consecutive_btc_below_ma = 0
            self._regime = REGIME_RISK_ON_STRONG
        else:
            self._consecutive_btc_below_ma += 1
            if self._consecutive_btc_below_ma >= consecutive_to_off:
                self._regime = REGIME_RISK_OFF
            else:
                self._regime = REGIME_RISK_ON_SOFT
        self._last_regime_eval_bucket = context.server_time_ms // self._regime_eval_ms
        logger.info(
            "regime=%s consecutive_btc_below_ma=%s",
            self._regime,
            self._consecutive_btc_below_ma,
        )

    def _get_target_exposure(self) -> float:
        """Base target exposure from regime and prelim_mode (before drawdown ladder)."""
        strong = self._regime_config["strong_exposure"]
        if not self._regime_filter_enabled:
            return strong
        prelim = self._regime_config["prelim_mode"]
        soft = self._regime_config["soft_exposure"]
        if not prelim:
            return strong if self._regime == REGIME_RISK_ON_STRONG else 0.0
        if self._regime == REGIME_RISK_ON_STRONG:
            return strong
        if self._regime == REGIME_RISK_ON_SOFT:
            return soft
        return 0.0

    def _is_risk_off(self) -> bool:
        if not self._regime_filter_enabled:
            return False
        return self._regime == REGIME_RISK_OFF

    def _get_base_exposure(self) -> float:
        return self._get_target_exposure()

    def _compute_regime(self, context: TradingContext) -> None:
        if not self._regime_filter_enabled:
            return
        self._update_btc_regime(context)

    def _check_breakout(self, context: TradingContext) -> bool:
        """If risk_off and live BTC price exceeds daily MA20 by threshold, enter risk_on_soft.

        Returns True if breakout was triggered (caller should force a re-rank).
        """
        if self._regime != REGIME_RISK_OFF:
            return False
        threshold = self._regime_config["breakout_threshold_pct"]
        if threshold <= 0:
            return False
        cooldown_ms = self._regime_config["breakout_cooldown_min"] * 60 * 1000
        now = context.server_time_ms
        if self._last_breakout_ms is not None and (now - self._last_breakout_ms) < cooldown_ms:
            return False
        if not context.price_store:
            return False
        ma_window = self._regime_config["ma_window"]
        btc_closes = context.price_store.get_daily_closes(BTC_PAIR, ma_window + 2)
        if len(btc_closes) < ma_window:
            return False
        ma_vals = sma(btc_closes, ma_window)
        if not ma_vals:
            return False
        last_ma = ma_vals[-1]
        btc_price = get_price(context.ticker, BTC_PAIR)
        if btc_price <= 0:
            return False
        if btc_price > last_ma * (1 + threshold):
            self._regime = REGIME_RISK_ON_SOFT
            self._effective_exposure = self._regime_config["breakout_exposure"]
            self._last_breakout_ms = now
            logger.info(
                "breakout: BTC %.2f > MA20 %.2f * %.3f, regime -> risk_on_soft (exposure=%.2f)",
                btc_price, last_ma, 1 + threshold, self._effective_exposure,
            )
            return True
        return False

    def _check_breakdown(self, context: TradingContext) -> bool:
        """If risk_on and live BTC price falls significantly below MA20, immediately go risk_off.

        Mirror of _check_breakout: provides fast downside exit without waiting for the
        next scheduled regime evaluation.
        """
        if self._regime == REGIME_RISK_OFF:
            return False
        threshold = self._regime_config["breakdown_threshold_pct"]
        if threshold <= 0:
            return False
        if not context.price_store:
            return False
        ma_window = self._regime_config["ma_window"]
        btc_closes = context.price_store.get_daily_closes(BTC_PAIR, ma_window + 2)
        if len(btc_closes) < ma_window:
            return False
        ma_vals = sma(btc_closes, ma_window)
        if not ma_vals:
            return False
        last_ma = ma_vals[-1]
        btc_price = get_price(context.ticker, BTC_PAIR)
        if btc_price <= 0:
            return False
        if btc_price < last_ma * (1 - threshold):
            prev_regime = self._regime
            self._regime = REGIME_RISK_OFF
            self._effective_exposure = 0.0
            self._target_weights = {}
            logger.warning(
                "breakdown: BTC %.2f < MA20 %.2f * %.3f, regime %s -> risk_off (immediate liquidation)",
                btc_price, last_ma, 1 - threshold, prev_regime,
            )
            return True
        return False

    def _pre_rerank(self, context: TradingContext, now: int) -> bool:
        if not self._regime_filter_enabled:
            return False
        breakdown_triggered = self._check_breakdown(context)
        if breakdown_triggered:
            return True
        breakout_triggered = self._check_breakout(context)
        if breakout_triggered:
            return True
        if self._effective_exposure > 0 and self._target_weights and all(w == 0.0 for w in self._target_weights.values()):
            return True
        return False
