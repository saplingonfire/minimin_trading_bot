"""Timestamp and HMAC signing for Roostoo RCL_TopLevelCheck endpoints."""

import hmac
import hashlib
import time
from typing import Any


def timestamp_ms() -> str:
    """Return a 13-digit millisecond timestamp as string."""
    return str(int(time.time() * 1000))


def sign(api_key: str, secret_key: str, payload: dict[str, Any]) -> tuple[dict[str, str], str]:
    """
    Generate signed headers and totalParams for RCL_TopLevelCheck endpoints.

    Adds timestamp to payload, sorts keys, builds k1=v1&k2=v2 string,
    signs with HMAC-SHA256(secret_key, string), returns (headers, body_or_query_string).
    """
    payload = dict(payload)
    payload["timestamp"] = timestamp_ms()
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)

    signature = hmac.new(
        secret_key.encode("utf-8"),
        total_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers: dict[str, str] = {
        "RST-API-KEY": api_key,
        "MSG-SIGNATURE": signature,
    }
    return headers, total_params
