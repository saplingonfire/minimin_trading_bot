#!/usr/bin/env python3
"""Run the trading bot: load config, then runner.run(). Use --strategy and --dry-run."""

import argparse
import logging
import os
import sys

# Repo root on path so config and bot can be imported
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(_path: str | None = None) -> bool:
        return False

from config.settings import load_settings
from bot.runner import run, HYBRID_LIKE_STRATEGIES


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the trading bot")
    parser.add_argument("--strategy", "-s", help="Strategy name (overrides BOT_STRATEGY)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--live", action="store_true", help="Use live credentials (ROOSTOO_API_KEY, ROOSTOO_SECRET_KEY)")
    group.add_argument("--test", action="store_true", help="Use test account credentials (ROOSTOO_TEST_*, default)")
    parser.add_argument("--dry-run", action="store_true", help="Do not place/cancel real orders")
    parser.add_argument("--tick-seconds", type=int, help="Seconds between ticks (overrides BOT_TICK_SECONDS)")
    parser.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--skip-warmup", action="store_true", help="Skip automatic price store warmup from Binance")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    load_dotenv(args.env_file)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    overrides = {}
    if args.strategy is not None:
        overrides["strategy_name"] = args.strategy
    if args.live:
        overrides["live"] = True
    if args.test:
        overrides["live"] = False
    if args.dry_run:
        overrides["dry_run"] = True
    if args.tick_seconds is not None:
        overrides["tick_seconds"] = args.tick_seconds

    try:
        settings = load_settings(overrides)
    except ValueError as e:
        logging.error("config error: %s", e)
        return 1

    if not args.skip_warmup and settings.strategy_name in HYBRID_LIKE_STRATEGIES:
        db_path = (
            settings.price_store_path
            or (settings.strategy_params or {}).get("db_path")
            or "prices.db"
        )
        from scripts.warmup_price_store import warmup_all
        warmup_all(db_path=db_path)

    try:
        run(settings)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.exception("runner failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
