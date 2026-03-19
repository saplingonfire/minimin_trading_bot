#!/usr/bin/env python3
"""Test all Roostoo SDK methods using test credentials (ROOSTOO_TEST_* from .env)."""

import argparse
import json
import os
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(_path: str | None = None) -> bool:
        return False

load_dotenv(os.path.join(_repo_root, ".env"))

from roostoo.client import RoostooClient
from roostoo.exceptions import RoostooAPIError

ENV_TEST_API_KEY = "ROOSTOO_TEST_API_KEY"
ENV_TEST_SECRET_KEY = "ROOSTOO_TEST_SECRET_KEY"
ENV_TEST_BASE_URL = "ROOSTOO_TEST_BASE_URL"
DEFAULT_BASE_URL = "https://mock-api.roostoo.com"


def run(name: str, fn, *args, **kwargs) -> tuple[bool, str, dict | None]:
    """Run a test; return (ok, message, response). response is None on failure or when not dict."""
    try:
        out = fn(*args, **kwargs)
        if out is None:
            return (True, "ok", None)
        if isinstance(out, dict):
            keys = list(out.keys())[:5]
            return (True, str(keys) if keys else "ok", out)
        return (True, str(type(out).__name__), None)
    except RoostooAPIError as e:
        msg = str(e).strip()
        if msg == "no order matched":
            return (True, "ok (no orders)", None)
        if "no pending order" in msg.lower():
            return (True, "ok (no pending orders)", None)
        if "no order canceled" in msg.lower():
            return (True, "ok (no orders to cancel)", None)
        return (False, msg, None)
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}", None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test all Roostoo SDK methods using ROOSTOO_TEST_* credentials"
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env (default: repo root .env)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Include place_order and cancel_order (read-only by default)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print failures",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log full API response JSON for each test",
    )
    args = parser.parse_args()

    env_path = args.env_file or os.path.join(_repo_root, ".env")
    load_dotenv(env_path)

    api_key = os.environ.get(ENV_TEST_API_KEY, "").strip()
    secret_key = os.environ.get(ENV_TEST_SECRET_KEY, "").strip()
    base_url = (
        os.environ.get(ENV_TEST_BASE_URL) or DEFAULT_BASE_URL
    ).rstrip("/")

    if not api_key or not secret_key:
        print("Error: set ROOSTOO_TEST_API_KEY and ROOSTOO_TEST_SECRET_KEY in .env", file=sys.stderr)
        return 1

    client = RoostooClient(api_key=api_key, secret_key=secret_key, base_url=base_url)

    tests = [
        ("get_server_time", lambda: client.get_server_time()),
        ("get_exchange_info", lambda: client.get_exchange_info()),
        ("get_ticker()", lambda: client.get_ticker()),
        ("get_ticker(BTC/USD)", lambda: client.get_ticker("BTC/USD")),
        ("get_balance", lambda: client.get_balance()),
        ("get_pending_count", lambda: client.get_pending_count()),
        ("query_order(pending_only=True)", lambda: client.query_order(pending_only=True)),
        (
            "query_order(pending_only=False, limit=5)",
            lambda: client.query_order(pending_only=False, limit=5),
        ),
        ("cancel_order()", lambda: client.cancel_order()),
    ]

    if args.write:
        tests.extend([
            ("place_order(BTC/USD BUY 0.0001 MARKET)", lambda: client.place_order("BTC/USD", "BUY", 0.0001)),
            ("cancel_order(pair=BTC/USD)", lambda: client.cancel_order(pair="BTC/USD")),
        ])

    failed = 0
    for name, fn in tests:
        ok, msg, response = run(name, fn)
        if not ok:
            failed += 1
            print(f"FAIL {name}: {msg}")
        elif not args.quiet:
            print(f"OK   {name}: {msg}")
        if args.verbose and response is not None:
            print(json.dumps(response, indent=2))

    if failed:
        print(f"\n{failed} failure(s)", file=sys.stderr)
        return 1
    if not args.quiet:
        print("\nAll SDK methods passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
