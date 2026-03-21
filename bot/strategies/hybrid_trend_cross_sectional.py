"""Hybrid Trend-Filter + Cross-Sectional Momentum: BTC MA20 regime + top-N MomScore, inverse-vol weights."""

from __future__ import annotations

import logging
import math
from typing import Any

from bot.base import FeeSchedule, PlaceOrderSignal, Signal, Strategy, TradingContext
from bot.price_store import BTC_PAIR, MS_PER_DAY
from bot.regime import REGIME_RISK_OFF, REGIME_RISK_ON, compute_regime
from bot.risk import get_drawdown_exposure, should_restore_exposure
from bot.strategies.utils import (
    get_balance_free,
    get_change_pct,
    get_price,
    get_volume_usd,
    parse_pair,
    tradeable_pairs,
)

logger = logging.getLogger(__name__)

# Minimum days of history for momentum (r7 needs 7+1 closes)
MIN_DAYS_FOR_MOMENTUM = 8


def _momentum_score(r1: float, r3: float, r7: float, w1: float, w3: float, w7: float) -> float:
    return w1 * r1 + w3 * r3 + w7 * r7


def _rolling_volatility_7d(daily_closes: list[float]) -> float:
    """Annualized volatility from 7 daily returns; floor 0.01."""
    if len(daily_closes) < 8:
        return 0.01
    returns = []
    for i in range(1, len(daily_closes)):
        if daily_closes[i - 1] and daily_closes[i - 1] > 0:
            returns.append((daily_closes[i] - daily_closes[i - 1]) / daily_closes[i - 1])
    if len(returns) < 2:
        return 0.01
    n = len(returns)
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
    vol = math.sqrt(var) if var > 0 else 0.0
    return max(vol, 0.01)


