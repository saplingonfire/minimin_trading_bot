"""Minimal tests for backtest module: engine runs and report produces numeric metrics."""

import csv
from pathlib import Path

import pytest

from bot.backtest import run_backtest, compute_metrics, print_report, BacktestResult


def _write_1h_csv(path: Path, day_start_ms: int, close: float, volume: float = 1000.0) -> None:
    """Write 24 rows of 1h candles for one day so resample gives one daily bar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for h in range(24):
        t = day_start_ms + h * 3600 * 1000
        rows.append({
            "Open time": str(t),
            "Open": str(close - 100),
            "High": str(close + 50),
            "Low": str(close - 50),
            "Close": str(close),
            "Volume": str(volume),
        })
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_backtest_engine_returns_equity_and_trades(tmp_path: Path) -> None:
    # Need warmup (max(ma_window+2, 8)) + at least 1 trading day. With ma_window=5 -> warmup=8.
    # So 10 days of daily data = 10 days * 24h candles each.
    base = tmp_path / "data" / "spot" / "daily" / "klines" / "BTCUSDT" / "1h"
    base.mkdir(parents=True)
    day_ms = 24 * 3600 * 1000
    start_ms = 1704067200000  # 2024-01-01
    for i in range(10):
        _write_1h_csv(base / f"BTCUSDT-1h-2024-01-{i+1:02d}.csv", start_ms + i * day_ms, 40000.0 + i * 100)

    result = run_backtest(
        str(tmp_path),
        "hybrid_trend_cross_sectional",
        {"ma_window": 5, "N": 2, "min_days_history": 2, "min_volume_usd": 0},
        initial_balance_usd=10_000.0,
    )
    assert isinstance(result, BacktestResult)
    assert isinstance(result.equity_curve, list)
    assert isinstance(result.trades, list)
    assert isinstance(result.end_portfolio, list)
    assert len(result.equity_curve) >= 1
    assert result.equity_curve[0][1] == 10_000.0


def test_report_metrics_are_numeric() -> None:
    # One point -> no returns; two points with same value -> 0 return
    equity_curve = [(1704067200000, 10_000.0), (1704153600000, 10_100.0)]
    trades: list[dict] = []
    metrics = compute_metrics(equity_curve, trades)
    assert metrics["start_equity"] == 10_000.0
    assert metrics["end_equity"] == 10_100.0
    assert metrics["total_return_pct"] == pytest.approx(1.0)
    assert metrics["max_drawdown_pct"] >= 0
    assert metrics["num_trades"] == 0
    print_report(metrics, strategy_name="test")
    # Should not raise
