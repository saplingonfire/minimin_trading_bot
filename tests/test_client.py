"""Unit tests for RoostooClient and signing."""

import hmac
import hashlib
from unittest.mock import patch

import pytest

from roostoo import RoostooClient, RoostooAPIError
from roostoo.auth import sign, timestamp_ms


def test_timestamp_ms_is_13_digits() -> None:
    ts = timestamp_ms()
    assert len(ts) == 13
    assert ts.isdigit()


def test_sign_sorts_keys_and_produces_correct_hmac() -> None:
    api_key = "USEAPIKEYASMYID"
    secret_key = "S1XP1e3UZj6A7H5fATj0jNhqPxxdSJYdInClVN65XAbvqqMKjVHjA7PZj4W12oep"
    # Use fixed timestamp from Roostoo doc example so we can check HMAC
    with patch("roostoo.auth.timestamp_ms", return_value="1580774512000"):
        payload = {
            "pair": "BNB/USD",
            "quantity": "2000",
            "side": "BUY",
            "type": "MARKET",
        }
        headers, body = sign(api_key, secret_key, payload)
    expected_str = "pair=BNB/USD&quantity=2000&side=BUY&timestamp=1580774512000&type=MARKET"
    assert body == expected_str
    expected_sig = hmac.new(
        secret_key.encode("utf-8"),
        expected_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert headers["RST-API-KEY"] == api_key
    assert headers["MSG-SIGNATURE"] == expected_sig


def test_place_order_limit_requires_price() -> None:
    client = RoostooClient(api_key="k", secret_key="s")
    with pytest.raises(ValueError, match="LIMIT orders require price"):
        client.place_order("BTC/USD", "BUY", 0.01, order_type="LIMIT")


def test_api_error_on_success_false() -> None:
    client = RoostooClient(api_key="k", secret_key="s")
    with patch("requests.get") as mget:
        mget.return_value.status_code = 200
        mget.return_value.json.return_value = {
            "Success": False,
            "ErrMsg": "no pending order under this account",
        }
        mget.return_value.raise_for_status = lambda: None
        with pytest.raises(RoostooAPIError) as exc_info:
            client.get_balance()
    assert "no pending order" in str(exc_info.value)
    assert exc_info.value.raw is not None
