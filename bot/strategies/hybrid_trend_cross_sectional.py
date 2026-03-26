"""Hybrid Trend-Filter + Intraday Cross-Sectional Momentum: BTC MA20 regime + top-N hourly (4h/12h/24h) MomScore, inverse-vol weights."""

from __future__ import annotations

import logging
import math
from typing import Any

from bot.base import FeeSchedule, PlaceOrderSignal, Signal, Strategy, TradingContext
from bot.price_store import BTC_PAIR
from bot.regime import REGIME_RISK_OFF, REGIME_RISK_ON, compute_regime
from bot.risk import DrawdownConfig, get_drawdown_exposure, should_restore_exposure
from bot.strategies.utils import (
    get_balance_free,
    get_change_pct,
    get_max_bid,
    get_min_ask,
    get_price,
    get_volume_usd,
    parse_pair,
    tradeable_pairs,
)

logger = logging.getLogger(__name__)

_MIN_HOURLY_BARS = 25


def _rolling_volatility_24h(hourly_closes: list[float], vol_floor: float = 0.01) -> float:
    """Std-dev of step-to-step hourly returns over ~24h of hourly closes; floor at vol_floor."""
    if len(hourly_closes) < 2:
        return vol_floor
    returns = []
    for i in range(1, len(hourly_closes)):
        prev = hourly_closes[i - 1]
        if prev > 0:
            returns.append((hourly_closes[i] - prev) / prev)
    if len(returns) < 2:
        return vol_floor
    n = len(returns)
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    vol = math.sqrt(var) if var > 0 else 0.0
    return max(vol, vol_floor)


