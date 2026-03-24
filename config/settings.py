"""Centralized config: env vars + optional YAML + CLI overrides, validation, BotSettings."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
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
    # Hybrid / execution pacing (optional)
    price_store_path: str | None = None
    max_orders_per_cycle: int | None = None
    order_spacing_sec: float | None = None
    stale_order_timeout_sec: float | None = None
    retry_delays: tuple[float, ...] | None = None
    retry_statuses: tuple[int, ...] | None = None
    warmup_min_btc_days: int = 20
    warmup_backfill_days: int = 30
    # Log files (append-only; bot only); defaults match test account when unset in load_settings
    trades_log_path: str = "trades-test.log"
    roostoo_api_log_path: str = "roostoo-api-test.log"


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


def _resolve_append_log_path(
    override: str | None,
    env_val: str | None,
    live: bool,
    log_dir: str | None,
    stem: str,
) -> str:
    """CLI override, then env, else trades-{live|test}.log under optional log_dir."""
    if override and str(override).strip():
        return str(Path(str(override).strip()))
    if env_val is not None and env_val.strip():
        return env_val.strip()
    suffix = "live" if live else "test"
    basename = f"{stem}-{suffix}.log"
    if log_dir and log_dir.strip():
        return str(Path(log_dir.strip()) / basename)
    return basename


def _load_config_yaml(path: str | Path = "config.yaml") -> dict[str, Any]:
    """Load optional config.yaml; return empty dict if missing or invalid."""
    try:
        import yaml
    except ImportError:
        return {}
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {}
    try:
        with p.open() as f:
            data = yaml.safe_load(f)
        return data or {}
    except Exception:
        return {}


def load_settings(cli_overrides: dict[str, Any] | None = None) -> BotSettings:
    """Load settings from env, optional config.yaml, and CLI overrides. Validate. Never log secrets."""
    overrides = dict(cli_overrides) if cli_overrides else {}
    yaml_config = _load_config_yaml(os.environ.get("BOT_CONFIG_PATH", "config.yaml"))
    yaml_strategy: dict[str, Any] = {}
    fees_yaml: dict[str, Any] = {}
    log_dir_from_yaml: str | None = None
    if yaml_config:
        yaml_strategy = yaml_config.get("strategy") or {}
        execution_yaml = yaml_config.get("execution") or {}
        data_yaml = yaml_config.get("data") or {}
        fees_yaml = (execution_yaml.get("fees") or {}) if execution_yaml else {}
        if execution_yaml:
            overrides.setdefault("tick_seconds", execution_yaml.get("cycle_sec"))
            overrides.setdefault("max_orders_per_cycle", execution_yaml.get("max_orders_per_cycle"))
            overrides.setdefault("order_spacing_sec", execution_yaml.get("order_spacing_sec"))
        if data_yaml:
            overrides.setdefault("price_store_path", data_yaml.get("db_path"))
            raw_ld = data_yaml.get("log_dir")
            if isinstance(raw_ld, str) and raw_ld.strip():
                log_dir_from_yaml = raw_ld.strip()

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
        from_env = params_raw
    else:
        try:
            from_env = json.loads(params_raw) if params_raw else {}
        except json.JSONDecodeError as e:
            raise ValueError(f"BOT_STRATEGY_PARAMS must be valid JSON: {e}") from e
    if not isinstance(from_env, dict):
        raise ValueError("BOT_STRATEGY_PARAMS must be a JSON object")
    strategy_params = {**yaml_strategy, **from_env}
    # Env BOT_EXCLUDE_PAIRS overrides strategy.exclude_pairs (comma-separated)
    exclude_env = (os.environ.get("BOT_EXCLUDE_PAIRS") or "").strip()
    if exclude_env:
        strategy_params["exclude_pairs"] = [p.strip() for p in exclude_env.split(",") if p.strip()]

    # Inject fee rates (bps -> fractional) from execution.fees into strategy_params
    strategy_params.setdefault(
        "fee_market_rate", int(fees_yaml.get("market_bps", 10)) / 10_000,
    )
    strategy_params.setdefault(
        "fee_limit_rate", int(fees_yaml.get("limit_bps", 5)) / 10_000,
    )

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

    price_store_path = overrides.get("price_store_path")
    if price_store_path is None:
        price_store_path = os.environ.get("BOT_PRICE_STORE_PATH", "").strip() or None
    max_orders_per_cycle = overrides.get("max_orders_per_cycle")
    if max_orders_per_cycle is None:
        raw = os.environ.get("BOT_MAX_ORDERS_PER_CYCLE", "").strip()
        max_orders_per_cycle = int(raw) if raw.isdigit() else None
    order_spacing_sec = overrides.get("order_spacing_sec")
    if order_spacing_sec is None:
        order_spacing_sec = _parse_float(os.environ.get("BOT_ORDER_SPACING_SEC"))

    stale_order_timeout_sec = overrides.get("stale_order_timeout_sec")
    if stale_order_timeout_sec is None:
        execution_yaml_2 = yaml_config.get("execution") or {}
        raw_stale = execution_yaml_2.get("stale_order_timeout_sec")
        if raw_stale is not None:
            stale_order_timeout_sec = float(raw_stale)

    execution_yaml_full = yaml_config.get("execution") or {}
    raw_retry_delays = execution_yaml_full.get("retry_delays")
    retry_delays = tuple(float(d) for d in raw_retry_delays) if raw_retry_delays else None
    raw_retry_statuses = execution_yaml_full.get("retry_statuses")
    retry_statuses = tuple(int(s) for s in raw_retry_statuses) if raw_retry_statuses else None
    warmup_min_btc_days = int(execution_yaml_full.get("warmup_min_btc_days", 20))
    warmup_backfill_days = int(execution_yaml_full.get("warmup_backfill_days", 30))

    if not api_key or not secret_key:
        which = "live (ROOSTOO_API_KEY, ROOSTOO_SECRET_KEY)" if live else "test (ROOSTOO_TEST_API_KEY, ROOSTOO_TEST_SECRET_KEY)"
        raise ValueError(f"Credentials are required for {which}; set in env or .env")

    trades_log_path = _resolve_append_log_path(
        overrides.get("trades_log_path"),
        os.environ.get("BOT_TRADES_LOG"),
        live,
        log_dir_from_yaml,
        "trades",
    )
    roostoo_api_log_path = _resolve_append_log_path(
        overrides.get("roostoo_api_log_path"),
        os.environ.get("BOT_ROOSTOO_API_LOG"),
        live,
        log_dir_from_yaml,
        "roostoo-api",
    )

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
        price_store_path=price_store_path,
        max_orders_per_cycle=max_orders_per_cycle,
        order_spacing_sec=order_spacing_sec,
        stale_order_timeout_sec=stale_order_timeout_sec,
        retry_delays=retry_delays,
        retry_statuses=retry_statuses,
        warmup_min_btc_days=warmup_min_btc_days,
        warmup_backfill_days=warmup_backfill_days,
        trades_log_path=trades_log_path,
        roostoo_api_log_path=roostoo_api_log_path,
    )