class HybridTrendCrossSectionalStrategy(Strategy):
    """BTC MA20 regime filter + cross-sectional momentum (MomScore), inverse-vol top-N, long-only."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._n = int(config.get("N", 5))
        self._ma_window = int(config.get("ma_window", 20))
        self._momentum_weights = config.get("momentum_weights") or [0.2, 0.3, 0.5]
        if len(self._momentum_weights) != 3:
            self._momentum_weights = [0.2, 0.3, 0.5]
        self._target_exposure = float(config.get("target_exposure", 0.85))
        self._max_weight_per_coin = float(config.get("max_weight_per_coin", 0.20))
        self._min_trade_usd = float(config.get("min_trade_usd", 50.0))
        self._min_volume_usd = float(config.get("min_volume_usd", 500_000))
        self._min_price_usd = float(config.get("min_price_usd", 0.0))
        self._min_days_history = int(config.get("min_days_history", 3))
        self._rank_interval_min = int(config.get("rank_interval_min", 60))
        self._regime_utc_hour = int(config.get("regime_utc_hour", 0))
        self._regime: str = REGIME_RISK_OFF
        self._regime_candidate: str | None = None
        self._last_regime_eval_day: int | None = None
        self._last_rank_time_ms: int | None = None
        self._target_weights: dict[str, float] = {}
        self._portfolio_peak: float = 0.0
        self._effective_exposure: float = 0.85
        self._exclude_pairs: list[str] = list(config.get("exclude_pairs") or [])
        self._fees = FeeSchedule(
            market_rate=float(config.get("fee_market_rate", 0.001)),
            limit_rate=float(config.get("fee_limit_rate", 0.0005)),
        )

    def on_start(self) -> None:
        self._regime = REGIME_RISK_OFF
        self._regime_candidate = None
        self._last_regime_eval_day = None
        self._last_rank_time_ms = None
        self._target_weights = {}
        self._portfolio_peak = 0.0
        self._effective_exposure = self._target_exposure

    def _is_daily_regime_time(self, server_time_ms: int) -> bool:
        """True if we should re-evaluate regime (once per UTC day)."""
        day_key = server_time_ms // MS_PER_DAY
        if self._last_regime_eval_day is None:
            return True
        return day_key != self._last_regime_eval_day

    def _should_rerank(self, server_time_ms: int) -> bool:
        if self._last_rank_time_ms is None:
            return True
        return (server_time_ms - self._last_rank_time_ms) >= self._rank_interval_min * 60 * 1000

    def _compute_regime(self, context: TradingContext) -> None:
        if not context.price_store:
            return
        btc_closes = context.price_store.get_daily_closes(BTC_PAIR, self._ma_window + 2)
        self._regime, self._regime_candidate = compute_regime(
            btc_closes,
            self._ma_window,
            self._regime,
            self._regime_candidate,
        )
        self._last_regime_eval_day = context.server_time_ms // MS_PER_DAY
        logger.info("regime=%s candidate=%s", self._regime, self._regime_candidate)

    def _cross_sectional_rank(self, context: TradingContext) -> list[tuple[str, float, float]]:
        """Return list of (pair, mom_score, vol) for eligible pairs, sorted by MomScore desc."""
        store = context.price_store
        if not store or not context.exchange_info:
            return []
        pairs = tradeable_pairs(context.exchange_info, exclude=self._exclude_pairs)
        w1, w3, w7 = self._momentum_weights[0], self._momentum_weights[1], self._momentum_weights[2]
        scored: list[tuple[str, float, float]] = []
        for pair in pairs:
            if store.count_days_with_data(pair) < self._min_days_history:
                continue
            vol_usd = get_volume_usd(context.ticker, pair)
            if vol_usd < self._min_volume_usd:
                continue
            if self._min_price_usd > 0:
                price = get_price(context.ticker, pair)
                if price < self._min_price_usd:
                    continue
            ch = get_change_pct(context.ticker, pair)
            if abs(ch) > 0.50:
                continue
            closes = store.get_daily_closes(pair, MIN_DAYS_FOR_MOMENTUM)
            if len(closes) < MIN_DAYS_FOR_MOMENTUM:
                continue
            p0 = closes[-1]
            p1 = closes[-2] if len(closes) >= 2 else p0
            p3 = closes[-4] if len(closes) >= 4 else p1
            p7 = closes[-8] if len(closes) >= 8 else p3
            r1 = (p0 - p1) / p1 if p1 and p1 > 0 else 0.0
            r3 = (p0 - p3) / p3 if p3 and p3 > 0 else 0.0
            r7 = (p0 - p7) / p7 if p7 and p7 > 0 else 0.0
            mom = _momentum_score(r1, r3, r7, w1, w3, w7)
            vol = _rolling_volatility_7d(closes)
            scored.append((pair, mom, vol))
        scored.sort(key=lambda x: -x[1])
        return scored

    def _compute_target_weights(self, context: TradingContext) -> dict[str, float]:
        if self._regime == REGIME_RISK_OFF:
            return {}
        ranked = self._cross_sectional_rank(context)
        if not ranked:
            return {}
        n_cut = max(1, int(len(ranked) * 0.20))
        top_pool = ranked[:-n_cut] if n_cut < len(ranked) else ranked
        top_n = top_pool[: self._n]
        if not top_n:
            return {}
        inv_vols = {p: 1.0 / vol for p, _, vol in top_n}
        total_iv = sum(inv_vols.values())
        raw = {p: inv_vols[p] / total_iv for p in inv_vols}
        capped = {p: min(w, self._max_weight_per_coin) for p, w in raw.items()}
        total_capped = sum(capped.values())
        if total_capped <= 0:
            return {}
        weights = {p: (capped[p] / total_capped) * self._effective_exposure for p in capped}
        return weights

    def _portfolio_value(self, context: TradingContext, pairs: list[str]) -> float:
        pv = get_balance_free(context.balance, "USD") + get_balance_free(context.balance, "USDT")
        for pair in pairs:
            base, _ = parse_pair(pair)
            qty = get_balance_free(context.balance, base)
            price = get_price(context.ticker, pair)
            if price > 0:
                pv += qty * price
        return pv

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
        if should_restore_exposure(portfolio_value, self._portfolio_peak):
            self._effective_exposure = self._target_exposure
        else:
            exposure, force_risk_off = get_drawdown_exposure(
                portfolio_value,
                self._portfolio_peak,
                self._target_exposure,
            )
            self._effective_exposure = exposure
            if force_risk_off:
                self._regime = REGIME_RISK_OFF
                self._target_weights = {}

        now = context.server_time_ms
        if self._is_daily_regime_time(now):
            self._compute_regime(context)

        if self._should_rerank(now):
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

    def get_managed_pairs(self) -> list[str] | None:
        return list(self._target_weights.keys()) if self._target_weights else None
