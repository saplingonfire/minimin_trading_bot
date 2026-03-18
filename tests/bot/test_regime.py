"""Tests for bot/regime: BTC MA20 regime with 2-day confirmation."""

import pytest

from bot.regime import REGIME_RISK_OFF, REGIME_RISK_ON, compute_regime


def test_risk_on_when_above_ma_and_already_risk_on() -> None:
    # 20 closes, last well above MA20
    closes = [100.0 + i for i in range(20)]
    regime, candidate = compute_regime(closes, 20, REGIME_RISK_ON, None)
    assert regime == REGIME_RISK_ON
    assert candidate is None


def test_risk_off_when_below_ma() -> None:
    # Last close below MA (e.g. declining then drop)
    closes = [100.0 - i for i in range(20)]
    regime, candidate = compute_regime(closes, 20, REGIME_RISK_ON, None)
    assert regime == REGIME_RISK_OFF
    assert candidate is None


def test_first_day_above_sets_candidate_not_regime() -> None:
    # Start risk-off; one day above MA -> candidate risk-on, regime stays risk-off
    # e.g. last 20 days: mostly 90s, last day 110; MA20 ~95, so above
    closes = [90.0] * 19 + [110.0]
    regime, candidate = compute_regime(closes, 20, REGIME_RISK_OFF, None)
    assert regime == REGIME_RISK_OFF
    assert candidate == REGIME_RISK_ON


def test_second_day_above_confirms_risk_on() -> None:
    # Was risk-off with candidate risk-on; another day above -> confirm risk-on
    closes = [90.0] * 18 + [110.0, 112.0]
    regime, candidate = compute_regime(closes, 20, REGIME_RISK_OFF, REGIME_RISK_ON)
    assert regime == REGIME_RISK_ON
    assert candidate is None


def test_insufficient_data_returns_unchanged() -> None:
    closes = [100.0, 101.0]
    regime, candidate = compute_regime(closes, 20, REGIME_RISK_ON, None)
    assert regime == REGIME_RISK_ON
    assert candidate is None
