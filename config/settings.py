"""Centralized config: env vars + CLI overrides, validation, BotSettings."""

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BotSettings:
    """Validated bot settings (secrets and strategy config)."""

    api_key: str
    secret_key: str
    base_url: str
    live: bool  # True = live credentials, False = test account credentials
    strategy_name: str
    strategy_params: dict[str, Any]
    tick_seconds: int
    dry_run: bool
    cancel_orders_on_stop: bool
    max_pending_orders: int | None
    max_order_notional: float | None


def _parse_bool(s: str | None) -> bool:
    if s is None:
        return False
    return s.strip().lower() in ("1", "true", "yes")


def _parse_int(s: str | None, default: int) -> int:
    if s is None:
        return default
    try:
        return int(s.strip())
    except ValueError:
        return default


def _parse_float(s: str | None) -> float | None:
    if s is None or s.strip() == "":
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


def load_settings(cli_overrides: dict[str, Any] | None = None) -> BotSettings:
    """Load settings from env, apply CLI overrides, validate. Never log secrets."""
    overrides = cli_overrides or {}

    live = overrides.get("live")
    if live is None:
        live = _parse_bool(os.environ.get("BOT_LIVE"))

    if live:
        api_key = overrides.get("api_key") or os.environ.get("ROOSTOO_API_KEY", "")
        secret_key = overrides.get("secret_key") or os.environ.get("ROOSTOO_SECRET_KEY", "")
        base_url = (
            overrides.get("base_url")
            or os.environ.get("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com")
        ).rstrip("/")
    else:
        api_key = overrides.get("api_key") or os.environ.get("ROOSTOO_TEST_API_KEY", "")
        secret_key = overrides.get("secret_key") or os.environ.get("ROOSTOO_TEST_SECRET_KEY", "")
        base_url = (
            overrides.get("base_url")
            or os.environ.get("ROOSTOO_TEST_BASE_URL", "https://mock-api.roostoo.com")
        ).rstrip("/")

    strategy_name = (
        overrides.get("strategy_name") or os.environ.get("BOT_STRATEGY", "")
    ).strip()
    if not strategy_name:
        raise ValueError("BOT_STRATEGY is required")

    params_raw = overrides.get("strategy_params") or os.environ.get("BOT_STRATEGY_PARAMS", "{}")
    if isinstance(params_raw, dict):
        strategy_params = params_raw
    else:
        try:
            strategy_params = json.loads(params_raw) if params_raw else {}
        except json.JSONDecodeError as e:
            raise ValueError(f"BOT_STRATEGY_PARAMS must be valid JSON: {e}") from e
    if not isinstance(strategy_params, dict):
        raise ValueError("BOT_STRATEGY_PARAMS must be a JSON object")

    tick_seconds = overrides.get("tick_seconds")
    if tick_seconds is None:
        tick_seconds = _parse_int(os.environ.get("BOT_TICK_SECONDS"), 30)
    if tick_seconds < 1:
        tick_seconds = 30

    dry_run = overrides.get("dry_run")
    if dry_run is None:
        dry_run = _parse_bool(os.environ.get("BOT_DRY_RUN"))

    cancel_orders_on_stop = overrides.get("cancel_orders_on_stop")
    if cancel_orders_on_stop is None:
        cancel_orders_on_stop = _parse_bool(os.environ.get("BOT_CANCEL_ORDERS_ON_STOP"))

    max_pending = overrides.get("max_pending_orders")
    if max_pending is None:
        raw = os.environ.get("BOT_MAX_PENDING_ORDERS")
        max_pending = int(raw) if raw and raw.strip().isdigit() else None
    max_notional = overrides.get("max_order_notional")
    if max_notional is None:
        max_notional = _parse_float(os.environ.get("BOT_MAX_ORDER_NOTIONAL"))

    if not api_key or not secret_key:
        which = "live (ROOSTOO_API_KEY, ROOSTOO_SECRET_KEY)" if live else "test (ROOSTOO_TEST_API_KEY, ROOSTOO_TEST_SECRET_KEY)"
        raise ValueError(f"Credentials are required for {which}; set in env or .env")

    return BotSettings(
        api_key=api_key or "",
        secret_key=secret_key or "",
        base_url=base_url,
        live=live,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        tick_seconds=tick_seconds,
        dry_run=dry_run,
        cancel_orders_on_stop=cancel_orders_on_stop,
        max_pending_orders=max_pending,
        max_order_notional=max_notional,
    )
