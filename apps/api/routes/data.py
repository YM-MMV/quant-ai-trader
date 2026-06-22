"""Market-data routes (M20): symbols, candles, features.

All data is the deterministic, offline sample feed behind the agent tools — no
MT5, no network, no secrets. ``/symbols`` reads the canonical
``config/symbols.yaml`` (public config only).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from apps.agent.tools import get_candles, get_market_features
from services.config_loader import load_symbols_config

router = APIRouter(tags=["data"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class SymbolOut(BaseModel):
    symbol: str
    asset_class: str
    allowlisted: bool
    enabled: bool


class SymbolsResponse(BaseModel):
    count: int
    symbols: list[SymbolOut]


class CandleOut(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float


class CandlesResponse(BaseModel):
    symbol: str
    timeframe: str
    count: int
    candles: list[CandleOut]


class FeaturesResponse(BaseModel):
    symbol: str
    timeframe: str
    timestamp: str
    session: str
    features: dict[str, float]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/symbols", response_model=SymbolsResponse)
def list_symbols() -> SymbolsResponse:
    """List canonical symbols and whether each is allowlisted for trading."""
    cfg = load_symbols_config()
    allowlist = set(cfg.allowlist)
    symbols = [
        SymbolOut(
            symbol=name,
            asset_class=spec.asset_class.value,
            allowlisted=name in allowlist,
            enabled=spec.enabled,
        )
        for name, spec in sorted(cfg.symbols.items())
    ]
    return SymbolsResponse(count=len(symbols), symbols=symbols)


@router.get("/candles", response_model=CandlesResponse)
def candles(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("H1", min_length=1),
    n: int = Query(200, ge=1, le=5000),
) -> CandlesResponse:
    """Return recent OHLCV candles (deterministic offline sample data)."""
    return CandlesResponse(**get_candles(symbol, timeframe, n))


@router.get("/features", response_model=FeaturesResponse)
def features(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("H1", min_length=1),
    n: int = Query(200, ge=20, le=5000),
) -> FeaturesResponse:
    """Return the latest causal technical features for a symbol/timeframe."""
    return FeaturesResponse(**get_market_features(symbol, timeframe, n))
