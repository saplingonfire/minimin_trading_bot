"""Tests for bot/risk: drawdown ladder and kill switch."""

import time

import pytest

from bot.risk import (
    get_drawdown_exposure,
    kill_switch_check,
    should_restore_exposure,
)


def test_drawdown_normal_exposure_unchanged() -> None:
    exposure, force = get_drawdown_exposure(100, 100, 0.85)
    assert exposure == 0.85
    assert force is False


def test_drawdown_soft_05_reduces_exposure() -> None:
    # -5% drawdown -> 0.70
    exposure, force = get_drawdown_exposure(95, 100, 0.85)
    assert exposure == 0.70
    assert force is False


def test_drawdown_soft_10_half_exposure() -> None:
    exposure, force = get_drawdown_exposure(89, 100, 0.85)
    assert exposure == 0.50
    assert force is False


def test_drawdown_hard_15_force_risk_off() -> None:
    exposure, force = get_drawdown_exposure(84, 100, 0.85)
    assert exposure == 0.0
    assert force is True


def test_should_restore_at_95_percent() -> None:
    assert should_restore_exposure(95, 100) is True
    assert should_restore_exposure(94, 100) is False


def test_kill_switch_consecutive_errors() -> None:
    halt, force = kill_switch_check(5, 0, None, max_consecutive_errors=5)
    assert halt is True
    assert force is True


def test_kill_switch_btc_move_force_cash_only() -> None:
    # Use current time so clock-drift check does not trigger (server_time_ms=1e12 would fail drift).
    now_ms = int(time.time() * 1000)
    halt, force = kill_switch_check(0, now_ms, 0.50, btc_daily_move_kill=0.40)
    assert halt is False
    assert force is True


def test_kill_switch_no_trigger() -> None:
    now_ms = int(time.time() * 1000)
    halt, force = kill_switch_check(0, now_ms, 0.10, btc_daily_move_kill=0.40)
    assert halt is False
    assert force is False