class HybridTrendCrossSectionalStrategy(Strategy):
    """BTC MA20 regime filter + intraday cross-sectional momentum (4h/12h/24h), inverse-vol top-N, long-only."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._n = int(config.get("N", 3))
        self._ma_window = int(config.get("ma_window", 10))
        self._momentum_weights = list(config.get("momentum_weights") or [0.5, 0.3, 0.2])
        if len(self._momentum_weights) != 3:
            self._momentum_weights = [0.5, 0.3, 0.2]
        self._target_exposure = float(config.get("target_exposure", 1.0))
        self._max_weight_per_coin = float(config.get("max_weight_per_coin", 0.35))
        self._min_trade_usd = float(config.get("min_trade_usd", 50.0))
        self._min_volume_usd = float(config.get("min_volume_usd", 500_000))
        self._min_price_usd = float(config.get("min_price_usd", 0.0))
        self._min_days_history = int(config.get("min_days_history", 3))
        self._rank_interval_min = int(config.get("rank_interval_min", 60))
        self._regime_eval_hours = int(config.get("regime_eval_hours", 24))
        if self._regime_eval_hours < 1:
            self._regime_eval_hours = 24
        self._regime_eval_ms = self._regime_eval_hours * 3600 * 1000
        self._min_rebalance_pct = float(config.get("min_rebalance_pct", 0.02))
        self._pair_cooldown_min = int(config.get("pair_cooldown_min", 30))
        self._regime: str = REGIME_RISK_OFF
        self._regime_candidate: str | None = None
        self._last_regime_eval_bucket: int | None = None
        self._last_rank_time_ms: int | None = None
        self._target_weights: dict[str, float] = {}
        self._portfolio_peak: float = 0.0
        self._effective_exposure: float = 1.0
        self._last_trade_time: dict[str, int] = {}
        self._exclude_pairs: list[str] = list(config.get("exclude_pairs") or [])
        self._fees = FeeSchedule(
            market_rate=float(config.get("fee_market_rate", 0.001)),
            limit_rate=float(config.get("fee_limit_rate", 0.0005)),
        )
        self._use_limit_fee_opt = bool(config.get("use_limit_fee_optimization", True))
        self._limit_price_offset = float(config.get("limit_price_offset", 0.001))
        self._rank_buffer = int(config.get("rank_buffer", 1))
        self._regime_filter_enabled = bool(config.get("regime_filter_enabled", False))
        self._min_hold_hours = float(config.get("min_hold_hours", 4.0))
        self._max_change_filter = float(config.get("max_change_filter", 0.50))
        self._bottom_trim_pct = float(config.get("bottom_trim_pct", 0.20))
        self._volatility_floor = float(config.get("volatility_floor", 0.01))
        risk_cfg = config.get("risk") or {}
        self._drawdown_config = DrawdownConfig.from_config(risk_cfg)
        self._recovery_ratio = self._drawdown_config.recovery_ratio
        self._position_entry_time: dict[str, int] = {}

    def on_start(self) -> None:
        self._regime = REGIME_RISK_OFF if self._regime_filter_enabled else REGIME_RISK_ON
        self._regime_candidate = None
        self._last_regime_eval_bucket = None
        self._last_rank_time_ms = None
        self._target_weights = {}
        self._portfolio_peak = 0.0
        self._effective_exposure = self._target_exposure
        self._last_trade_time = {}
        self._position_entry_time = {}

    def _is_risk_off(self) -> bool:
        if not self._regime_filter_enabled:
            return False
        return self._regime == REGIME_RISK_OFF

    def _get_base_exposure(self) -> float:
        return self._target_exposure

    def _pre_rerank(self, context: TradingContext, now: int) -> bool:
        """Hook called before rerank check. Return True to force a rerank."""
        return False

    def _is_pair_on_cooldown(self, pair: str, now_ms: int) -> bool:
        if self._pair_cooldown_min <= 0:
            return False
        last = self._last_trade_time.get(pair)
        if last is None:
            return False
        return (now_ms - last) < self._pair_cooldown_min * 60 * 1000

    def _record_trade(self, pair: str, now_ms: int) -> None:
        self._last_trade_time[pair] = now_ms

    def _make_order_signal(
        self,
        pair: str,
        side: str,
        quantity: float,
        ticker: dict[str, Any],
    ) -> PlaceOrderSignal:
        """Build a PlaceOrderSignal, using an aggressive LIMIT when the fee optimization is active.

        BUY: price just above MaxBid triggers the 0.05% limit fee tier while filling immediately.
        SELL: price just below MinAsk does the same for the sell side.
        Falls back to MARKET when bid/ask data is unavailable or the book looks crossed.
        """
        if not self._use_limit_fee_opt:
            return PlaceOrderSignal(pair, side, quantity, "MARKET", None)

        max_bid = get_max_bid(ticker, pair)
        min_ask = get_min_ask(ticker, pair)

        offset = self._limit_price_offset
        if side == "BUY" and max_bid > 0:
            if min_ask > 0 and min_ask < max_bid:
                return PlaceOrderSignal(pair, side, quantity, "MARKET", None)
            limit_price = max_bid * (1 + offset)
            return PlaceOrderSignal(pair, side, quantity, "LIMIT", limit_price)

        if side == "SELL" and min_ask > 0:
            if max_bid > 0 and min_ask < max_bid:
                return PlaceOrderSignal(pair, side, quantity, "MARKET", None)
            limit_price = min_ask * (1 - offset)
            return PlaceOrderSignal(pair, side, quantity, "LIMIT", limit_price)

        return PlaceOrderSignal(pair, side, quantity, "MARKET", None)

    def _active_fee_type(self) -> str:
        """Return the order type used for fee calculations (dead-zone, sizing)."""
        return "LIMIT" if self._use_limit_fee_opt else "MARKET"

    def _sell_stale_positions(
        self,
        context: TradingContext,
        tradeable: list[str],
        now_ms: int,
    ) -> list[Signal]:
        """SELL any held asset that is tradeable but no longer in _target_weights."""
        signals: list[Signal] = []
        target_bases = {parse_pair(p)[0] for p in self._target_weights}
        hold_ms = self._min_hold_hours * 3600 * 1000
        is_risk_off = self._is_risk_off()
        for pair in tradeable:
            base, _ = parse_pair(pair)
            if base in target_bases:
                continue
            if self._is_pair_on_cooldown(pair, now_ms):
                continue
            if not is_risk_off and hold_ms > 0:
                entry_time = self._position_entry_time.get(pair)
                if entry_time is not None and (now_ms - entry_time) < hold_ms:
                    continue
            qty = get_balance_free(context.balance, base)
            if qty <= 0:
                continue
            price = get_price(context.ticker, pair)
            if price <= 0:
                continue
            value = qty * price
            if value < self._min_trade_usd:
                continue
            signals.append(self._make_order_signal(pair, "SELL", qty, context.ticker))
            self._record_trade(pair, now_ms)
        return signals

    def _is_regime_eval_time(self, server_time_ms: int) -> bool:
        """True if we should re-evaluate regime (every ``_regime_eval_hours`` hours)."""
        bucket = server_time_ms // self._regime_eval_ms
        if self._last_regime_eval_bucket is None:
            return True
        return bucket != self._last_regime_eval_bucket

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
        self._last_regime_eval_bucket = context.server_time_ms // self._regime_eval_ms
        logger.info("regime=%s candidate=%s", self._regime, self._regime_candidate)

    def _cross_sectional_rank(self, context: TradingContext) -> list[tuple[str, float, float]]:
        """Return list of (pair, mom_score, vol) for eligible pairs using hourly 4h/12h/24h returns, sorted desc."""
        store = context.price_store
        if not store or not context.exchange_info:
            return []
        pairs = tradeable_pairs(context.exchange_info, exclude=self._exclude_pairs)
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
            if abs(ch) > self._max_change_filter:
                continue
            closes = store.get_hourly_closes(pair, _MIN_HOURLY_BARS)
            if len(closes) < _MIN_HOURLY_BARS:
                continue
            if closes[-5] <= 0 or closes[-13] <= 0 or closes[-25] <= 0:
                continue
            r4 = (closes[-1] - closes[-5]) / closes[-5]
            r12 = (closes[-1] - closes[-13]) / closes[-13]
            r24 = (closes[-1] - closes[-25]) / closes[-25]
            w4, w12, w24 = self._momentum_weights
            mom = w4 * r4 + w12 * r12 + w24 * r24
            # Absolute momentum guard: never allocate to negative-trending assets
            if mom <= 0:
                continue
            vol = _rolling_volatility_24h(closes, self._volatility_floor)
            scored.append((pair, mom, vol))
        scored.sort(key=lambda x: -x[1])
        return scored

    def _compute_target_weights(self, context: TradingContext) -> dict[str, float]:
        if self._is_risk_off():
            return {}
        ranked = self._cross_sectional_rank(context)
        if not ranked:
            return {}
        n_cut = max(1, int(len(ranked) * self._bottom_trim_pct))
        top_pool = ranked[:-n_cut] if n_cut < len(ranked) else ranked

        held_bases = {parse_pair(p)[0] for p in self._target_weights}
        selected: list[tuple[str, float, float]] = []
        for pair, mom, vol in top_pool:
            base = parse_pair(pair)[0]
            rank = len(selected)
            if rank < self._n:
                selected.append((pair, mom, vol))
            elif base in held_bases and rank < self._n + self._rank_buffer:
                selected.append((pair, mom, vol))

        if not selected:
            return {}
        inv_vols = {p: 1.0 / vol for p, _, vol in selected}
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

        base_exposure = self._get_base_exposure()
        if should_restore_exposure(portfolio_value, self._portfolio_peak, self._recovery_ratio):
            self._effective_exposure = base_exposure
        else:
            exposure, force_risk_off = get_drawdown_exposure(
                portfolio_value,
                self._portfolio_peak,
                base_exposure,
                dd=self._drawdown_config,
            )
            self._effective_exposure = exposure
            if force_risk_off:
                self._regime = REGIME_RISK_OFF
                self._target_weights = {}

        now = context.server_time_ms
        if self._regime_filter_enabled and self._is_regime_eval_time(now):
            self._compute_regime(context)

        force_rerank = self._pre_rerank(context, now)

        if self._should_rerank(now) or force_rerank:
            self._target_weights = self._compute_target_weights(context)
            self._last_rank_time_ms = now
            for pair in self._target_weights:
                if pair not in self._position_entry_time:
                    self._position_entry_time[pair] = now
            self._position_entry_time = {
                p: t for p, t in self._position_entry_time.items()
                if p in self._target_weights
            }

        target_usd: dict[str, float] = {}
        for pair, w in self._target_weights.items():
            target_usd[pair] = portfolio_value * w

        stale_sells = self._sell_stale_positions(context, pairs, now)

        sell_signals: list[Signal] = []
        buy_signals: list[Signal] = []
        remaining_quote = get_balance_free(context.balance, "USD") + get_balance_free(context.balance, "USDT")
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
                spend = min(delta_usd, remaining_quote)
                if spend >= self._min_trade_usd and spend > 0:
                    buy_qty = spend / (price * (1 + self._fees.rate_for(fee_type)))
                    buy_signals.append(self._make_order_signal(pair, "BUY", buy_qty, context.ticker))
                    self._record_trade(pair, now)
                    remaining_quote -= spend

        return stale_sells + sell_signals + buy_signals

    def get_managed_pairs(self) -> list[str] | None:
        return list(self._target_weights.keys()) if self._target_weights else None
