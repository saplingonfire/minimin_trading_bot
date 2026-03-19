"""FastAPI server: GET-only API routes proxying Roostoo SDK, serves /dashboard UI."""

import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(_path: str | None = None) -> bool:
        return False

# Load .env from repo root so ROOSTOO_TEST_* / ROOSTOO_* are set when running uvicorn
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from roostoo.client import RoostooClient
from roostoo.exceptions import RoostooAPIError

# Credential env (same as bot)
ENV_LIVE = "DASHBOARD_USE_LIVE"
ENV_API_KEY = "ROOSTOO_API_KEY"
ENV_SECRET_KEY = "ROOSTOO_SECRET_KEY"
ENV_BASE_URL = "ROOSTOO_BASE_URL"
ENV_TEST_API_KEY = "ROOSTOO_TEST_API_KEY"
ENV_TEST_SECRET_KEY = "ROOSTOO_TEST_SECRET_KEY"
ENV_TEST_BASE_URL = "ROOSTOO_TEST_BASE_URL"
DEFAULT_BASE_URL = "https://mock-api.roostoo.com"

logger = logging.getLogger(__name__)


def _api_error_status(e: RoostooAPIError) -> int:
    """Map RoostooAPIError to HTTP status: use upstream code or 400 for API Success:false."""
    code = getattr(e, "status_code", None)
    if code is not None and 400 <= code < 600:
        return code
    # API returned 200 with Success: false (e.g. bad signature, invalid key)
    return 400


def _handle_roostoo_error(e: RoostooAPIError, path: str) -> None:
    """Log Roostoo API failure before raising HTTPException."""
    logger.warning(
        "Roostoo API error on %s: %s (status_code=%s)",
        path,
        str(e),
        getattr(e, "status_code", None),
    )


def _parse_bool(s: str | None) -> bool:
    if s is None:
        return False
    return s.strip().lower() in ("1", "true", "yes")


def _get_client() -> RoostooClient:
    """Build RoostooClient from env. Uses test credentials unless DASHBOARD_USE_LIVE=true."""
    use_live = _parse_bool(os.environ.get(ENV_LIVE))
    if use_live:
        api_key = os.environ.get(ENV_API_KEY, "")
        secret_key = os.environ.get(ENV_SECRET_KEY, "")
        base_url = (os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
    else:
        api_key = os.environ.get(ENV_TEST_API_KEY, "")
        secret_key = os.environ.get(ENV_TEST_SECRET_KEY, "")
        base_url = (
            os.environ.get(ENV_TEST_BASE_URL) or DEFAULT_BASE_URL
        ).rstrip("/")
    if not api_key or not secret_key:
        raise ValueError(
            "Set ROOSTOO_TEST_API_KEY and ROOSTOO_TEST_SECRET_KEY (or DASHBOARD_USE_LIVE=true "
            "with ROOSTOO_API_KEY and ROOSTOO_SECRET_KEY)"
        )
    return RoostooClient(api_key=api_key, secret_key=secret_key, base_url=base_url)


app = FastAPI(title="Roostoo Dashboard API", version="0.1.0")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/dashboard", response_class=RedirectResponse)
def dashboard_redirect() -> RedirectResponse:
    """Redirect /dashboard to /dashboard/ so static index is served."""
    return RedirectResponse(url="/dashboard/", status_code=302)


# Mount static files under /dashboard; html=True serves index.html for /dashboard/
if _STATIC_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_STATIC_DIR), html=True), name="dashboard")


@app.get("/api/server_time")
def api_server_time() -> dict:
    """GET server time and connectivity."""
    try:
        return _get_client().get_server_time()
    except RoostooAPIError as e:
        _handle_roostoo_error(e, "/api/server_time")
        raise HTTPException(
            status_code=_api_error_status(e),
            detail={"message": str(e), "response_body": getattr(e, "response_body", None)},
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail={"message": str(e)})


@app.get("/api/exchange_info")
def api_exchange_info() -> dict:
    """GET exchange info (symbols, rules)."""
    try:
        return _get_client().get_exchange_info()
    except RoostooAPIError as e:
        _handle_roostoo_error(e, "/api/exchange_info")
        raise HTTPException(
            status_code=_api_error_status(e),
            detail={"message": str(e), "response_body": getattr(e, "response_body", None)},
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail={"message": str(e)})


@app.get("/api/ticker")
def api_ticker(pair: str | None = Query(None)) -> dict:
    """GET ticker for one pair or all pairs."""
    try:
        return _get_client().get_ticker(pair)
    except RoostooAPIError as e:
        _handle_roostoo_error(e, "/api/ticker")
        raise HTTPException(
            status_code=_api_error_status(e),
            detail={"message": str(e), "response_body": getattr(e, "response_body", None)},
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail={"message": str(e)})


@app.get("/api/balance")
def api_balance() -> dict:
    """GET current wallet balance (signed)."""
    try:
        return _get_client().get_balance()
    except RoostooAPIError as e:
        _handle_roostoo_error(e, "/api/balance")
        raise HTTPException(
            status_code=_api_error_status(e),
            detail={"message": str(e), "response_body": getattr(e, "response_body", None)},
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail={"message": str(e)})


@app.get("/api/pending_count")
def api_pending_count() -> dict:
    """GET pending order count (signed)."""
    try:
        return _get_client().get_pending_count()
    except RoostooAPIError as e:
        _handle_roostoo_error(e, "/api/pending_count")
        raise HTTPException(
            status_code=_api_error_status(e),
            detail={"message": str(e), "response_body": getattr(e, "response_body", None)},
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail={"message": str(e)})


@app.get("/api/orders")
def api_orders(
    pair: str | None = Query(None),
    pending_only: bool | None = Query(None),
    limit: int | None = Query(50, ge=1, le=200),
    offset: int | None = Query(None, ge=0),
) -> dict:
    """GET order history or pending orders (signed)."""
    try:
        client = _get_client()
        return client.query_order(
            pair=pair,
            pending_only=pending_only,
            limit=limit,
            offset=offset,
        )
    except RoostooAPIError as e:
        _handle_roostoo_error(e, "/api/orders")
        raise HTTPException(
            status_code=_api_error_status(e),
            detail={"message": str(e), "response_body": getattr(e, "response_body", None)},
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail={"message": str(e)})
