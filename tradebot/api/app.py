"""FastAPI application factory for the multi-wallet dashboard API.

Read routes are open on loopback; every MUTATION requires a valid token and
fails closed. Errors are redacted to a generic message + correlation id.
There is deliberately NO endpoint to edit the model endpoint or the DataBroker
allowlist — those are operator-controlled configuration (A09).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .security import (
    SECURITY_HEADERS,
    ApiSettings,
    new_correlation_id,
    origin_allowed,
    token_matches,
    validate_startup,
)
from .views import PortfolioView

logger = logging.getLogger("tradebot.api")

GENERIC_ERROR = "request could not be completed"


def create_app(view: PortfolioView, settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    validate_startup(settings)  # refuses insecure remote bind at startup

    app = FastAPI(title="tradebot", version="2", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.view = view

    def require_token(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        origin: Annotated[str | None, Header()] = None,
    ) -> None:
        """Mutation guard. Fails CLOSED on every failure path (A12)."""

        # Tokens must never travel in query strings.
        if any(k in request.query_params for k in ("token", "auth", "api_key")):
            raise HTTPException(status_code=400, detail="token must not be in query")
        if not origin_allowed(origin, settings):
            raise HTTPException(status_code=403, detail="origin not allowed")
        presented = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer "):]
        if not token_matches(settings.auth_token, presented):
            raise HTTPException(status_code=401, detail="unauthorized")

    app.state.require_token = require_token

    @app.middleware("http")
    async def _security_middleware(request: Request, call_next: Callable):
        # Content-Length is a hint, not a guarantee: a chunked/streamed request
        # omits it entirely. Check the declared length first (cheap rejection),
        # then enforce the real cap against the actual body so the limit cannot
        # be bypassed by dropping the header.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > settings.max_body_bytes:
                    return _too_large()
            except ValueError:
                return _too_large()
        elif request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            if len(body) > settings.max_body_bytes:
                return _too_large()

        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    def _too_large() -> JSONResponse:
        # Early returns must still carry the security headers.
        return JSONResponse({"error": "request too large"}, status_code=413,
                            headers=SECURITY_HEADERS)

    @app.exception_handler(Exception)
    async def _redact(request: Request, exc: Exception) -> JSONResponse:
        """A13: never leak internals. Detail goes to structured logs only."""

        cid = new_correlation_id()
        logger.exception("unhandled error", extra={"correlation_id": cid,
                                                   "path": request.url.path})
        return JSONResponse(
            {"error": GENERIC_ERROR, "correlation_id": cid}, status_code=500,
            headers=SECURITY_HEADERS,
        )

    _register_routes(app, view, require_token)
    return app


def _register_routes(app: FastAPI, view: PortfolioView, require_token) -> None:
    guarded = [Depends(require_token)]

    # -- dashboard shell + assets --------------------------------------------
    # Served from our own origin so the CSP's `script-src 'self'` is satisfied
    # without inline script. StaticFiles is read-only and cannot be mutated
    # through the API.
    static_dir = Path(__file__).resolve().parents[2] / "dashboard" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def dashboard() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    @app.get("/api/v2/system/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/v2/system/readiness")
    def readiness() -> dict:
        return view.readiness()

    @app.get("/api/v2/portfolio/summary")
    def portfolio_summary() -> dict:
        return view.portfolio_summary()

    @app.get("/api/v2/portfolio/insights")
    def portfolio_insights() -> dict:
        return view.portfolio_insights()

    @app.get("/api/v2/wallets")
    def wallets(kind: str | None = None) -> dict:
        return {"wallets": view.wallets(kind=kind)}

    @app.get("/api/v2/wallets/{wallet_id}")
    def wallet(wallet_id: str) -> dict:
        found = view.wallet(wallet_id)
        if found is None:
            raise HTTPException(status_code=404, detail="wallet not found")
        return found

    @app.get("/api/v2/wallets/{wallet_id}/equity")
    def wallet_equity(wallet_id: str) -> dict:
        return {"points": view.wallet_equity(wallet_id)}

    @app.get("/api/v2/wallets/{wallet_id}/orders")
    def wallet_orders(wallet_id: str) -> dict:
        return {"orders": view.wallet_orders(wallet_id)}

    @app.get("/api/v2/wallets/{wallet_id}/fills")
    def wallet_fills(wallet_id: str) -> dict:
        return {"fills": view.wallet_fills(wallet_id)}

    @app.get("/api/v2/wallets/{wallet_id}/ledger")
    def wallet_ledger(wallet_id: str) -> dict:
        return {"transactions": view.wallet_ledger(wallet_id)}

    @app.get("/api/v2/strategies")
    def strategies() -> dict:
        return {"strategies": view.strategies()}

    @app.get("/api/v2/strategies/{strategy_version_id}")
    def strategy(strategy_version_id: str) -> dict:
        found = view.strategy(strategy_version_id)
        if found is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        return found

    @app.get("/api/v2/lineage")
    def lineage() -> dict:
        return {"edges": view.lineage()}

    @app.get("/api/v2/evaluations")
    def evaluations(window: str | None = None) -> dict:
        return {"evaluations": view.evaluations(window=window)}

    @app.get("/api/v2/promotions")
    def promotions() -> dict:
        return {"promotions": view.promotions()}

    @app.get("/api/v2/reports/daily")
    def reports_daily(date: str | None = None) -> dict:
        return {"reports": view.reports_daily(date=date)}

    @app.get("/api/v2/reports/weekly")
    def reports_weekly(window: str | None = None) -> dict:
        return {"reports": view.reports_weekly(window=window)}

    @app.get("/api/v2/quarantines")
    def quarantines() -> dict:
        return {"quarantines": view.quarantines()}

    @app.get("/api/v2/data-sources/status")
    def data_sources() -> dict:
        return {"sources": view.data_sources()}

    @app.get("/api/v2/llm/status")
    def llm_status() -> dict:
        """Reports status only. Endpoint config is NOT editable here (A09)."""

        return view.llm_status()

    # -- mutations: all guarded, all fail closed (A12) ------------------------

    @app.post("/api/v2/reports/daily/refresh", dependencies=guarded)
    def refresh_daily(payload: dict) -> dict:
        date = payload.get("date")
        if not date:
            raise HTTPException(status_code=422, detail="date required")
        return view.refresh_daily(date)
