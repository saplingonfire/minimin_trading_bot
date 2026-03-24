"""Risk controls: drawdown ladder and kill switch."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DRAWDOWN_SOFT_1_DEFAULT = -0.03
DRAWDOWN_SOFT_2_DEFAULT = -0.07
DRAWDOWN_HARD_DEFAULT = -0.10
DRAWDOWN_SOFT_1_EXPOSURE_DEFAULT = 0.60
DRAWDOWN_SOFT_2_EXPOSURE_DEFAULT = 0.30
RECOVERY_RATIO_DEFAULT = 0.95


@dataclass(frozen=True)
class DrawdownConfig:
    """Configurable drawdown ladder thresholds and exposure levels."""

    soft_1: float = DRAWDOWN_SOFT_1_DEFAULT
    soft_1_exposure: float = DRAWDOWN_SOFT_1_EXPOSURE_DEFAULT
    soft_2: float = DRAWDOWN_SOFT_2_DEFAULT
    soft_2_exposure: float = DRAWDOWN_SOFT_2_EXPOSURE_DEFAULT
    hard: float = DRAWDOWN_HARD_DEFAULT
    recovery_ratio: float = RECOVERY_RATIO_DEFAULT

    @classmethod
    def from_config(cls, risk_config: dict) -> DrawdownConfig:
        return cls(
            soft_1=float(risk_config.get("drawdown_soft_1", DRAWDOWN_SOFT_1_DEFAULT)),
            soft_1_exposure=float(risk_config.get("drawdown_soft_1_exposure", DRAWDOWN_SOFT_1_EXPOSURE_DEFAULT)),
            soft_2=float(risk_config.get("drawdown_soft_2", DRAWDOWN_SOFT_2_DEFAULT)),
            soft_2_exposure=float(risk_config.get("drawdown_soft_2_exposure", DRAWDOWN_SOFT_2_EXPOSURE_DEFAULT)),
            hard=float(risk_config.get("drawdown_hard", DRAWDOWN_HARD_DEFAULT)),
            recovery_ratio=float(risk_config.get("recovery_ratio", RECOVERY_RATIO_DEFAULT)),
        )


def get_drawdown_exposure(
    portfolio_value: float,
    peak_value: float,
    current_target_exposure: float,
    *,
    dd: DrawdownConfig | None = None,
) -> tuple[float, bool]:
    """Return (target_exposure, force_risk_off) from drawdown ladder.

    If peak_value <= 0 or portfolio_value <= 0, returns (current_target_exposure, False).
    """
    if dd is None:
        dd = DrawdownConfig()
    if peak_value <= 0 or portfolio_value <= 0:
        return (current_target_exposure, False)
    drawdown = (portfolio_value - peak_value) / peak_value
    if drawdown <= dd.hard:
        return (0.0, True)
    if drawdown <= dd.soft_2:
        return (dd.soft_2_exposure, False)
    if drawdown <= dd.soft_1:
        return (dd.soft_1_exposure, False)
    return (current_target_exposure, False)


def should_restore_exposure(
    portfolio_value: float,
    peak_value: float,
    recovery_ratio: float = RECOVERY_RATIO_DEFAULT,
) -> bool:
    """True if portfolio has recovered to recovery_ratio of peak."""
    if peak_value <= 0:
        return True
    return portfolio_value >= peak_value * recovery_ratio


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
