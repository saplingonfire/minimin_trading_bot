"""Tests for bot.strategies.utils: tradeable_pairs, exclude filter."""

import pytest

from bot.strategies.utils import tradeable_pairs


def test_tradeable_pairs_empty_exchange_info() -> None:
    assert tradeable_pairs(None) == []
    assert tradeable_pairs({}) == []


def test_tradeable_pairs_no_exclude() -> None:
    exchange_info = {
        "TradePairs": {
            "BTC/USD": {"CanTrade": True},
            "ETH/USD": {"CanTrade": True},
            "XRP/USD": {"CanTrade": False},
        }
    }
    result = tradeable_pairs(exchange_info)
    assert set(result) == {"BTC/USD", "ETH/USD"}
    assert result == sorted(result)


def test_tradeable_pairs_exclude_single() -> None:
    exchange_info = {
        "TradePairs": {
            "BTC/USD": {"CanTrade": True},
            "ETH/USD": {"CanTrade": True},
            "TRUMP/USD": {"CanTrade": True},
        }
    }
    result = tradeable_pairs(exchange_info, exclude=["TRUMP/USD"])
    assert set(result) == {"BTC/USD", "ETH/USD"}


def test_tradeable_pairs_exclude_base_only_normalized() -> None:
    exchange_info = {
        "TradePairs": {
            "BTC/USD": {"CanTrade": True},
            "ETH/USD": {"CanTrade": True},
        }
    }
    result = tradeable_pairs(exchange_info, exclude=["TRUMP", "eth"])
    assert "ETH/USD" not in result
    assert "BTC/USD" in result


def test_tradeable_pairs_exclude_empty_and_none() -> None:
    exchange_info = {"TradePairs": {"BTC/USD": {"CanTrade": True}}}
    assert tradeable_pairs(exchange_info, exclude=None) == ["BTC/USD"]
    assert tradeable_pairs(exchange_info, exclude=[]) == ["BTC/USD"]
