"""Tests for bot/ohlcv: pair mapping, BinanceHistoricalFileProvider, resampling."""

import csv
from pathlib import Path

import pytest

from bot.ohlcv import (
    OHLCVCandle,
    OHLCVUnavailableError,
    BinanceHistoricalFileProvider,
    _resample_to_daily,
    binance_ticker_to_roostoo_pair,
    discover_tradeable_pairs,
    roostoo_pair_to_binance_ticker,
)


# --- roostoo_pair_to_binance_ticker ---


def test_roostoo_pair_to_binance_ticker_btc_usd() -> None:
    assert roostoo_pair_to_binance_ticker("BTC/USD") == "BTCUSDT"


def test_roostoo_pair_to_binance_ticker_eth_usd() -> None:
    assert roostoo_pair_to_binance_ticker("ETH/USD") == "ETHUSDT"


def test_roostoo_pair_to_binance_ticker_normalizes_case() -> None:
    assert roostoo_pair_to_binance_ticker("btc/usd") == "BTCUSDT"


def test_roostoo_pair_to_binance_ticker_base_only() -> None:
    assert roostoo_pair_to_binance_ticker("BNB") == "BNBUSDT"


def test_roostoo_pair_to_binance_ticker_empty() -> None:
    assert roostoo_pair_to_binance_ticker("") == ""


def test_roostoo_pair_to_binance_ticker_strips_whitespace() -> None:
    assert roostoo_pair_to_binance_ticker("  BTC/USD  ") == "BTCUSDT"


# --- binance_ticker_to_roostoo_pair ---


def test_binance_ticker_to_roostoo_pair_btc() -> None:
    assert binance_ticker_to_roostoo_pair("BTCUSDT") == "BTC/USD"


def test_binance_ticker_to_roostoo_pair_eth() -> None:
    assert binance_ticker_to_roostoo_pair("ETHUSDT") == "ETH/USD"


def test_binance_ticker_to_roostoo_pair_empty() -> None:
    assert binance_ticker_to_roostoo_pair("") == ""


# --- discover_tradeable_pairs ---


def test_discover_tradeable_pairs_empty_dir(tmp_path: Path) -> None:
    assert discover_tradeable_pairs(tmp_path) == []


