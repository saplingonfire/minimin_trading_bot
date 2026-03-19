"""Backtest module: run configured strategy over historical OHLCV and print performance report."""

from bot.backtest.engine import run_backtest
from bot.backtest.report import compute_metrics, print_report

__all__ = ["run_backtest", "compute_metrics", "print_report"]
