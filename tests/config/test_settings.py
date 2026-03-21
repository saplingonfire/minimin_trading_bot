"""Tests for config/settings: test vs live credential switching."""

from pathlib import Path

import pytest

from config.settings import load_settings


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


def _no_yaml_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("BOT_CONFIG_PATH", str(missing))


def test_load_settings_default_append_log_paths_test_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_yaml_config(monkeypatch, tmp_path)
    monkeypatch.setenv("BOT_STRATEGY", "example")
    monkeypatch.setenv("ROOSTOO_TEST_API_KEY", "test_key")
    monkeypatch.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
    monkeypatch.delenv("BOT_LIVE", raising=False)
    monkeypatch.delenv("BOT_TRADES_LOG", raising=False)
    monkeypatch.delenv("BOT_ROOSTOO_API_LOG", raising=False)
    s = load_settings()
    assert s.trades_log_path == "trades-test.log"
    assert s.roostoo_api_log_path == "roostoo-api-test.log"


def test_load_settings_default_append_log_paths_live_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_yaml_config(monkeypatch, tmp_path)
    monkeypatch.setenv("BOT_STRATEGY", "example")
    monkeypatch.setenv("ROOSTOO_API_KEY", "live_key")
    monkeypatch.setenv("ROOSTOO_SECRET_KEY", "live_secret")
    monkeypatch.setenv("ROOSTOO_TEST_API_KEY", "test_key")
    monkeypatch.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
    monkeypatch.delenv("BOT_TRADES_LOG", raising=False)
    monkeypatch.delenv("BOT_ROOSTOO_API_LOG", raising=False)
    s = load_settings(cli_overrides={"live": True})
    assert s.trades_log_path == "trades-live.log"
    assert s.roostoo_api_log_path == "roostoo-api-live.log"


def test_load_settings_append_log_paths_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_yaml_config(monkeypatch, tmp_path)
    monkeypatch.setenv("BOT_STRATEGY", "example")
    monkeypatch.setenv("ROOSTOO_TEST_API_KEY", "test_key")
    monkeypatch.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
    monkeypatch.setenv("BOT_TRADES_LOG", "trades.log")
    monkeypatch.setenv("BOT_ROOSTOO_API_LOG", "roostoo-api.log")
    s = load_settings()
    assert s.trades_log_path == "trades.log"
    assert s.roostoo_api_log_path == "roostoo-api.log"


def test_load_settings_append_log_paths_join_data_log_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("data:\n  log_dir: ./logs/\n", encoding="utf-8")
    monkeypatch.setenv("BOT_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("BOT_STRATEGY", "example")
    monkeypatch.setenv("ROOSTOO_TEST_API_KEY", "test_key")
    monkeypatch.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
    monkeypatch.delenv("BOT_TRADES_LOG", raising=False)
    monkeypatch.delenv("BOT_ROOSTOO_API_LOG", raising=False)
    s = load_settings()
    assert Path(s.trades_log_path).name == "trades-test.log"
    assert "logs" in Path(s.trades_log_path).parts
    assert Path(s.roostoo_api_log_path).name == "roostoo-api-test.log"


def test_load_settings_trades_log_cli_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_yaml_config(monkeypatch, tmp_path)
    monkeypatch.setenv("BOT_STRATEGY", "example")
    monkeypatch.setenv("ROOSTOO_TEST_API_KEY", "test_key")
    monkeypatch.setenv("ROOSTOO_TEST_SECRET_KEY", "test_secret")
    custom = str(tmp_path / "custom-trades.log")
    s = load_settings(cli_overrides={"trades_log_path": custom})
    assert s.trades_log_path == custom
