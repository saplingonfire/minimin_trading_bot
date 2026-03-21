"""Local price store and bar builder for strategies that need self-built history.

Roostoo does not provide OHLCV; we persist ticker snapshots (from GET /v3/ticker)
and build daily bars for regime (BTC MA20) and momentum (r1/r3/r7). Optional
Binance warmup seeds BTC (and optionally other symbols) so MA20 is available from day one.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Default BTC pair for regime and warmup
BTC_PAIR = "BTC/USD"
MS_PER_DAY = 24 * 3600 * 1000

# SQLite: wait for locks; WAL improves concurrent access (persists on DB file)
_CONNECT_TIMEOUT_SEC = 8.0
_BUSY_TIMEOUT_MS = 10_000

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SEC = 0.05

T = TypeVar("T")


def _with_retry(operation: Callable[[], T], *, attempts: int = _RETRY_ATTEMPTS) -> T:
    """Retry transient sqlite3.OperationalError with exponential backoff."""
    delay = _RETRY_BASE_DELAY_SEC
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as e:
            last_exc = e
            if attempt < attempts - 1:
                logger.warning(
                    "sqlite OperationalError (attempt %s/%s): %s; retrying in %.3fs",
                    attempt + 1,
                    attempts,
                    e,
                    delay,
                )
                time.sleep(delay)
                delay *= 2
            else:
                logger.error("sqlite OperationalError after %s attempts: %s", attempts, e)
                raise
    assert last_exc is not None
    raise last_exc


def _normalize_pair(key: str) -> str:
    """Normalize exchange pair key to 'BASE/USD' form."""
    key = (key or "").strip().upper()
    if not key:
        return ""
    if "/" in key:
        return key
    return f"{key}/USD"


def _ticker_row_to_values(
    pair: str,
    row: dict[str, Any],
    ts_ms: int,
) -> tuple[str, int, float, float, float]:
    """Extract (symbol, ts_ms, last_price, volume_24h_usd, change_24h) from one ticker row."""
    symbol = _normalize_pair(pair)
    last = row.get("LastPrice") or row.get("lastPrice") or 0
    vol = row.get("UnitTradeValue") or row.get("CoinTradeValue") or row.get("unit_trade_value") or 0
    ch = row.get("Change") or row.get("change") or 0
    return (symbol, ts_ms, float(last), float(vol), float(ch))


class PriceStore:
    """SQLite-backed store of ticker snapshots. Append per cycle; query daily closes for bars."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self._path), timeout=_CONNECT_TIMEOUT_SEC)
        c.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return c

    def _init_schema(self) -> None:
        def _do() -> None:
            with self._conn() as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute(
                    """
                    CREATE TABLE IF NOT EXISTS price_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts_utc_ms INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        last_price REAL NOT NULL,
                        volume_24h_usd REAL,
                        change_24h REAL
                    )
                    """
                )
                c.execute(
                    "CREATE INDEX IF NOT EXISTS idx_price_symbol_ts ON price_snapshots(symbol, ts_utc_ms)"
                )
                c.commit()

        _with_retry(_do)

    def append_ticker_snapshot(self, ticker: dict[str, Any], ts_ms: int | None = None) -> int:
        """Append one snapshot from a full ticker response (pair -> {LastPrice, UnitTradeValue, Change, ...}).
        Returns number of rows inserted.
        """
        ts_ms = ts_ms or int(time.time() * 1000)
        if not isinstance(ticker, dict):
            return 0
        return _with_retry(lambda: self._append_ticker_snapshot_impl(ticker, ts_ms))

    def _append_ticker_snapshot_impl(self, ticker: dict[str, Any], ts_ms: int) -> int:
        count = 0
        with self._conn() as c:
            for pair, row in ticker.items():
                if not isinstance(row, dict):
                    continue
                try:
                    symbol, t, last, vol, ch = _ticker_row_to_values(pair, row, ts_ms)
                    if not symbol or last <= 0:
                        continue
                    c.execute(
                        "INSERT INTO price_snapshots (ts_utc_ms, symbol, last_price, volume_24h_usd, change_24h) VALUES (?, ?, ?, ?, ?)",
                        (t, symbol, last, vol, ch),
                    )
                    count += 1
                except (TypeError, ValueError) as e:
                    logger.debug("skip pair %s: %s", pair, e)
                    continue
            c.commit()
        return count

    def get_daily_closes(self, symbol: str, limit_days: int) -> list[float]:
        """Return the last `limit_days` daily close prices for symbol (oldest first).
        Daily close = last snapshot in that UTC day. If fewer than limit_days exist, returns what exists.
        """
        symbol = _normalize_pair(symbol)
        if not symbol or limit_days <= 0:
            return []
        return _with_retry(lambda: self._get_daily_closes_impl(symbol, limit_days))

    def _get_daily_closes_impl(self, symbol: str, limit_days: int) -> list[float]:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT ts_utc_ms, last_price FROM price_snapshots WHERE symbol = ? ORDER BY ts_utc_ms",
                (symbol,),
            )
            rows = cur.fetchall()
        if not rows:
            return []
        by_day: dict[int, float] = {}
        for ts_ms, price in rows:
            day_key = ts_ms // MS_PER_DAY
            by_day[day_key] = price
        sorted_days = sorted(by_day.keys())
        return [by_day[d] for d in sorted_days[-limit_days:]]

    def count_days_with_data(self, symbol: str) -> int:
        """Number of distinct UTC days that have at least one snapshot for symbol."""
        symbol = _normalize_pair(symbol)
        if not symbol:
            return 0
        return _with_retry(lambda: self._count_days_with_data_impl(symbol))

    def _count_days_with_data_impl(self, symbol: str) -> int:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(DISTINCT (ts_utc_ms / ?)) FROM price_snapshots WHERE symbol = ?",
                (MS_PER_DAY, symbol),
            )
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0

    def symbols_with_at_least_n_days(self, min_days: int) -> list[str]:
        """Return list of symbols that have at least min_days of daily data."""
        return _with_retry(lambda: self._symbols_with_at_least_n_days_impl(min_days))

    def _symbols_with_at_least_n_days_impl(self, min_days: int) -> list[str]:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol FROM price_snapshots
                GROUP BY symbol
                HAVING COUNT(DISTINCT (ts_utc_ms / ?)) >= ?
                """,
                (MS_PER_DAY, min_days),
            )
            return [row[0] for row in cur.fetchall()]

    def insert_daily_rows(self, rows: list[tuple[str, int, float]]) -> int:
        """Bulk insert (symbol, ts_utc_ms, last_price). Used by Binance warmup. Returns count inserted."""
        if not rows:
            return 0
        return _with_retry(lambda: self._insert_daily_rows_impl(rows))

    def _insert_daily_rows_impl(self, rows: list[tuple[str, int, float]]) -> int:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO price_snapshots (ts_utc_ms, symbol, last_price, volume_24h_usd, change_24h) VALUES (?, ?, ?, 0.0, 0.0)",
                [(ts, sym, price) for sym, ts, price in rows],
            )
            conn.commit()
            return len(rows)
        finally:
            conn.close()


def build_daily_bars_from_closes(closes: list[float]) -> list[dict[str, Any]]:
    """Turn a list of daily closes (oldest first) into a list of bar dicts with 'close' and 'time'.
    Used by callers that need bar-shaped data. time is index-based placeholder if not stored.
    """
    return [{"close": c, "time": i * MS_PER_DAY} for i, c in enumerate(closes)]


def warmup_from_binance_klines(
    store: PriceStore,
    symbol_binance: str = "BTCUSDT",
    pair_roostoo: str = BTC_PAIR,
    limit: int = 30,
) -> int:
    """Fetch Binance daily klines and backfill the price store. No API key required.
    Returns number of days written. Call once at startup if store has no BTC history.
    """
    try:
        import urllib.request
    except ImportError:
        logger.warning("urllib not available, skip Binance warmup")
        return 0
    url = (
        f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1d&limit={limit}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read()
    except Exception as e:
        logger.warning("Binance warmup request failed: %s", e)
        return 0
    try:
        import json
        klines = json.loads(data.decode("utf-8"))
    except Exception as e:
        logger.warning("Binance warmup parse failed: %s", e)
        return 0
    # Binance klines: [ open_time, open, high, low, close, volume, ... ]
    rows: list[tuple[str, int, float]] = []
    for k in klines:
        if len(k) < 5:
            continue
        ts_ms = int(k[0])
        close = float(k[4])
        if close <= 0:
            continue
        rows.append((pair_roostoo, ts_ms, close))
    count = store.insert_daily_rows(rows) if rows else 0
    logger.info("Binance warmup: %s days for %s", count, pair_roostoo)
    return count
