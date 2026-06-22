"""FastAPI backend (M20) — local API for data, strategies, backtests, risk & paper trades.

A thin, **paper-only** HTTP layer over the existing services (via the agent tool
layer). Mounted routers expose read endpoints for market data and strategies, and
write endpoints for backtests, risk checks and paper trades.

Safety, by construction:

* **No live-execution endpoint.** Nothing here can place a real order; the only
  state-changing route creates *paper* trades and only after the RiskManager
  approves them.
* **No secrets exposed.** Endpoints return public config (symbols, modes) and
  computed results only — never ``.env`` settings (credentials, API keys).

Run locally::

    uvicorn apps.api.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from apps.api.routes import backtests, data, paper_trading, risk, strategies
from services.config_loader import load_app_config

API_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    """Health payload — deliberately free of any secret/credential fields."""

    status: str
    app_name: str
    environment: str
    trading_mode: str
    api_version: str
    live_trading: bool


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="quant-ai-trader API",
        version=API_VERSION,
        description="Local, paper-only API for data, strategies, backtests and risk.",
    )

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        """Liveness + safe, non-secret system info."""
        cfg = load_app_config()
        return HealthResponse(
            status="ok",
            app_name=cfg.app_name,
            environment=cfg.environment,
            trading_mode=cfg.default_mode.value,
            api_version=API_VERSION,
            live_trading=False,  # the API is paper-only; never live
        )

    app.include_router(data.router)
    app.include_router(strategies.router)
    app.include_router(backtests.router)
    app.include_router(risk.router)
    app.include_router(paper_trading.router)
    return app


app = create_app()


__all__ = ["app", "create_app", "API_VERSION", "HealthResponse"]
