"""BTC trend regime filter: risk-on when price > MA(20), with 2-day confirmation to avoid whipsaw."""

from __future__ import annotations

from bot.indicators import sma

REGIME_RISK_ON = "risk-on"
REGIME_RISK_OFF = "risk-off"


def compute_regime(
    btc_daily_closes: list[float],
    ma_window: int,
    current_regime: str,
    regime_candidate: str | None,
) -> tuple[str, str | None]:
    """Compute regime from BTC daily closes and optional previous state.

    Regime is risk-on when latest close > MA(ma_window). A single day above MA
    does not flip from risk-off to risk-on; we require two consecutive daily
    evaluations above MA (2-day confirmation). Whipsaw: one day above then
    below leaves us in risk-off.

    Args:
        btc_daily_closes: Oldest-first list of daily close prices.
        ma_window: Number of days for the moving average (e.g. 20).
        current_regime: Current state: REGIME_RISK_ON or REGIME_RISK_OFF.
        regime_candidate: If 'risk-on', previous evaluation was above MA (waiting for confirm).

    Returns:
        (new_regime, new_regime_candidate).
    """
    if ma_window < 1 or len(btc_daily_closes) < ma_window:
        return (current_regime, regime_candidate)
    ma_vals = sma(btc_daily_closes, ma_window)
    if not ma_vals:
        return (current_regime, regime_candidate)
    last_close = btc_daily_closes[-1]
    last_ma = ma_vals[-1]
    above = last_close > last_ma

    if above and current_regime == REGIME_RISK_ON:
        return (REGIME_RISK_ON, None)
    if above and current_regime == REGIME_RISK_OFF:
        if regime_candidate == REGIME_RISK_ON:
            return (REGIME_RISK_ON, None)
        return (REGIME_RISK_OFF, REGIME_RISK_ON)
    return (REGIME_RISK_OFF, None)
