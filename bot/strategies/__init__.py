"""Strategy registry: name -> strategy class."""

from bot.strategies.bollinger_rsi import BollingerRSIStrategy
from bot.strategies.cross_sectional_momentum import CrossSectionalMomentumStrategy
from bot.strategies.example import ExampleStrategy
from bot.strategies.momentum_20_50 import Momentum20_50Strategy

STRATEGIES: dict[str, type] = {
    "example": ExampleStrategy,
    "cross_sectional_momentum": CrossSectionalMomentumStrategy,
    "momentum_20_50": Momentum20_50Strategy,
    "bollinger_rsi": BollingerRSIStrategy,
}


def get(name: str) -> type:
    """Resolve strategy class by name. Raises KeyError with available names if unknown."""
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; available: {list(STRATEGIES.keys())}")
    return STRATEGIES[name]
