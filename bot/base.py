"""Strategy contract and shared types: Context, Signals, Strategy ABC. No I/O."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from bot.ohlcv import OHLCVProvider


@dataclass(frozen=True)
class PlaceOrderSignal:
    """Signal to place an order."""

    pair: str
    side: Literal["BUY", "SELL"]
    quantity: float
    order_type: Literal["MARKET", "LIMIT"]
    price: float | None = None  # required when order_type == LIMIT


@dataclass(frozen=True)
class CancelOrderSignal:
    """Signal to cancel order(s) by order_id or by pair."""

    order_id: str | None = None
    pair: str | None = None


Signal = PlaceOrderSignal | CancelOrderSignal


@dataclass(frozen=True)
class TradingContext:
    """Read-only snapshot of market and account for strategy.next()."""

    server_time_ms: int
    ticker: dict[str, Any]
    balance: dict[str, Any]
    pending_orders: list[dict[str, Any]]
    exchange_info: dict[str, Any] | None = None
    ohlcv_provider: OHLCVProvider | None = None


class Strategy(ABC):
    """Abstract base for all strategies. Implement next(); override on_start/on_stop/get_managed_pairs as needed."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def on_start(self) -> None:
        """Called once before the first tick. Override for setup."""
        pass

    @abstractmethod
    def next(self, context: TradingContext) -> list[Signal]:
        """Given current context, return a list of signals (place/cancel)."""
        ...

    def on_stop(self) -> None:
        """Called once on shutdown. Override for teardown."""
        pass

    def get_managed_pairs(self) -> list[str] | None:
        """Pairs this strategy manages; used for cancel_orders_on_stop. Return None to skip cancel-by-pair."""
        return None
