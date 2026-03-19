"""Cross-Sectional Momentum: rank by 90d return, filter by 200d MA, equal-weight top N (max 20% per coin), rebalance every 7 days."""

from __future__ import annotations

import logging
from typing import Any

from bot.base import PlaceOrderSignal, Signal, Strategy, TradingContext
from bot.indicators import sma
from bot.ohlcv import OHLCVUnavailableError
from bot.strategies.utils import get_balance_free, get_price, parse_pair, tradeable_pairs

logger = logging.getLogger(__name__)

REBALANCE_MS = 7 * 24 * 3600 * 1000


class CrossSectionalMomentumStrategy(Strategy):
    """Weekly rebalance: top 3–5 by 90d return, above 200 MA, equal weight (cap 20% per coin)."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._rebalance_days = float(config.get("rebalance_days", 7))
        self._top_n = int(config.get("top_n", 5))
        self._max_weight_per_coin = float(config.get("max_weight_per_coin", 0.20))
        self._return_lookback_days = int(config.get("return_lookback_days", 90))
        self._ma_filter_days = int(config.get("ma_filter_days", 200))
        self._last_rebalance_ms: int | None = None
        self._exclude_pairs: list[str] = list(config.get("exclude_pairs") or [])

    def on_start(self) -> None:
        self._last_rebalance_ms = None

    def next(self, context: TradingContext) -> list[Signal]:
        if context.ohlcv_provider is None or context.exchange_info is None:
            return []

        now = context.server_time_ms
        if self._last_rebalance_ms is not None and now - self._last_rebalance_ms < REBALANCE_MS:
            return []

        pairs = tradeable_pairs(context.exchange_info, exclude=self._exclude_pairs)
        if not pairs:
            return []

        rankings: list[tuple[str, float]] = []
        for pair in pairs:
            try:
                candles = context.ohlcv_provider.get_klines(pair, "1d", self._ma_filter_days + 5)
            except OHLCVUnavailableError:
                continue
            if len(candles) < self._ma_filter_days or len(candles) < self._return_lookback_days + 1:
                continue
            closes = [c["close"] for c in candles]
            sma_200 = sma(closes, self._ma_filter_days)
            if not sma_200:
                continue
            current = closes[-1]
            ma = sma_200[-1]
            if current < ma or ma <= 0:
                continue
            idx_90 = max(0, len(closes) - 1 - self._return_lookback_days)
            close_90 = closes[idx_90]
            if close_90 <= 0:
                continue
            ret = (current - close_90) / close_90
            rankings.append((pair, ret))

        rankings.sort(key=lambda x: -x[1])
        leaders = [p for p, _ in rankings[: self._top_n * 2]][: self._top_n]
        if not leaders:
            self._last_rebalance_ms = now
            return []

        weights: dict[str, float] = {}
        n = len(leaders)
        for i, p in enumerate(leaders):
            w = 1.0 / n
            weights[p] = min(w, self._max_weight_per_coin)
        total_w = sum(weights.values())
        if total_w > 0:
            for p in weights:
                weights[p] /= total_w

        quote_balance = get_balance_free(context.balance, "USD") + get_balance_free(context.balance, "USDT")
        portfolio_value = quote_balance
        for pair in pairs:
            base, _ = parse_pair(pair)
            qty = get_balance_free(context.balance, base)
            price = get_price(context.ticker, pair)
            if price > 0:
                portfolio_value += qty * price

        signals: list[Signal] = []
        for pair in leaders:
            base, quote = parse_pair(pair)
            price = get_price(context.ticker, pair)
            if price <= 0:
                continue
            current_value = get_balance_free(context.balance, base) * price
            target_value = portfolio_value * weights.get(pair, 0)
            diff = target_value - current_value
            if abs(diff) < price * 0.001:
                continue
            qty = abs(diff) / price
            if diff > 0:
                spend = min(diff, quote_balance)
                if spend > 0 and spend >= price * 0.0001:
                    signals.append(PlaceOrderSignal(pair, "BUY", spend / price, "MARKET", None))
                    quote_balance -= spend
            else:
                to_sell = min(qty, get_balance_free(context.balance, base))
                if to_sell > 0:
                    signals.append(PlaceOrderSignal(pair, "SELL", to_sell, "MARKET", None))

        self._last_rebalance_ms = now
        return signals

    def get_managed_pairs(self) -> list[str] | None:
        return None
