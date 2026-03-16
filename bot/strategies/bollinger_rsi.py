"""Bollinger Bands + RSI: oversold entry (price < lower BB, RSI < 30), 4H 200 SMA regime filter, volatility-scaled size."""

from __future__ import annotations

import logging
from typing import Any

from bot.base import PlaceOrderSignal, Signal, Strategy, TradingContext
from bot.indicators import atr, bollinger_bands, rsi, sma
from bot.ohlcv import OHLCVUnavailableError

logger = logging.getLogger(__name__)


def _get_balance_free(balance: dict[str, Any], asset: str) -> float:
    entry = balance.get(asset) or balance.get(asset.upper())
    if not isinstance(entry, dict):
        return 0.0
    return float(entry.get("Free", entry.get("free", 0)) or 0)


def _get_price(ticker: dict[str, Any], pair: str) -> float | None:
    row = ticker.get(pair) or ticker
    if not isinstance(row, dict):
        return None
    return float(row.get("LastPrice", row.get("lastPrice", 0)) or 0) or None


def _parse_pair(pair: str) -> tuple[str, str]:
    if "/" in pair:
        a, b = pair.strip().upper().split("/", 1)
        return (a.strip(), b.strip())
    return (pair.strip().upper(), "USD")


class BollingerRSIStrategy(Strategy):
    """Entry: price < lower BB and RSI < 30; regime: 4H 200 SMA; position size = risk_amount / ATR."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._pair = str(config.get("pair", "ETH/USD"))
        self._bb_period = int(config.get("bb_period", 20))
        self._bb_std = float(config.get("bb_std", 2.0))
        self._rsi_period = int(config.get("rsi_period", 14))
        self._rsi_oversold = int(config.get("rsi_oversold", 30))
        self._regime_ma_period = int(config.get("regime_ma_period", 200))
        self._regime_interval = str(config.get("regime_interval", "4h"))
        self._entry_interval = str(config.get("entry_interval", "1h"))
        self._risk_amount = float(config.get("risk_amount", 100.0))
        self._atr_period = int(config.get("atr_period", 14))
        self._rsi_overbought = int(config.get("rsi_overbought", 70))
        self._in_position = False

    def on_start(self) -> None:
        self._in_position = False

    def next(self, context: TradingContext) -> list[Signal]:
        if context.ohlcv_provider is None:
            return []

        try:
            regime_candles = context.ohlcv_provider.get_klines(
                self._pair, self._regime_interval, self._regime_ma_period + 5
            )
            entry_candles = context.ohlcv_provider.get_klines(
                self._pair, self._entry_interval, self._bb_period + self._rsi_period + 10
            )
        except OHLCVUnavailableError:
            return []

        if len(regime_candles) < self._regime_ma_period or len(entry_candles) < self._bb_period + self._rsi_period:
            return []

        regime_closes = [c["close"] for c in regime_candles]
        sma_200 = sma(regime_closes, self._regime_ma_period)
        if not sma_200:
            return []
        current_4h_close = regime_closes[-1]
        ma_200 = sma_200[-1]
        if current_4h_close < ma_200:
            return []

        closes = [c["close"] for c in entry_candles]
        highs = [c["high"] for c in entry_candles]
        lows = [c["low"] for c in entry_candles]
        mid, upper, lower = bollinger_bands(closes, self._bb_period, self._bb_std)
        rsi_series = rsi(closes, self._rsi_period)
        atr_series = atr(highs, lows, closes, self._atr_period)
        if not mid or not lower or not rsi_series or not atr_series:
            return []

        price = _get_price(context.ticker, self._pair)
        if price is None or price <= 0:
            return []

        cur_lower = lower[-1]
        cur_rsi = rsi_series[-1]
        cur_atr = atr_series[-1]
        base, quote = _parse_pair(self._pair)
        base_held = _get_balance_free(context.balance, base)
        if base_held > 0:
            self._in_position = True

        if self._in_position:
            self._in_position = True
            if cur_rsi >= self._rsi_overbought or (upper and price >= upper[-1]):
                self._in_position = False
                if base_held > 0:
                    return [PlaceOrderSignal(self._pair, "SELL", base_held, "MARKET", None)]
            return []

        if price >= cur_lower or cur_rsi >= self._rsi_oversold:
            return []

        if cur_atr <= 0:
            return []
        size_quote = self._risk_amount
        qty = size_quote / cur_atr
        if price > 0:
            qty = size_quote / (cur_atr * price)
        if qty <= 0:
            return []
        self._in_position = True
        return [PlaceOrderSignal(self._pair, "BUY", qty, "MARKET", None)]

    def get_managed_pairs(self) -> list[str] | None:
        return [self._pair]
