"""Tests for config/settings: test vs live credential switching."""

import os

import pytest

from config.settings import BotSettings, load_settings


def test_load_settings_uses_test_credentials_by_default() -> None:
    with pytest.MonkeyPatch.context() as m:
        m.setenv("BOT_STRATEGY", "example")
        m.setenv("ROOSTOO_TEST_API_KEY", "test_key")
        m.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
        m.delenv("BOT_LIVE", raising=False)
        s = load_settings()
    assert s.live is False
    assert s.api_key == "test_key"
    assert s.secret_key == "test_secret"


def test_load_settings_uses_live_credentials_when_live_true() -> None:
    with pytest.MonkeyPatch.context() as m:
        m.setenv("BOT_STRATEGY", "example")
        m.setenv("ROOSTOO_API_KEY", "live_key")
        m.setenv("ROOSTOO_SECRET_KEY", "live_secret")
        m.setenv("ROOSTOO_TEST_API_KEY", "test_key")
        m.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
        s = load_settings(cli_overrides={"live": True})
    assert s.live is True
    assert s.api_key == "live_key"
    assert s.secret_key == "live_secret"


def test_load_settings_uses_test_credentials_when_live_false_override() -> None:
    with pytest.MonkeyPatch.context() as m:
        m.setenv("BOT_STRATEGY", "example")
        m.setenv("ROOSTOO_TEST_API_KEY", "test_key")
        m.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
        s = load_settings(cli_overrides={"live": False})
    assert s.live is False
    assert s.api_key == "test_key"
    assert s.secret_key == "test_secret"


def test_load_settings_requires_credentials_for_chosen_mode() -> None:
    with pytest.MonkeyPatch.context() as m:
        m.setenv("BOT_STRATEGY", "example")
        m.delenv("ROOSTOO_API_KEY", raising=False)
        m.delenv("ROOSTOO_SECRET_KEY", raising=False)
        m.delenv("ROOSTOO_TEST_API_KEY", raising=False)
        m.delenv("ROOSTOO_TEST_SECRET_KEY", raising=False)
        with pytest.raises(ValueError, match="test \\(ROOSTOO_TEST"):
            load_settings(cli_overrides={"live": False})
        with pytest.raises(ValueError, match="live \\(ROOSTOO_API_KEY"):
            load_settings(cli_overrides={"live": True})
