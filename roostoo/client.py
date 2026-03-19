"""Roostoo Public API (v3) client."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

import requests

from roostoo.auth import sign, timestamp_ms
from roostoo.exceptions import RoostooAPIError

DEFAULT_BASE_URL = "https://mock-api.roostoo.com"
ENV_API_KEY = "ROOSTOO_API_KEY"
ENV_SECRET_KEY = "ROOSTOO_SECRET_KEY"


class RoostooClient:
    """Client for the Roostoo Public API (v3)."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        api_log_path: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get(ENV_API_KEY) or ""
        self._secret_key = secret_key or os.environ.get(ENV_SECRET_KEY) or ""
        self._base_url = base_url.rstrip("/")
        self._api_log_path = api_log_path
        if not self._api_key or not self._secret_key:
            raise ValueError(
                "api_key and secret_key are required (or set ROOSTOO_API_KEY and ROOSTOO_SECRET_KEY)"
            )

    def _log_api(
        self,
        method: str,
        path: str,
        success: bool,
        status_code: int | None = None,
        error: str | None = None,
    ) -> None:
        """Append one JSONL line to api_log_path when set. No request/response bodies or secrets."""
        if not self._api_log_path:
            return
        ts = datetime.now(timezone.utc).isoformat()
        line: dict[str, Any] = {"ts": ts, "method": method, "path": path, "success": success}
        if status_code is not None:
            line["status_code"] = status_code
        if error:
            line["error"] = error[:500]
        try:
            with open(self._api_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
                f.flush()
        except OSError:
            pass

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {}
        query_string: str | None = None
        body: str | None = None
        if signed:
            payload = params or {}
            headers, signed_str = sign(self._api_key, self._secret_key, payload)
            if method == "GET":
                query_string = signed_str
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                body = signed_str
            params = None
        elif params is None:
            params = {}

        try:
            if method == "GET":
                if query_string is not None:
                    url = f"{url}?{query_string}"
                resp = requests.get(url, headers=headers, timeout=30)
            else:
                resp = requests.post(
                    url,
                    headers=headers,
                    data=body,
                    timeout=30,
                )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            status = getattr(e.response, "status_code", None) if e.response else None
            body = e.response.text if e.response else None
            self._log_api(method, path, success=False, status_code=status, error=str(e)[:500])
            raise RoostooAPIError(
                str(e),
                status_code=status,
                response_body=body,
            ) from e

        out: dict[str, Any] = resp.json()

        if out.get("Success") is False:
            err_msg = out.get("ErrMsg", "Unknown error")
            self._log_api(method, path, success=False, error=err_msg[:500])
            raise RoostooAPIError(err_msg, raw=out)

        self._log_api(method, path, success=True)
        return out

    def get_server_time(self) -> dict[str, Any]:
        """GET /v3/serverTime — test connectivity and get server time."""
        path = "/v3/serverTime"
        url = f"{self._base_url}{path}"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            out = resp.json()
            self._log_api("GET", path, success=True)
            return out
        except requests.exceptions.RequestException as e:
            status = getattr(e.response, "status_code", None) if e.response else None
            body = e.response.text if e.response else None
            self._log_api("GET", path, success=False, status_code=status, error=str(e)[:500])
            raise RoostooAPIError(str(e), status_code=status, response_body=body) from e

    def get_exchange_info(self) -> dict[str, Any]:
        """GET /v3/exchangeInfo — exchange trading rules and symbol information."""
        path = "/v3/exchangeInfo"
        url = f"{self._base_url}{path}"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            out = resp.json()
            self._log_api("GET", path, success=True)
            return out
        except requests.exceptions.RequestException as e:
            status = getattr(e.response, "status_code", None) if e.response else None
            body = e.response.text if e.response else None
            self._log_api("GET", path, success=False, status_code=status, error=str(e)[:500])
            raise RoostooAPIError(str(e), status_code=status, response_body=body) from e

    def get_ticker(self, pair: str | None = None) -> dict[str, Any]:
        """GET /v3/ticker — market ticker for one or all pairs (RCL_TSCheck)."""
        path = "/v3/ticker"
        params: dict[str, Any] = {"timestamp": timestamp_ms()}
        if pair is not None:
            params["pair"] = pair
        url = f"{self._base_url}{path}"
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            out = resp.json()
            if out.get("Success") is False:
                err_msg = out.get("ErrMsg", "Unknown error")
                self._log_api("GET", path, success=False, error=err_msg[:500])
                raise RoostooAPIError(err_msg, raw=out)
            self._log_api("GET", path, success=True)
            return out
        except requests.exceptions.RequestException as e:
            status = getattr(e.response, "status_code", None) if e.response else None
            body = e.response.text if e.response else None
            self._log_api("GET", path, success=False, status_code=status, error=str(e)[:500])
            raise RoostooAPIError(str(e), status_code=status, response_body=body) from e

    def get_balance(self) -> dict[str, Any]:
        """GET /v3/balance — current wallet balance (signed)."""
        return self._request("GET", "/v3/balance", params={}, signed=True)

    def get_pending_count(self) -> dict[str, Any]:
        """GET /v3/pending_count — total pending order count (signed)."""
        return self._request("GET", "/v3/pending_count", params={}, signed=True)

    def place_order(
        self,
        pair: str,
        side: Literal["BUY", "SELL"],
        quantity: str | float,
        order_type: Literal["MARKET", "LIMIT"] = "MARKET",
        price: str | float | None = None,
    ) -> dict[str, Any]:
        """POST /v3/place_order — place a LIMIT or MARKET order (signed)."""
        pair_str = pair if "/" in pair else f"{pair}/USD"
        if order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT orders require price")
        payload: dict[str, Any] = {
            "pair": pair_str,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        if order_type == "LIMIT" and price is not None:
            payload["price"] = str(price)
        return self._request("POST", "/v3/place_order", params=payload, signed=True)

    def query_order(
        self,
        order_id: str | None = None,
        pair: str | None = None,
        pending_only: bool | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """POST /v3/query_order — query order history or pending orders (signed).
        Per API docs, when no order matched the API returns Success: false, ErrMsg: \"no order matched\".
        We treat that as success with empty OrderMatched list."""
        payload: dict[str, Any] = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        elif pair is not None:
            payload["pair"] = pair
            if pending_only is not None:
                payload["pending_only"] = "TRUE" if pending_only else "FALSE"
        if offset is not None:
            payload["offset"] = str(offset)
        if limit is not None:
            payload["limit"] = str(limit)
        try:
            return self._request("POST", "/v3/query_order", params=payload, signed=True)
        except RoostooAPIError as e:
            if (e.raw and e.raw.get("ErrMsg") == "no order matched") or (str(e).strip() == "no order matched"):
                return {"Success": True, "ErrMsg": "", "OrderMatched": []}
            raise

    def cancel_order(
        self,
        order_id: str | None = None,
        pair: str | None = None,
    ) -> dict[str, Any]:
        """POST /v3/cancel_order — cancel specific or all pending orders (signed)."""
        payload: dict[str, Any] = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        elif pair is not None:
            payload["pair"] = pair
        return self._request("POST", "/v3/cancel_order", params=payload, signed=True)
