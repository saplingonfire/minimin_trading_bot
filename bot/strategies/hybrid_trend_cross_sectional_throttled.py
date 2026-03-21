"""Hybrid Trend + Cross-Sectional Momentum (throttled): three-tier BTC regime, soft exposure in prelim."""

from __future__ import annotations

import logging
from typing import Any

from bot.base import PlaceOrderSignal, Signal, TradingContext
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
    }


class HybridTrendCrossSectionalThrottledStrategy(HybridTrendCrossSectionalStrategy):
    """Same as hybrid_trend_cross_sectional but with three-tier regime and prelim soft exposure.

    Regimes: risk_on_strong (close > MA20), risk_on_soft (close <= MA20 but < 2 consecutive
    closes below), risk_off (>= 2 consecutive daily closes below MA20).
    In prelim_mode: strong -> full exposure, soft -> reduced (e.g. 0.35), risk_off -> 0.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._regime_config = _default_regime_config(config)
        self._regime: str = REGIME_RISK_OFF
        self._consecutive_btc_below_ma: int = 0

    def on_start(self) -> None:
        super().on_start()
        self._regime = REGIME_RISK_OFF
        self._consecutive_btc_below_ma = 0
        self._effective_exposure = self._get_target_exposure()

    def _update_btc_regime(self, context: TradingContext) -> None:
        """Update regime and consecutive_below_ma from BTC daily close vs MA20. Call once per day."""
        if not context.price_store:
            return
        ma_window = self._regime_config["ma_window"]
        consecutive_to_off = self._regime_config["consecutive_below_to_off"]
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
        self._last_regime_eval_day = context.server_time_ms // MS_PER_DAY
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
        if self._is_daily_regime_time(now):
            self._compute_regime(context)

        _needs_rerank = self._should_rerank(now)
        if not _needs_rerank and self._effective_exposure > 0 and self._target_weights and all(w == 0.0 for w in self._target_weights.values()):
            _needs_rerank = True
        if _needs_rerank:
            self._target_weights = self._compute_target_weights(context)
            self._last_rank_time_ms = now

        target_usd: dict[str, float] = {}
        for pair, w in self._target_weights.items():
            target_usd[pair] = portfolio_value * w

        sell_signals: list[Signal] = []
        buy_signals: list[Signal] = []
        for pair in self._target_weights:
            base, _ = parse_pair(pair)
            price = get_price(context.ticker, pair)
            if price <= 0:
                continue
            current_qty = get_balance_free(context.balance, base)
            current_value = current_qty * price
            target = target_usd.get(pair, 0.0)
            delta_usd = target - current_value
            fee_threshold = current_value * self._fees.round_trip("MARKET")
            if abs(delta_usd) < max(self._min_trade_usd, fee_threshold):
                continue
            qty = abs(delta_usd) / price
            if delta_usd < 0:
                to_sell = min(qty, current_qty)
                if to_sell > 0:
                    sell_signals.append(PlaceOrderSignal(pair, "SELL", to_sell, "MARKET", None))
            else:
                quote_free = get_balance_free(context.balance, "USD") + get_balance_free(context.balance, "USDT")
                spend = min(delta_usd, quote_free)
                if spend >= self._min_trade_usd and spend > 0:
                    buy_qty = spend / (price * (1 + self._fees.market_rate))
                    buy_signals.append(PlaceOrderSignal(pair, "BUY", buy_qty, "MARKET", None))

        return sell_signals + buy_signals
