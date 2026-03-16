"""Optional TypedDicts for Roostoo API responses (IDE/type checking)."""

from typing import Any, TypedDict


class TradePairInfo(TypedDict, total=False):
    Coin: str
    CoinFullName: str
    Unit: str
    UnitFullName: str
    CanTrade: bool
    PricePrecision: int
    AmountPrecision: int
    MiniOrder: float


class ExchangeInfoResponse(TypedDict, total=False):
    IsRunning: bool
    InitialWallet: dict[str, float]
    TradePairs: dict[str, TradePairInfo]


class TickerData(TypedDict, total=False):
    MaxBid: float
    MinAsk: float
    LastPrice: float
    Change: float
    CoinTradeValue: float
    UnitTradeValue: float


class WalletAsset(TypedDict):
    Free: float
    Lock: float


class OrderDetail(TypedDict, total=False):
    Pair: str
    OrderID: int
    Status: str
    Role: str
    ServerTimeUsage: float
    CreateTimestamp: int
    FinishTimestamp: int
    Side: str
    Type: str
    StopType: str
    Price: float
    Quantity: float
    FilledQuantity: float
    FilledAverPrice: float
    CoinChange: float
    UnitChange: float
    CommissionCoin: str
    CommissionChargeValue: float
    CommissionPercent: float


def as_order_detail(d: Any) -> OrderDetail:
    """Cast a dict to OrderDetail for type narrowing."""
    return d
