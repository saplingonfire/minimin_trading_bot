"""Backtest performance metrics and stdout report. No I/O except printing."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# Assumed trading days per year for annualized Sharpe ratio.
TRADING_DAYS_PER_YEAR = 252


def _format_ts_ms_as_utc_date(ts_ms: int) -> str:
    """Format timestamp (ms) as YYYY-MM-DD UTC."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _daily_returns(equity_curve: list[tuple[int, float]]) -> list[float]:
    """Return list of daily simple returns (eq[t]/eq[t-1] - 1). Requires at least 2 points."""
    if len(equity_curve) < 2:
        return []
    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1][1]
        curr = equity_curve[i][1]
        if prev and prev > 0:
            returns.append((curr - prev) / prev)
        else:
            returns.append(0.0)
    return returns


def _annualized_sharpe(returns: list[float], risk_free_rate: float = 0.0) -> float | None:
    """Annualized Sharpe ratio. Returns None if insufficient data or zero vol."""
    if not returns or len(returns) < 2:
        return None
    n = len(returns)
    mean_r = sum(returns) / n
    var = sum((r - mean_r) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return None
    excess = mean_r - (risk_free_rate / TRADING_DAYS_PER_YEAR)
    return (excess / std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _max_drawdown(equity_curve: list[tuple[int, float]]) -> float:
    """Max drawdown as a positive decimal (e.g. 0.082 for -8.2%)."""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0][1]
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _cagr(start_equity: float, end_equity: float, years: float) -> float | None:
    """Compound annual growth rate. Returns None if years <= 0 or start_equity <= 0."""
    if years <= 0 or start_equity <= 0:
        return None
    if end_equity <= 0:
        return -1.0
    return (end_equity / start_equity) ** (1.0 / years) - 1.0


def compute_metrics(
    equity_curve: list[tuple[int, float]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute performance metrics from equity curve and trade list.

    equity_curve: list of (timestamp_ms, equity_usd).
    trades: list of dicts with keys ts_ms, pair, side, quantity, price, notional_usd;
            optional 'pnl' for realized PnL (used for win rate).
    """
    if not equity_curve:
        return {
            "start_equity": 0.0,
            "end_equity": 0.0,
            "total_return_pct": 0.0,
            "cagr_pct": None,
            "sharpe_annual": None,
            "max_drawdown_pct": 0.0,
            "num_trades": 0,
            "win_rate_pct": None,
            "start_time_ms": None,
            "end_time_ms": None,
        }

    start_equity = equity_curve[0][1]
    end_equity = equity_curve[-1][1]
    total_return = (end_equity / start_equity - 1.0) * 100.0 if start_equity > 0 else 0.0

    returns = _daily_returns(equity_curve)
    sharpe = _annualized_sharpe(returns)
    max_dd = _max_drawdown(equity_curve) * 100.0

    # Years from first to last timestamp (ms)
    t0 = equity_curve[0][0]
    t1 = equity_curve[-1][0]
    years = (t1 - t0) / (1000.0 * 86400 * 365.25) if t1 > t0 else 0.0
    cagr = _cagr(start_equity, end_equity, years)
    cagr_pct = cagr * 100.0 if cagr is not None else None

    num_trades = len(trades)
    win_rate_pct = None
    if trades:
        pnls = [t.get("pnl") for t in trades if "pnl" in t and t["pnl"] is not None]
        if pnls:
            wins = sum(1 for p in pnls if p > 0)
            win_rate_pct = (wins / len(pnls)) * 100.0

    total_fees = sum(float(t.get("fee", 0)) for t in trades)

    return {
        "start_equity": start_equity,
        "end_equity": end_equity,
        "total_return_pct": total_return,
        "cagr_pct": cagr_pct,
        "sharpe_annual": sharpe,
        "max_drawdown_pct": max_dd,
        "num_trades": num_trades,
        "win_rate_pct": win_rate_pct,
        "total_fees_usd": total_fees,
        "start_time_ms": t0,
        "end_time_ms": t1,
    }


def print_report(
    metrics: dict[str, Any],
    strategy_name: str = "Strategy",
    portfolio_breakdown: list[dict[str, Any]] | None = None,
) -> None:
    """Print a performance report to stdout (plain text). portfolio_breakdown: optional list of {asset, quantity, value_usd}."""
    lines = [
        "",
        "=== Backtest Report ===",
        f"Strategy: {strategy_name}",
        "",
    ]
    if metrics.get("start_time_ms") is not None and metrics.get("end_time_ms") is not None:
        lines.append(f"  Period:         {_format_ts_ms_as_utc_date(metrics['start_time_ms'])} to {_format_ts_ms_as_utc_date(metrics['end_time_ms'])} (UTC)")
        lines.append("")
    lines.extend([
        f"  Start equity:    {metrics['start_equity']:,.2f} USD",
        f"  End equity:      {metrics['end_equity']:,.2f} USD",
        f"  Total return:   {metrics['total_return_pct']:+.2f}%",
    ])
    if metrics.get("cagr_pct") is not None:
        lines.append(f"  CAGR:           {metrics['cagr_pct']:+.2f}%")
    lines.append(f"  Max drawdown:   {-metrics['max_drawdown_pct']:.2f}%")
    if metrics.get("sharpe_annual") is not None:
        lines.append(f"  Sharpe (ann.):  {metrics['sharpe_annual']:.2f}")
    lines.append(f"  Trades:        {metrics['num_trades']}")
    if metrics.get("total_fees_usd") is not None and metrics["total_fees_usd"] > 0:
        lines.append(f"  Total fees:    {metrics['total_fees_usd']:,.2f} USD")
    if metrics.get("win_rate_pct") is not None:
        lines.append(f"  Win rate:       {metrics['win_rate_pct']:.1f}%")
    breakdown = portfolio_breakdown if portfolio_breakdown is not None else metrics.get("portfolio_breakdown")
    if breakdown:
        # Sort by value_usd descending so largest positions appear first.
        sorted_breakdown = sorted(breakdown, key=lambda r: r.get("value_usd", 0.0), reverse=True)
        lines.append("")
        lines.append("  Portfolio at period end:")
        for row in sorted_breakdown:
            asset, qty, val = row["asset"], row["quantity"], row["value_usd"]
            lines.append(f"    {asset}: {qty:,.6g}  ({val:,.2f} USD)")
        lines.append("")
    print("\n".join(lines))
