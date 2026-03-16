"""Build TradingContext from RoostooClient (one snapshot per tick)."""

from typing import TYPE_CHECKING, Any

from bot.base import TradingContext

if TYPE_CHECKING:
    from bot.ohlcv import OHLCVProvider
    from roostoo.client import RoostooClient


def build_context(
    client: "RoostooClient",
    pair: str | None = None,
    exchange_info: dict[str, Any] | None = None,
    ohlcv_provider: "OHLCVProvider | None" = None,
) -> TradingContext:
    """Fetch market data and account state, return a read-only TradingContext."""
    st = client.get_server_time()
    server_time_ms = int(st.get("ServerTime", 0) or st.get("serverTime", 0) or 0)

    ticker_resp = client.get_ticker(pair)
    ticker_data = ticker_resp.get("Ticker") or ticker_resp.get("ticker") or ticker_resp
    if isinstance(ticker_data, dict) and "pair" not in ticker_data and pair:
        ticker_data = {pair: ticker_data}
    if not isinstance(ticker_data, dict):
        ticker_data = {}

    balance_resp = client.get_balance()
    balance = balance_resp.get("Wallet") or balance_resp.get("wallet") or balance_resp

    orders_resp = client.query_order(pending_only=True)
    pending_orders = orders_resp.get("Orders") or orders_resp.get("orders") or orders_resp.get("Data") or []
    if not isinstance(pending_orders, list):
        pending_orders = []

    return TradingContext(
        server_time_ms=server_time_ms,
        ticker=ticker_data,
        balance=balance,
        pending_orders=pending_orders,
        exchange_info=exchange_info,
        ohlcv_provider=ohlcv_provider,
    )
