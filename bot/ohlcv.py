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

    def _read_csv_klines(self, ticker: str, interval: str) -> list[OHLCVCandle]:
        # Try layout: data_dir/data/spot/daily/klines/{ticker}/{interval}/
        base = self._data_dir.joinpath(*_SUBDIR_KLINES, ticker, interval)
        if not base.exists():
            # Alternative: data_dir/spot/daily/klines/{ticker}/{interval}/
            base = self._data_dir.joinpath("spot", "daily", "klines", ticker, interval)
        if not base.exists() or not base.is_dir():
            return []

        csv_files = sorted(base.glob("*.csv"))
        if not csv_files:
            return []

        all_rows: list[OHLCVCandle] = []
        for path in csv_files:
            try:
                rows = self._parse_csv(path)
                all_rows.extend(rows)
            except (ValueError, KeyError, OSError) as e:
                self._log.warning("parse error for %s: %s", path, e)
                raise OHLCVUnavailableError(f"Parse error reading {path}: {e}") from e

        return all_rows

    def _parse_csv(self, path: Path) -> list[OHLCVCandle]:
        out: list[OHLCVCandle] = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return []
            # Normalize column names (Binance: "Open time", "Open", ...)
            for row in reader:
                try:
                    t = int(_get_col(row, _COL_OPEN_TIME, "Open time"))
                    o = float(_get_col(row, _COL_OPEN, "Open"))
                    hi = float(_get_col(row, _COL_HIGH, "High"))
                    lo = float(_get_col(row, _COL_LOW, "Low"))
                    c = float(_get_col(row, _COL_CLOSE, "Close"))
                    v = float(_get_col(row, _COL_VOLUME, "Volume"))
                    out.append(
                        OHLCVCandle(time=t, open=o, high=hi, low=lo, close=c, volume=v)
                    )
                except (ValueError, TypeError) as e:
                    self._log.warning("skip bad row in %s: %s", path, e)
                    continue
        return out