def test_discover_tradeable_pairs_finds_btc(tmp_path: Path) -> None:
    (tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h").mkdir(parents=True)
    assert discover_tradeable_pairs(tmp_path) == ["BTC/USD"]


def test_discover_tradeable_pairs_finds_multiple(tmp_path: Path) -> None:
    for ticker in ("BTCUSDT", "ETHUSDT"):
        (tmp_path / "data" / "spot" / "daily" / "klines" / ticker / "1h").mkdir(parents=True)
    assert set(discover_tradeable_pairs(tmp_path)) == {"BTC/USD", "ETH/USD"}


# --- _resample_to_daily ---


def test_resample_to_daily_empty() -> None:
    assert _resample_to_daily([]) == []


def test_resample_to_daily_single_day() -> None:
    # One day, two 1h candles
    base_ms = 24 * 3600 * 1000  # one day in ms
    candles: list[OHLCVCandle] = [
        OHLCVCandle(time=base_ms, open=100.0, high=105.0, low=99.0, close=102.0, volume=10.0),
        OHLCVCandle(time=base_ms + 3600 * 1000, open=102.0, high=106.0, low=101.0, close=104.0, volume=20.0),
    ]
    out = _resample_to_daily(candles)
    assert len(out) == 1
    assert out[0]["time"] == base_ms
    assert out[0]["open"] == 100.0
    assert out[0]["high"] == 106.0
    assert out[0]["low"] == 99.0
    assert out[0]["close"] == 104.0
    assert out[0]["volume"] == 30.0


def test_resample_to_daily_two_days() -> None:
    base_ms = 24 * 3600 * 1000
    candles: list[OHLCVCandle] = [
        OHLCVCandle(time=base_ms, open=100.0, high=101.0, low=99.0, close=100.5, volume=5.0),
        OHLCVCandle(time=base_ms + 2 * 24 * 3600 * 1000, open=200.0, high=202.0, low=198.0, close=201.0, volume=10.0),
    ]
    out = _resample_to_daily(candles)
    assert len(out) == 2
    assert out[0]["close"] == 100.5 and out[0]["volume"] == 5.0
    assert out[1]["close"] == 201.0 and out[1]["volume"] == 10.0


# --- BinanceHistoricalFileProvider with fixture dir ---


def _write_fixture_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_provider_returns_klines_from_fixture(tmp_path: Path) -> None:
    # Layout: data_dir/data/spot/daily/klines/BTCUSDT/1h/
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h"
    base.mkdir(parents=True)
    csv_path = base / "BTCUSDT-1h-2024-01-01.csv"
    _write_fixture_csv(
        csv_path,
        [
            {"Open time": "1704067200000", "Open": "42000", "High": "42100", "Low": "41900", "Close": "42050", "Volume": "100"},
            {"Open time": "1704070800000", "Open": "42050", "High": "42200", "Low": "42000", "Close": "42100", "Volume": "200"},
        ],
    )
    provider = BinanceHistoricalFileProvider(tmp_path)
    out = provider.get_klines("BTC/USD", "1h", 10)
    assert len(out) == 2
    assert out[0]["time"] == 1704067200000
    assert out[0]["open"] == 42000.0
    assert out[0]["high"] == 42100.0
    assert out[0]["low"] == 41900.0
    assert out[0]["close"] == 42050.0
    assert out[0]["volume"] == 100.0
    assert out[1]["time"] == 1704070800000
    assert out[1]["close"] == 42100.0


def test_provider_returns_last_limit(tmp_path: Path) -> None:
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "ETHUSDT" / "4h"
    base.mkdir(parents=True)
    csv_path = base / "ETHUSDT-4h.csv"
    rows = [
        {"Open time": str(1704067200000 + i * 4 * 3600 * 1000), "Open": "2000", "High": "2010", "Low": "1990", "Close": "2005", "Volume": "50"}
        for i in range(5)
    ]
    _write_fixture_csv(csv_path, rows)
    provider = BinanceHistoricalFileProvider(tmp_path)
    out = provider.get_klines("ETH/USD", "4h", 2)
    assert len(out) == 2
    # Last two candles
    assert out[0]["time"] == 1704067200000 + 3 * 4 * 3600 * 1000
    assert out[1]["time"] == 1704067200000 + 4 * 4 * 3600 * 1000


def test_provider_missing_dir_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    assert not missing.exists()
    provider = BinanceHistoricalFileProvider(missing)
    with pytest.raises(OHLCVUnavailableError) as exc_info:
        provider.get_klines("BTC/USD", "1h", 5)
    assert "not a directory" in str(exc_info.value).lower() or "data_dir" in str(exc_info.value).lower()


def test_provider_empty_data_returns_empty_list(tmp_path: Path) -> None:
    # Directory exists but no CSVs for this pair/interval
    (tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h").mkdir(parents=True)
    provider = BinanceHistoricalFileProvider(tmp_path)
    out = provider.get_klines("BTC/USD", "1h", 5)
    assert out == []


def test_provider_limit_zero_returns_empty(tmp_path: Path) -> None:
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h"
    base.mkdir(parents=True)
    _write_fixture_csv(base / "x.csv", [{"Open time": "0", "Open": "1", "High": "1", "Low": "1", "Close": "1", "Volume": "1"}])
    provider = BinanceHistoricalFileProvider(tmp_path)
    assert provider.get_klines("BTC/USD", "1h", 0) == []


def test_provider_interval_1d_resamples_from_1h(tmp_path: Path) -> None:
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h"
    base.mkdir(parents=True)
    day_start_ms = 1704067200000  # 2024-01-01 00:00 UTC
    rows = [
        {"Open time": str(day_start_ms), "Open": "100", "High": "102", "Low": "99", "Close": "101", "Volume": "10"},
        {"Open time": str(day_start_ms + 3600 * 1000), "Open": "101", "High": "103", "Low": "100", "Close": "102", "Volume": "20"},
    ]
    _write_fixture_csv(base / "BTCUSDT-1h-2024-01-01.csv", rows)
    provider = BinanceHistoricalFileProvider(tmp_path)
    out = provider.get_klines("BTC/USD", "1d", 5)
    assert len(out) == 1
    assert out[0]["open"] == 100.0
    assert out[0]["high"] == 103.0
    assert out[0]["low"] == 99.0
    assert out[0]["close"] == 102.0
    assert out[0]["volume"] == 30.0


def test_get_daily_klines_range_returns_all_without_end_time(tmp_path: Path) -> None:
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h"
    base.mkdir(parents=True)
    day_ms = 24 * 3600 * 1000
    for i in range(3):
        t = 1704067200000 + i * day_ms
        _write_fixture_csv(
            base / f"BTCUSDT-1h-2024-01-{i+1:02d}.csv",
            [{"Open time": str(t), "Open": "100", "High": "101", "Low": "99", "Close": "100.5", "Volume": "10"}],
        )
    provider = BinanceHistoricalFileProvider(tmp_path)
    out = provider.get_daily_klines_range("BTC/USD", end_time_ms=None)
    assert len(out) == 3
    assert out[0]["close"] == 100.5
    assert out[-1]["time"] == 1704067200000 + 2 * day_ms


def test_get_daily_klines_range_respects_end_time_ms(tmp_path: Path) -> None:
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h"
    base.mkdir(parents=True)
    day_ms = 24 * 3600 * 1000
    for i in range(3):
        t = 1704067200000 + i * day_ms
        _write_fixture_csv(
            base / f"BTCUSDT-1h-2024-01-{i+1:02d}.csv",
            [{"Open time": str(t), "Open": "100", "High": "101", "Low": "99", "Close": "100", "Volume": "10"}],
        )
    provider = BinanceHistoricalFileProvider(tmp_path)
    end_after_first = 1704067200000 + day_ms
    out = provider.get_daily_klines_range("BTC/USD", end_time_ms=end_after_first)
    assert len(out) == 2
