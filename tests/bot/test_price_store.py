"""Tests for bot/price_store: append, daily closes, warmup."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from bot.price_store import (
    MS_PER_DAY,
    MS_PER_HOUR,
    PriceStore,
    build_daily_bars_from_closes,
    warmup_from_binance_klines,
)


def test_append_and_daily_closes() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        # Simulate 3 days of data for BTC: one snapshot per day
        base_ts = 1000 * MS_PER_DAY
        for i, price in enumerate([100.0, 101.0, 102.0]):
            ts = base_ts + i * MS_PER_DAY
            store.insert_daily_rows([("BTC/USD", ts, price)])
        closes = store.get_daily_closes("BTC/USD", 10)
        assert closes == [100.0, 101.0, 102.0]


def test_count_days_with_data() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        assert store.count_days_with_data("BTC/USD") == 0
        store.insert_daily_rows([("BTC/USD", MS_PER_DAY, 100.0)])
        assert store.count_days_with_data("BTC/USD") == 1


def test_append_ticker_snapshot() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        ticker = {"BTC/USD": {"LastPrice": 50000, "UnitTradeValue": 1e9, "Change": 0.02}}
        n = store.append_ticker_snapshot(ticker, 2000 * MS_PER_DAY)
        assert n == 1
        closes = store.get_daily_closes("BTC/USD", 5)
        assert closes == [50000.0]


def test_build_daily_bars_from_closes() -> None:
    bars = build_daily_bars_from_closes([1.0, 2.0, 3.0])
    assert len(bars) == 3
    assert bars[0]["close"] == 1.0
    assert bars[2]["close"] == 3.0


def test_journal_mode_wal() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "prices.db"
        PriceStore(path)
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            assert row is not None
            assert row[0].lower() == "wal"
        finally:
            conn.close()


def test_get_hourly_closes() -> None:
    """Hourly closes bucket by hour and return oldest-first."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        base_ts = 1000 * MS_PER_DAY
        for h in range(5):
            ts = base_ts + h * MS_PER_HOUR
            store.append_ticker_snapshot(
                {"BTC/USD": {"LastPrice": 40000 + h * 100}}, ts,
            )
        closes = store.get_hourly_closes("BTC/USD", 10)
        assert closes == [40000, 40100, 40200, 40300, 40400]


def test_get_hourly_closes_multiple_per_hour() -> None:
    """When multiple snapshots land in the same hour, last-write wins."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        base_ts = 1000 * MS_PER_DAY
        store.append_ticker_snapshot({"BTC/USD": {"LastPrice": 40000}}, base_ts)
        store.append_ticker_snapshot({"BTC/USD": {"LastPrice": 41000}}, base_ts + 5 * 60 * 1000)
        closes = store.get_hourly_closes("BTC/USD", 5)
        assert closes == [41000]


def test_get_hourly_closes_limit() -> None:
    """Limit parameter truncates to most recent N hours."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = PriceStore(Path(tmp) / "prices.db")
        base_ts = 1000 * MS_PER_DAY
        for h in range(10):
            store.append_ticker_snapshot(
                {"BTC/USD": {"LastPrice": 30000 + h * 100}}, base_ts + h * MS_PER_HOUR,
            )
        closes = store.get_hourly_closes("BTC/USD", 3)
        assert len(closes) == 3
        assert closes == [30700, 30800, 30900]


def test_connect_retries_on_operational_error() -> None:
    real_connect = sqlite3.connect
    call_count = {"n": 0}

    def flaky_connect(database: str, timeout: float = 5.0, **kwargs: object) -> sqlite3.Connection:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(database, timeout=timeout)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = str(Path(tmp) / "p.db")
        with patch("bot.price_store.sqlite3.connect", side_effect=flaky_connect):
            store = PriceStore(path)
        assert store.count_days_with_data("BTC/USD") == 0
        assert call_count["n"] >= 2
