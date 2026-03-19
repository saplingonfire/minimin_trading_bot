"""Shared helpers for strategies: exchange info, ticker, balance, pair parsing. No I/O."""

from __future__ import annotations

from typing import Any


def _normalize_pair_symbol(s: str) -> str:
    """Normalize to BASE/QUOTE form (e.g. 'btc' -> 'BTC/USD', 'TRUMP/USD' -> 'TRUMP/USD')."""
    s = s.strip().upper()
    if not s:
        return s
    if "/" in s:
        base, quote = s.split("/", 1)
        return f"{base.strip()}/{quote.strip()}"
    return f"{s}/USD"


def tradeable_pairs(
    exchange_info: dict[str, Any] | None,
    exclude: list[str] | set[str] | None = None,
) -> list[str]:
    """Return list of tradeable pair symbols (e.g. BTC/USD), optionally excluding a blocklist.

    Args:
        exchange_info: API exchange info with TradePairs (or trade_pairs). CanTrade=False
            pairs are already omitted.
        exclude: Optional list/set of pair symbols to exclude (e.g. ["TRUMP/USD", "PENGU"]).
            Accepts "BASE" or "BASE/QUOTE"; normalized to "BASE/QUOTE" for matching.

    Returns:
        Sorted list of pair strings not in exclude. [] if exchange_info is None.
    """
    if not exchange_info:
        return []
    pairs = exchange_info.get("TradePairs") or exchange_info.get("trade_pairs") or {}
    out: list[str] = []
    for k, v in pairs.items():
        if isinstance(v, dict) and v.get("CanTrade", v.get("can_trade", True)) is False:
            continue
        pair = k if "/" in str(k) else f"{str(k)}/USD"
        out.append(pair)

    if exclude:
        exclude_set = {_normalize_pair_symbol(p) for p in exclude if p and str(p).strip()}
        out = [p for p in out if _normalize_pair_symbol(p) not in exclude_set]

    return sorted(out)


def get_price(ticker: dict[str, Any], pair: str) -> float:
    """Last price for pair from ticker. Returns 0.0 if missing or invalid."""
    row = ticker.get(pair) or ticker
    if not isinstance(row, dict):
        return 0.0
    return float(row.get("LastPrice", row.get("lastPrice", 0)) or 0)


def get_volume_usd(ticker: dict[str, Any], pair: str) -> float:
    """24h unit trade value (USD volume proxy) for pair. Returns 0.0 if missing."""
    row = ticker.get(pair) or ticker
    if not isinstance(row, dict):
        return 0.0
    return float(row.get("UnitTradeValue", row.get("unit_trade_value", 0)) or 0)


def get_change_pct(ticker: dict[str, Any], pair: str) -> float:
    """24h change percent for pair. Returns 0.0 if missing."""
    row = ticker.get(pair) or ticker
    if not isinstance(row, dict):
        return 0.0
    return float(row.get("Change", row.get("change", 0)) or 0)


def get_balance_free(balance: dict[str, Any], asset: str) -> float:
    """Free balance for asset. balance is dict of asset -> {Free, Lock}. Returns 0.0 if missing."""
    entry = balance.get(asset) or balance.get(asset.upper())
    if not isinstance(entry, dict):
        return 0.0
    return float(entry.get("Free", entry.get("free", 0)) or 0)


def parse_pair(pair: str) -> tuple[str, str]:
    """Return (base, quote) for a symbol like BTC/USD or BTC. Assumes USD if no quote."""
    if "/" in pair:
        a, b = pair.strip().upper().split("/", 1)
        return (a.strip(), b.strip())
    return (pair.strip().upper(), "USD")
