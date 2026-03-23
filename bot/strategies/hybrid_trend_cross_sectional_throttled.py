"""Hybrid Trend + Cross-Sectional Momentum (throttled): three-tier BTC regime, soft exposure in prelim."""

from __future__ import annotations

import logging
from typing import Any

from bot.base import Signal, TradingContext
from bot.indicators import sma
from bot.price_store import BTC_PAIR, MS_PER_DAY
from bot.risk import get_drawdown_exposure, should_restore_exposure
from bot.strategies.hybrid_trend_cross_sectional import HybridTrendCrossSectionalStrategy
from bot.strategies.utils import get_balance_free, get_price, parse_pair, tradeable_pairs

logger = logging.getLogger(__name__)

REGIME_RISK_ON_STRONG = "risk_on_strong"
REGIME_RISK_ON_SOFT = "risk_on_soft"
REGIME_RISK_OFF = "risk_off"


def _default_regime_config(config: dict[str, Any]) -> dict[str, Any]:
    r = config.get("regime") or {}
    return {
        "ma_window": int(r.get("ma_window", 20)),
        "prelim_mode": bool(r.get("prelim_mode", True)),
        "strong_exposure": float(r.get("strong_exposure", 0.85)),
        "soft_exposure": float(r.get("soft_exposure", 0.35)),
        "consecutive_below_to_off": int(r.get("consecutive_below_to_off", 2)),
        "regime_eval_hours": int(r.get("regime_eval_hours", 6)),
        "breakout_threshold_pct": float(r.get("breakout_threshold_pct", 0.02)),
        "breakout_exposure": float(r.get("breakout_exposure", 0.35)),
        "breakout_cooldown_min": int(r.get("breakout_cooldown_min", 60)),
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
        self._regime = REGIME_RISK_OFF
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
        prelim = self._regime_config["prelim_mode"]
        strong = self._regime_config["strong_exposure"]
        soft = self._regime_config["soft_exposure"]
        if not prelim:
            return strong if self._regime == REGIME_RISK_ON_STRONG else 0.0
        if self._regime == REGIME_RISK_ON_STRONG:
            return strong
        if self._regime == REGIME_RISK_ON_SOFT:
            return soft
        return 0.0

    def _compute_regime(self, context: TradingContext) -> None:
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

    def _compute_target_weights(self, context: TradingContext) -> dict[str, float]:
        if self._regime == REGIME_RISK_OFF:
            return {}
        return super()._compute_target_weights(context)

    def next(self, context: TradingContext) -> list[Signal]:
        if context.price_store is None or context.exchange_info is None:
            return []

        if getattr(context, "risk_force_cash", False):
            self._regime = REGIME_RISK_OFF
            self._target_weights = {}
            return []

        pairs = tradeable_pairs(context.exchange_info, exclude=self._exclude_pairs)
        portfolio_value = self._portfolio_value(context, pairs)
        if portfolio_value > self._portfolio_peak:
            self._portfolio_peak = portfolio_value

        base_target = self._get_target_exposure()
        if should_restore_exposure(portfolio_value, self._portfolio_peak):
            self._effective_exposure = base_target
        else:
            exposure, force_risk_off = get_drawdown_exposure(
                portfolio_value,
                self._portfolio_peak,
                base_target,
            )
            self._effective_exposure = exposure
            if force_risk_off:
                self._regime = REGIME_RISK_OFF
                self._target_weights = {}

        now = context.server_time_ms
        if self._is_regime_eval_time(now):
            self._compute_regime(context)

        breakout_triggered = self._check_breakout(context)

        _needs_rerank = self._should_rerank(now) or breakout_triggered
        if not _needs_rerank and self._effective_exposure > 0 and self._target_weights and all(w == 0.0 for w in self._target_weights.values()):
            _needs_rerank = True
        if _needs_rerank:
            self._target_weights = self._compute_target_weights(context)
            self._last_rank_time_ms = now

        target_usd: dict[str, float] = {}
        for pair, w in self._target_weights.items():
            target_usd[pair] = portfolio_value * w

        stale_sells = self._sell_stale_positions(context, pairs, now)

        sell_signals: list[Signal] = []
        buy_signals: list[Signal] = []
        for pair in self._target_weights:
            if self._is_pair_on_cooldown(pair, now):
                continue
            base, _ = parse_pair(pair)
            price = get_price(context.ticker, pair)
            if price <= 0:
                continue
            current_qty = get_balance_free(context.balance, base)
            current_value = current_qty * price
            target = target_usd.get(pair, 0.0)
            delta_usd = target - current_value
            fee_type = self._active_fee_type()
            fee_threshold = current_value * self._fees.round_trip(fee_type)
            pct_threshold = target * self._min_rebalance_pct if target > 0 else 0.0
            if abs(delta_usd) < max(self._min_trade_usd, fee_threshold, pct_threshold):
                continue
            qty = abs(delta_usd) / price
            if delta_usd < 0:
                to_sell = min(qty, current_qty)
                if to_sell > 0:
                    sell_signals.append(self._make_order_signal(pair, "SELL", to_sell, context.ticker))
                    self._record_trade(pair, now)
            else:
                quote_free = get_balance_free(context.balance, "USD") + get_balance_free(context.balance, "USDT")
                spend = min(delta_usd, quote_free)
                if spend >= self._min_trade_usd and spend > 0:
                    buy_qty = spend / (price * (1 + self._fees.rate_for(fee_type)))
                    buy_signals.append(self._make_order_signal(pair, "BUY", buy_qty, context.ticker))
                    self._record_trade(pair, now)

        return stale_sells + sell_signals + buy_signals
