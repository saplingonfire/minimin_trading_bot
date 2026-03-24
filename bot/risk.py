"""Risk controls: drawdown ladder and kill switch."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Drawdown ladder: reduce exposure or force cash (tightened for short competition windows)
DRAWDOWN_SOFT_03 = -0.03
DRAWDOWN_SOFT_07 = -0.07
DRAWDOWN_HARD_10 = -0.10
RECOVERY_RATIO = 0.95  # Restore exposure when portfolio >= 95% of peak


def get_drawdown_exposure(
    portfolio_value: float,
    peak_value: float,
    current_target_exposure: float,
    *,
    soft_03: float = DRAWDOWN_SOFT_03,
    soft_07: float = DRAWDOWN_SOFT_07,
    hard_10: float = DRAWDOWN_HARD_10,
) -> tuple[float, bool]:
    """Return (target_exposure, force_risk_off) from drawdown ladder.

    If peak_value <= 0 or portfolio_value <= 0, returns (current_target_exposure, False).
    """
    if peak_value <= 0 or portfolio_value <= 0:
        return (current_target_exposure, False)
    drawdown = (portfolio_value - peak_value) / peak_value
    if drawdown <= hard_10:
        return (0.0, True)
    if drawdown <= soft_07:
        return (0.30, False)
    if drawdown <= soft_03:
        return (0.60, False)
    return (current_target_exposure, False)


def should_restore_exposure(portfolio_value: float, peak_value: float) -> bool:
    """True if portfolio has recovered to RECOVERY_RATIO of peak."""
    if peak_value <= 0:
        return True
    return portfolio_value >= peak_value * RECOVERY_RATIO


def kill_switch_check(
    consecutive_api_errors: int,
    server_time_ms: int,
    btc_change_pct: float | None,
    *,
    max_consecutive_errors: int = 5,
    max_drift_ms: int = 60_000,
    btc_daily_move_kill: float = 0.15,
) -> tuple[bool, bool]:
    """Returns (halt_bot, force_risk_off).

    halt_bot: exit process (e.g. API failures, clock drift).
    force_risk_off: go to cash but keep running (e.g. BTC 15% move).
    """
    if consecutive_api_errors >= max_consecutive_errors:
        logger.critical("KILL SWITCH: consecutive API errors %s >= %s", consecutive_api_errors, max_consecutive_errors)
        return (True, True)
    local_ms = int(time.time() * 1000)
    drift = abs(server_time_ms - local_ms)
    if drift > max_drift_ms:
        logger.critical("KILL SWITCH: clock drift %s ms > %s ms", drift, max_drift_ms)
        return (True, True)
    if btc_change_pct is not None and abs(btc_change_pct) > btc_daily_move_kill:
        logger.warning("BTC daily move %.2f%% > %.0f%%. Forcing risk-off.", btc_change_pct * 100, btc_daily_move_kill * 100)
        return (False, True)
    return (False, False)
