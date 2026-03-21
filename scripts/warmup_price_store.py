#!/usr/bin/env python3
"""Backfill prices.db with daily closes from Binance public klines for all Roostoo-universe symbols.

Makes the hybrid strategies operational from tick 1 (momentum needs ~8 daily closes per symbol,
regime needs ~20 for BTC). Idempotent: skips symbols that already have enough history.

Usage (standalone):
  python scripts/warmup_price_store.py [--db-path prices.db] [--days 30]
  python scripts/warmup_price_store.py --tickers BTC,ETH,SOL

Called automatically by run_bot.py before the runner starts (unless --skip-warmup).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from bot.ohlcv import binance_ticker_to_roostoo_pair
from bot.price_store import PriceStore
from scripts.sync_binance_historical import ROOSTOO_TRADEABLE_UNIVERSE

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MIN_DAYS_THRESHOLD = 20
REQUEST_DELAY = 0.1


def _fetch_binance_daily(symbol_binance: str, limit: int) -> list[tuple[str, int, float]]:
    """Fetch daily klines from Binance public API. Returns (roostoo_pair, ts_ms, close) rows."""
    roostoo_pair = binance_ticker_to_roostoo_pair(symbol_binance)
    if not roostoo_pair:
        return []
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1d&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            klines = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("skip %s: %s", symbol_binance, e)
        return []
    rows: list[tuple[str, int, float]] = []
    for k in klines:
        if len(k) < 5:
            continue
        ts_ms = int(k[0])
        close = float(k[4])
        if close > 0:
            rows.append((roostoo_pair, ts_ms, close))
    return rows


def warmup_all(
    db_path: str = "prices.db",
    symbols: list[str] | None = None,
    limit_days: int = DEFAULT_DAYS,
    min_days_threshold: int = MIN_DAYS_THRESHOLD,
) -> dict[str, int]:
    """Backfill price store with Binance daily closes for all symbols.

    Returns dict of {roostoo_pair: days_inserted}. Skips symbols already having
    >= min_days_threshold days.
    """
    symbols = symbols or list(ROOSTOO_TRADEABLE_UNIVERSE)
    store = PriceStore(db_path)
    results: dict[str, int] = {}
    warmed = 0
    skipped = 0
    failed = 0

    for i, sym in enumerate(symbols):
        roostoo_pair = binance_ticker_to_roostoo_pair(sym)
        if not roostoo_pair:
            failed += 1
            continue
        existing = store.count_days_with_data(roostoo_pair)
        if existing >= min_days_threshold:
            skipped += 1
            continue

        rows = _fetch_binance_daily(sym, limit_days)
        if not rows:
            failed += 1
            continue
        count = store.insert_daily_rows(rows)
        results[roostoo_pair] = count
        warmed += 1
        logger.info("warmup %s: %s days (was %s)", roostoo_pair, count, existing)

        if i < len(symbols) - 1:
            time.sleep(REQUEST_DELAY)

    logger.info("warmup done: warmed=%s skipped=%s failed=%s", warmed, skipped, failed)
    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill prices.db with Binance daily closes for all Roostoo-universe symbols."
    )
    parser.add_argument(
        "--db-path",
        default=os.environ.get("BOT_PRICE_STORE_PATH", "prices.db"),
        help="Path to prices.db (default: BOT_PRICE_STORE_PATH or prices.db)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Number of daily candles to fetch per symbol (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated Binance tickers to warm (default: full Roostoo universe)",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=MIN_DAYS_THRESHOLD,
        help=f"Skip symbol if it already has >= this many days (default: {MIN_DAYS_THRESHOLD})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    symbols = None
    if args.tickers:
        raw = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        symbols = [t if t.endswith("USDT") else f"{t}USDT" for t in raw]

    warmup_all(
        db_path=args.db_path,
        symbols=symbols,
        limit_days=args.days,
        min_days_threshold=args.min_days,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
