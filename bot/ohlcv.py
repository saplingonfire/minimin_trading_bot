"""OHLCV data layer: typed candle, provider protocol, and file-based reader.

Strategies use OHLCVProvider.get_klines(pair, interval, limit). The file-based
implementation reads from local CSV dumps produced by scripts/sync_binance_historical.py
(binance-historical-data package). Reader uses stdlib only; no runtime dependency
on binance-historical-data.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict

logger = logging.getLogger(__name__)

# Path layout under data_dir: spot/daily/klines/{ticker}/{interval}/*.csv
# Matches Binance Vision and binance-historical-data dump structure.
_SUBDIR_KLINES = ("data", "spot", "daily", "klines")


def discover_tradeable_pairs(data_dir: str | Path) -> list[str]:
    """Discover Roostoo pairs (e.g. BTC/USD) that have OHLCV data under data_dir.

    Scans the same path layout as BinanceHistoricalFileProvider (data/spot/daily/klines
    and spot/daily/klines). Each subdir of klines/ is a Binance ticker; mapped to BASE/USD.
    """
    base = Path(data_dir).joinpath(*_SUBDIR_KLINES)
    if not base.exists() or not base.is_dir():
        base = Path(data_dir).joinpath("spot", "daily", "klines")
    if not base.exists() or not base.is_dir():
        return []
    pairs: list[str] = []
    for path in base.iterdir():
        if path.is_dir():
            roostoo = binance_ticker_to_roostoo_pair(path.name)
            if roostoo:
                pairs.append(roostoo)
    return sorted(pairs)

# CSV columns produced by Binance klines (binance-historical-data uses these names).
_COL_OPEN_TIME = "Open time"
_COL_OPEN = "Open"
_COL_HIGH = "High"
_COL_LOW = "Low"
_COL_CLOSE = "Close"
_COL_VOLUME = "Volume"


def _get_col(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and row[k] != "":
            return row[k]
    return "0"


class OHLCVCandle(TypedDict):
    """Single OHLCV candle; canonical type for strategies and indicators."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class OHLCVUnavailableError(Exception):
    """Raised when OHLCV data cannot be read (misconfiguration or parse error).

    Implementations may return [] for 'no data' or raise this for hard failures.
    Strategies should handle both (no-op or log).
    """

    pass


class OHLCVProvider(Protocol):
    """Protocol for OHLCV data. Implementations return list[OHLCVCandle]."""

    def get_klines(self, pair: str, interval: str, limit: int) -> list[OHLCVCandle]:
        """Return the last `limit` candles for pair and interval (chronological order).

        Implementations may return [] when no data exists; they may raise
        OHLCVUnavailableError for misconfiguration or parse errors.
        """
        ...


def roostoo_pair_to_binance_ticker(pair: str) -> str:
    """Convert Roostoo pair (e.g. BTC/USD) to Binance spot ticker (e.g. BTCUSDT).

    Handles only /USD quote for now.
    """
    pair = (pair or "").strip().upper()
    if not pair:
        return ""
    if "/" in pair:
        base, quote = pair.split("/", 1)
        base = base.strip()
        quote = quote.strip()
        if quote == "USD":
            return f"{base}USDT"
        return f"{base}{quote}"
    return f"{pair}USDT"


