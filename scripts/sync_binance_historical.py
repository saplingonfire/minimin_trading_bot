#!/usr/bin/env python3
"""Populate local Binance OHLCV dumps for the file-based provider.

Uses the binance-historical-data package. Run manually or via cron; the bot
never calls this script. Data is written under --data-dir so the bot's
BinanceHistoricalFileProvider can read it.

Usage:
  python scripts/sync_binance_historical.py [--data-dir data/binance] [--interval 1h] [--interval 4h]
  python scripts/sync_binance_historical.py --tickers BTC,ETH --update
  python scripts/sync_binance_historical.py   # syncs full Roostoo tradeable universe
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

VALID_INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h")

# Roostoo tradeable universe (from /v3/ticker); symbols as Binance USDT pairs
ROOSTOO_TRADEABLE_UNIVERSE = [
    "1000CHEEMSUSDT",
    "AAVEUSDT",
    "ADAUSDT",
    "APTUSDT",
    "ARBUSDT",
    "ASTERUSDT",
    "AVAXUSDT",
    "AVNTUSDT",
    "BIOUSDT",
    "BMTUSDT",
    "BNBUSDT",
    "BONKUSDT",
    "BTCUSDT",
    "CAKEUSDT",
    "CFXUSDT",
    "CRVUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "EDENUSDT",
    "EIGENUSDT",
    "ENAUSDT",
    "ETHUSDT",
    "FETUSDT",
    "FILUSDT",
    "FLOKIUSDT",
    "FORMUSDT",
    "HBARUSDT",
    "HEMIUSDT",
    "ICPUSDT",
    "LINEAUSDT",
    "LINKUSDT",
    "LISTAUSDT",
    "LTCUSDT",
    "MIRAUSDT",
    "NEARUSDT",
    "ONDOUSDT",
    "OPENUSDT",
    "PAXGUSDT",
    "PENDLEUSDT",
    "PENGUUSDT",
    "PEPEUSDT",
    "PLUMEUSDT",
    "POLUSDT",
    "PUMPUSDT",
    "SUSDT",
    "SEIUSDT",
    "SHIBUSDT",
    "SOLUSDT",
    "SOMIUSDT",
    "STOUSDT",
    "SUIUSDT",
    "TAOUSDT",
    "TONUSDT",
    "TRUMPUSDT",
    "TRXUSDT",
    "TUTUSDT",
    "UNIUSDT",
    "VIRTUALUSDT",
    "WIFUSDT",
    "WLDUSDT",
    "WLFIUSDT",
    "XLMUSDT",
    "XPLUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "ZENUSDT",
]

DEFAULT_TICKERS = ROOSTOO_TRADEABLE_UNIVERSE


def main(args: list[str] | None = None) -> int:
    """Run the sync. Pass args for testing; otherwise sys.argv is used."""
    parsed = _parse_args(args)
    logging.basicConfig(
        level=logging.DEBUG if parsed.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    try:
        from binance_historical_data import BinanceDataDumper
    except ImportError as e:
        logger.error(
            "binance-historical-data is required for this script. Install with: pip install binance-historical-data"
        )
        logger.error("%s", e)
        return 1

    data_dir = Path(parsed.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    tickers = parsed.tickers or DEFAULT_TICKERS
    # Normalize: "BTC,ETH" -> ["BTCUSDT", "ETHUSDT"]
    if isinstance(tickers, str):
        tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    tickers = [t if "USDT" in t else f"{t}USDT" for t in tickers]

    date_start = _parse_date(parsed.date_start) if parsed.date_start else None
    date_end = _parse_date(parsed.date_end) if parsed.date_end else None

    intervals = parsed.interval or ["1h", "4h"]

    for interval in intervals:
        if interval not in VALID_INTERVALS:
            logger.error("Invalid interval %r. Allowed: %s", interval, ", ".join(VALID_INTERVALS))
            return 1

        logger.info("Dumping interval=%s tickers=%s data_dir=%s", interval, tickers, data_dir)
        try:
            dumper = BinanceDataDumper(
                path_dir_where_to_dump=str(data_dir),
                asset_class="spot",
                data_type="klines",
                data_frequency=interval,
            )
            dumper.dump_data(
                tickers=tickers,
                date_start=date_start,
                date_end=date_end,
                is_to_update_existing=parsed.update,
            )
        except Exception as e:
            logger.exception("dump_data failed for interval=%s: %s", interval, e)
            return 1

    logger.info("Sync completed successfully")
    return 0


def _parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Binance historical klines to a local directory for the OHLCV provider.",
    )
    parser.add_argument(
        "--data-dir",
        default="data/binance",
        help="Directory to dump data (default: data/binance). Bot reads from here when BINANCE_DATA_DIR is set.",
    )
    parser.add_argument(
        "--interval",
        action="append",
        dest="interval",
        choices=VALID_INTERVALS,
        help="Candle interval (repeatable). Default: 1h 4h.",
    )
    parser.add_argument(
        "--date-start",
        help="Start date (YYYY-MM-DD). Omit for earliest available.",
    )
    parser.add_argument(
        "--date-end",
        help="End date (YYYY-MM-DD). Omit for today.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update existing files (is_to_update_existing=True).",
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated symbols (e.g. BTC,ETH or BTCUSDT,ETHUSDT). Default: full Roostoo tradeable universe.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser.parse_args(args)


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


if __name__ == "__main__":
    sys.exit(main())
