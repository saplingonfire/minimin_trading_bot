"""Technical indicators: SMA, EMA, ATR, Bollinger Bands, RSI. Operate on sequences of floats."""

from __future__ import annotations


def sma(prices: list[float], period: int) -> list[float]:
    """Simple moving average. Returns [] if len(prices) < period; otherwise one value per price from index period-1 onward."""
    if period < 1 or len(prices) < period:
        return []
    out: list[float] = []
    for i in range(period - 1, len(prices)):
        out.append(sum(prices[i - period + 1 : i + 1]) / period)
    return out


def ema(prices: list[float], period: int) -> list[float]:
    """Exponential moving average. First value is SMA of first `period` prices; then EMA = mult * price + (1-mult) * prev_EMA."""
    if period < 1 or len(prices) < period:
        return []
    mult = 2.0 / (period + 1)
    out: list[float] = [sum(prices[:period]) / period]
    for i in range(period, len(prices)):
        out.append(mult * prices[i] + (1 - mult) * out[-1])
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    """Average True Range. TR = max(high-low, |high-prev_close|, |low-prev_close|); ATR = EMA(TR, period)."""
    n = len(closes)
    if period < 1 or n < period + 1 or len(highs) != n or len(lows) != n:
        return []
    tr_list: list[float] = []
    for i in range(n):
        if i == 0:
            tr = highs[0] - lows[0]
        else:
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        tr_list.append(tr)
    return ema(tr_list, period)


def bollinger_bands(
    prices: list[float],
    period: int = 20,
    mult: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    """Bollinger Bands: middle = SMA(prices, period), upper/lower = middle ± mult * std. Returns (middle, upper, lower)."""
    if period < 1 or len(prices) < period:
        return ([], [], [])
    middle_list: list[float] = []
    upper_list: list[float] = []
    lower_list: list[float] = []
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        mid = sum(window) / period
        variance = sum((x - mid) ** 2 for x in window) / period
        std = variance ** 0.5 if variance > 0 else 0.0
        middle_list.append(mid)
        upper_list.append(mid + mult * std)
        lower_list.append(mid - mult * std)
    return (middle_list, upper_list, lower_list)


def rsi(prices: list[float], period: int = 14) -> list[float]:
    """Relative Strength Index. RS = avg_gain / avg_loss (smoothed); RSI = 100 - 100/(1+RS). First value at index `period`."""
    if period < 1 or len(prices) < period + 1:
        return []
    out: list[float] = []
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(prices)):
        ch = prices[i] - prices[i - 1]
        gains.append(ch if ch > 0 else 0.0)
        losses.append(-ch if ch < 0 else 0.0)
    # First RSI: SMA of first `period` gains/losses
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    if avg_l == 0:
        out.append(100.0)
    else:
        rs = avg_g / avg_l
        out.append(100.0 - 100.0 / (1 + rs))
    # Wilder smoothing: avg_gain = (prev_avg_gain * (period-1) + gain) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            out.append(100.0)
        else:
            rs = avg_g / avg_l
            out.append(100.0 - 100.0 / (1 + rs))
    return out