def binance_ticker_to_roostoo_pair(ticker: str) -> str:
    """Convert Binance spot ticker (e.g. BTCUSDT) to Roostoo pair (e.g. BTC/USD)."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return ""
    if ticker.endswith("USDT"):
        return f"{ticker[:-4]}/USD"
    return f"{ticker}/USD"


def _resample_to_daily(candles: list[OHLCVCandle]) -> list[OHLCVCandle]:
    """Resample 1h (or sub-daily) candles to daily: open=first, high=max, low=min, close=last, volume=sum."""
    if not candles:
        return []
    from collections import defaultdict

    # Group by day (UTC): time is ms, day key = time // (24 * 3600 * 1000)
    by_day: dict[int, list[OHLCVCandle]] = defaultdict(list)
    for c in candles:
        day_key = c["time"] // (24 * 3600 * 1000)
        by_day[day_key].append(c)

    result: list[OHLCVCandle] = []
    for day_key in sorted(by_day.keys()):
        group = by_day[day_key]
        group.sort(key=lambda x: x["time"])
        first, last = group[0], group[-1]
        result.append(
            OHLCVCandle(
                time=first["time"],
                open=first["open"],
                high=max(c["high"] for c in group),
                low=min(c["low"] for c in group),
                close=last["close"],
                volume=sum(c["volume"] for c in group),
            )
        )
    return result


class BinanceHistoricalFileProvider:
    """Reads OHLCV from local CSV dumps produced by binance-historical-data.

    Constructor accepts data_dir (and optional logger). Does not read os.environ;
    the runner injects data_dir. Missing or empty data returns []; misconfiguration
    or parse errors raise OHLCVUnavailableError after logging WARNING.
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        logger_instance: logging.Logger | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._log = logger_instance or logger

    def get_klines(self, pair: str, interval: str, limit: int) -> list[OHLCVCandle]:
        if limit <= 0:
            return []

        ticker = roostoo_pair_to_binance_ticker(pair)
        if not ticker:
            self._log.debug("empty pair, returning no klines")
            return []

        if not self._data_dir.exists() or not self._data_dir.is_dir():
            self._log.warning("ohlcv data_dir does not exist or is not a directory: %s", self._data_dir)
            raise OHLCVUnavailableError(f"data_dir not a directory: {self._data_dir}")

        if interval == "1d":
            # Resample from 1h
            raw = self._read_csv_klines(ticker, "1h")
            candles = _resample_to_daily(raw)
        else:
            candles = self._read_csv_klines(ticker, interval)

        if not candles:
            self._log.debug("no files or no rows for pair=%s interval=%s", pair, interval)
            return []

        candles.sort(key=lambda c: c["time"])
        out = candles[-limit:]
        self._log.debug("get_klines pair=%s interval=%s limit=%s returned %s", pair, interval, limit, len(out))
        return out

    def get_daily_klines_range(
        self, pair: str, end_time_ms: int | None = None
    ) -> list[OHLCVCandle]:
        """Return all daily candles for pair with time <= end_time_ms (or all if end_time_ms is None).

        Used by backtest to get as-of-date series. Uses same CSV layout and resampling as get_klines.
        """
        ticker = roostoo_pair_to_binance_ticker(pair)
        if not ticker:
            self._log.debug("empty pair, returning no klines")
            return []

        if not self._data_dir.exists() or not self._data_dir.is_dir():
            self._log.warning("ohlcv data_dir does not exist or is not a directory: %s", self._data_dir)
            raise OHLCVUnavailableError(f"data_dir not a directory: {self._data_dir}")

        raw = self._read_csv_klines(ticker, "1h")
        candles = _resample_to_daily(raw)
        if not candles:
            self._log.debug("no daily klines for pair=%s", pair)
            return []

        candles.sort(key=lambda c: c["time"])
        if end_time_ms is not None:
            candles = [c for c in candles if c["time"] <= end_time_ms]
        self._log.debug("get_daily_klines_range pair=%s end_time_ms=%s returned %s", pair, end_time_ms, len(candles))
        return candles

    def _read_csv_klines(self, ticker: str, interval: str) -> list[OHLCVCandle]:
        """Read all CSV klines from daily and monthly folders; merge and sort by time."""
        all_rows: list[OHLCVCandle] = []

        for folder in ("daily", "monthly"):
            base = self._data_dir.joinpath("data", "spot", folder, "klines", ticker, interval)
            if not base.exists() or not base.is_dir():
                base = self._data_dir.joinpath("spot", folder, "klines", ticker, interval)
            if not base.exists() or not base.is_dir():
                continue
            csv_files = sorted(base.glob("*.csv"))
            for path in csv_files:
                try:
                    rows = self._parse_csv(path)
                    all_rows.extend(rows)
                except (ValueError, KeyError, OSError) as e:
                    self._log.warning("parse error for %s: %s", path, e)
                    raise OHLCVUnavailableError(f"Parse error reading {path}: {e}") from e

        if not all_rows:
            return []
        all_rows.sort(key=lambda c: c["time"])
        # Dedupe by time (daily and monthly can overlap for recent month)
        seen: set[int] = set()
        unique: list[OHLCVCandle] = []
        for c in all_rows:
            if c["time"] not in seen:
                seen.add(c["time"])
                unique.append(c)
        return unique

    def _parse_csv(self, path: Path) -> list[OHLCVCandle]:
        """Parse CSV with or without header. Binance raw format has no header: open_time, open, high, low, close, volume (indices 0-5)."""
        out: list[OHLCVCandle] = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 6:
                    continue
                try:
                    # Support both headerless (numeric columns) and header row (skip non-numeric)
                    t_raw = float(row[0])
                    t = int(t_raw)
                    if t >= 1e15:  # microseconds -> ms
                        t = t // 1000
                    o = float(row[1])
                    hi = float(row[2])
                    lo = float(row[3])
                    c = float(row[4])
                    v = float(row[5])
                    out.append(
                        OHLCVCandle(time=t, open=o, high=hi, low=lo, close=c, volume=v)
                    )
                except (ValueError, TypeError) as e:
                    self._log.warning("skip bad row in %s: %s", path, e)
                    continue
        return out
