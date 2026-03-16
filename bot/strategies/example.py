"""Example strategy: minimal template that can place one MARKET buy per N ticks."""

from bot.base import PlaceOrderSignal, Strategy, TradingContext


class ExampleStrategy(Strategy):
    """Place a single MARKET buy every N ticks (for testing the pipeline)."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._pair = config.get("pair", "BTC/USD")
        self._size = float(config.get("size", 0.001))
        self._every_n_ticks = int(config.get("every_n_ticks", 10))
        self._tick_count = 0

    def on_start(self) -> None:
        self._tick_count = 0

    def next(self, context: TradingContext) -> list:
        self._tick_count += 1
        if self._tick_count % self._every_n_ticks != 0:
            return []

        ticker = context.ticker.get(self._pair) or context.ticker
        if isinstance(ticker, dict):
            last = ticker.get("LastPrice") or ticker.get("lastPrice")
        else:
            last = None
        if last is None:
            return []

        return [
            PlaceOrderSignal(
                pair=self._pair,
                side="BUY",
                quantity=self._size,
                order_type="MARKET",
                price=None,
            )
        ]

    def get_managed_pairs(self) -> list[str] | None:
        return [self._pair]
