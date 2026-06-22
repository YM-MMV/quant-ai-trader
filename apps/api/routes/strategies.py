"""Strategy routes (M20): the classified inventory and the runnable adapters."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from apps.agent.tools import list_strategy_adapters, list_strategy_inventory

router = APIRouter(prefix="/strategies", tags=["strategies"])


class InventoryItemOut(BaseModel):
    name: str
    category: str
    mt5_applicability: str
    porting_status: str
    supported_asset_classes: list[str]


class InventoryResponse(BaseModel):
    count: int
    strategies: list[InventoryItemOut]


class AdapterOut(BaseModel):
    name: str
    version: str
    category: str
    description: str
    supported_symbols: list[str] | None = None
    supported_timeframes: list[str] | None = None


class AdaptersResponse(BaseModel):
    count: int
    adapters: list[AdapterOut]


@router.get("/inventory", response_model=InventoryResponse)
def inventory() -> InventoryResponse:
    """List the statically-classified strategy inventory (read-only)."""
    return InventoryResponse(**list_strategy_inventory())


@router.get("/adapters", response_model=AdaptersResponse)
def adapters() -> AdaptersResponse:
    """List the runnable strategy adapters (the ones backtests accept)."""
    return AdaptersResponse(**list_strategy_adapters())
