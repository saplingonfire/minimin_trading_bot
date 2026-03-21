#!/usr/bin/env python3
"""Run a backtest from current config and Binance historical OHLCV; print performance report to stdout."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# Repo root on path so config and bot can be imported
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(_path: str | None = None) -> bool:
        return False

from config.settings import _load_config_yaml
from bot.backtest import run_backtest, compute_metrics, print_report


def _parse_date(s: str) -> int:
    """Parse YYYY-MM-DD to start-of-day UTC timestamp in ms."""
    dt = datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_end_date(s: str) -> int:
    """Parse YYYY-MM-DD to end-of-day UTC timestamp in ms (23:59:59.999)."""
    dt = datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int((dt.timestamp() + 86400 - 0.001) * 1000)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run backtest using config strategy and Binance historical OHLCV; print report to stdout."
    )
    parser.add_argument(
        "--config",
        "-c",
        default=os.environ.get("BOT_CONFIG_PATH", "config.yaml"),
        help="Path to config YAML (default: config.yaml or BOT_CONFIG_PATH)",
    )
    parser.add_argument(
        "--data-dir",
        "-d",
        default=os.environ.get("BINANCE_DATA_DIR", "").strip(),
        help="Path to Binance OHLCV data (default: BINANCE_DATA_DIR)",
    )
    parser.add_argument("--start-date", help="Backtest start date YYYY-MM-DD (optional)")
    parser.add_argument("--end-date", help="Backtest end date YYYY-MM-DD (optional)")
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=None,
        help="Initial equity in USD (default from config backtest or 10000)",
    )
    parser.add_argument("--env-file", default=os.path.join(_repo_root, ".env"), help="Path to .env file (default: repo root .env)")
    parser.add_argument(
        "--exclude-pairs",
        help="Comma-separated pairs to exclude from backtest universe (overrides config and BOT_EXCLUDE_PAIRS)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    load_dotenv(args.env_file)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    yaml_config = _load_config_yaml(args.config)
    strategy_section = (yaml_config or {}).get("strategy") or {}
    backtest_section = (yaml_config or {}).get("backtest") or {}

    strategy_name = (
        os.environ.get("BOT_STRATEGY", "").strip()
        or backtest_section.get("strategy_name")
        or strategy_section.get("_name")
        or "hybrid_trend_cross_sectional"
    )
    strategy_params = dict(strategy_section)
    strategy_params.pop("_name", None)
    # Same as bot load_settings: env BOT_EXCLUDE_PAIRS overrides config exclude_pairs
    exclude_env = (os.environ.get("BOT_EXCLUDE_PAIRS") or "").strip()
    if exclude_env:
        strategy_params["exclude_pairs"] = [p.strip() for p in exclude_env.split(",") if p.strip()]
    if args.exclude_pairs and args.exclude_pairs.strip():
        strategy_params["exclude_pairs"] = [p.strip() for p in args.exclude_pairs.split(",") if p.strip()]

    data_dir = (args.data_dir or backtest_section.get("data_dir") or "").strip()
    if not data_dir:
        logging.error("data-dir is required; set BINANCE_DATA_DIR or use --data-dir")
        return 1

    start_date_ms = None
    if args.start_date or backtest_section.get("start_date"):
        s = args.start_date or backtest_section.get("start_date")
        try:
            start_date_ms = _parse_date(s)
        except ValueError as e:
            logging.error("invalid start-date: %s", e)
            return 1
    end_date_ms = None
    if args.end_date or backtest_section.get("end_date"):
        s = args.end_date or backtest_section.get("end_date")
        try:
            end_date_ms = _parse_end_date(s)
        except ValueError as e:
            logging.error("invalid end-date: %s", e)
            return 1

    initial_balance = args.initial_balance
    if initial_balance is None and backtest_section.get("initial_balance") is not None:
        initial_balance = float(backtest_section["initial_balance"])
    if initial_balance is None:
        initial_balance = 10_000.0

    # Mute strategy INFO logs (e.g. regime=risk-off) during backtest; engine still logs fills.
    logging.getLogger("bot.strategies").setLevel(logging.WARNING)

    try:
        result = run_backtest(
            data_dir,
            strategy_name,
            strategy_params,
            start_date_ms=start_date_ms,
            end_date_ms=end_date_ms,
            initial_balance_usd=initial_balance,
        )
    except ValueError as e:
        logging.error("backtest failed: %s", e)
        return 1
    except Exception:
        logging.exception("backtest error")
        return 1

    metrics = compute_metrics(result.equity_curve, result.trades)
    print_report(metrics, strategy_name=strategy_name, portfolio_breakdown=result.end_portfolio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
