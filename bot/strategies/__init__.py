"""Strategy registry: name -> strategy class."""

from bot.strategies.example import ExampleStrategy

STRATEGIES: dict[str, type] = {
    "example": ExampleStrategy,
}


def get(name: str) -> type:
    """Resolve strategy class by name. Raises KeyError with available names if unknown."""
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; available: {list(STRATEGIES.keys())}")
    return STRATEGIES[name]
