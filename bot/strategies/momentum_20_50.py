"""Momentum 20/50: dual EMA crossover (1H or 4H), ATR trailing stop, 100% USDT when flat."""

from __future__ import annotations

import logging
from typing import Any

from bot.base import PlaceOrderSignal, Signal, Strategy, TradingContext
from bot.indicators import atr, ema
from bot.ohlcv import OHLCVUnavailableError
from bot.strategies.utils import get_balance_free, get_price, parse_pair

logger = logging.getLogger(__name__)


class Momentum20_50Strategy(Strategy):
    """Golden cross (EMA20 > EMA50) entry; death cross or ATR trailing stop exit."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._pair = str(config.get("pair", "BTC/USD"))
        self._interval = str(config.get("interval", "4h"))
        self._ema_fast = int(config.get("ema_fast", 20))
        self._ema_slow = int(config.get("ema_slow", 50))
        self._atr_period = int(config.get("atr_period", 14))
        self._atr_mult = float(config.get("atr_mult", 2.0))
        self._position_pct = float(config.get("position_pct", 1.0))
        self._in_position = False
        self._entry_price: float = 0.0
        self._trailing_stop: float = 0.0
        self._prev_ema_fast: float | None = None
        self._prev_ema_slow: float | None = None

    def on_start(self) -> None:
        self._in_position = False
        self._entry_price = 0.0
        self._trailing_stop = 0.0
        self._prev_ema_fast = None
        self._prev_ema_slow = None

    def next(self, context: TradingContext) -> list[Signal]:
        if context.ohlcv_provider is None:
            return []
        try:
            candles = context.ohlcv_provider.get_klines(self._pair, self._interval, 60)
        except OHLCVUnavailableError:
            return []
        if len(candles) < self._ema_slow + 1:
            return []

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        ema_fast_series = ema(closes, self._ema_fast)
        ema_slow_series = ema(closes, self._ema_slow)
        atr_series = atr(highs, lows, closes, self._atr_period)
        if not ema_fast_series or not ema_slow_series or not atr_series:
            return []

        cur_ema_fast = ema_fast_series[-1]
        cur_ema_slow = ema_slow_series[-1]
        cur_atr = atr_series[-1]
        price = get_price(context.ticker, self._pair)
        if price <= 0:
            return []

        base, quote = parse_pair(self._pair)
        quote_free = get_balance_free(context.balance, quote)
        base_held = get_balance_free(context.balance, base)

        if base_held > 0 and not self._in_position:
            self._in_position = True
            self._entry_price = price
            self._trailing_stop = price - self._atr_mult * cur_atr

        if self._in_position:
            self._trailing_stop = max(self._trailing_stop, price - self._atr_mult * cur_atr)
            if cur_ema_fast < cur_ema_slow:
                self._in_position = False
                self._trailing_stop = 0.0
                qty = base_held
                if qty > 0:
                    return [PlaceOrderSignal(self._pair, "SELL", qty, "MARKET", None)]
            elif price < self._trailing_stop:
                self._in_position = False
                self._trailing_stop = 0.0
                qty = base_held
                if qty > 0:
                    return [PlaceOrderSignal(self._pair, "SELL", qty, "MARKET", None)]
            return []

        prev_ema_fast = ema_fast_series[-2] if len(ema_fast_series) >= 2 else None
        prev_ema_slow = ema_slow_series[-2] if len(ema_slow_series) >= 2 else None
        golden = cur_ema_fast > cur_ema_slow and (
            prev_ema_fast is None or prev_ema_slow is None or prev_ema_fast <= prev_ema_slow
        )
        if not golden or quote_free <= 0:
            return []

        buy_value = quote_free * self._position_pct
        qty = buy_value / price if price else 0
        if qty <= 0:
            return []
        self._in_position = True
        self._entry_price = price
        self._trailing_stop = price - self._atr_mult * cur_atr
        return [PlaceOrderSignal(self._pair, "BUY", qty, "MARKET", None)]

    def get_managed_pairs(self) -> list[str] | None:
        return [self._pair]
