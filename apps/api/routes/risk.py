"""Risk routes (M20): run the deterministic RiskManager over an order intent.

The risk config is loaded read-only by the tool layer — the API exposes **no**
way to change risk limits, and there is no live-execution route. The shared
request models here are reused by the paper-trading route.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from apps.agent.tools import risk_check_order_intent

router = APIRouter(prefix="/risk", tags=["risk"])


# --------------------------------------------------------------------------- #
# Shared request models (also used by paper_trading.py)
# --------------------------------------------------------------------------- #
class OrderIntentIn(BaseModel):
    """A proposed order. Stop loss and take profit are mandatory."""

    symbol: str = Field(..., min_length=1)
    side: str = Field(..., description="buy | sell")
    volume: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    order_type: str = Field("market", description="market | limit | stop")
    price: Optional[float] = Field(None, gt=0)
    strategy_id: Optional[str] = None
    comment: str = ""

    def to_tool_dict(self) -> dict:
        return self.model_dump()


class RiskContextIn(BaseModel):
    """Runtime state the risk limits are evaluated against (no secrets)."""

    reference_price: Optional[float] = Field(None, gt=0)
    account_balance: Optional[float] = Field(None, gt=0)
    spread_points: float = Field(0.0, ge=0)
    volatility: Optional[float] = Field(None, ge=0)
    realized_daily_loss: float = Field(0.0, ge=0)
    trades_today: int = Field(0, ge=0)
    open_trades: list[tuple[str, str]] = Field(default_factory=list)
    strategy_approved: bool = True
    strategy_applicability: str = "direct"
    allowlist: Optional[list[str]] = None


class RiskCheckRequest(BaseModel):
    intent: OrderIntentIn
    context: RiskContextIn = Field(default_factory=RiskContextIn)


class RiskDecisionResponse(BaseModel):
    approved: bool
    mode: str
    reasons: list[str]
    checks: dict[str, bool]
    approved_volume: Optional[float] = None


def run_risk_check(intent: OrderIntentIn, context: RiskContextIn) -> dict:
    """Call the tool-layer risk check and return its decision dict."""
    return risk_check_order_intent(
        intent.to_tool_dict(),
        reference_price=context.reference_price,
        account_balance=context.account_balance,
        spread_points=context.spread_points,
        volatility=context.volatility,
        realized_daily_loss=context.realized_daily_loss,
        trades_today=context.trades_today,
        open_trades=tuple(tuple(p) for p in context.open_trades),
        strategy_approved=context.strategy_approved,
        strategy_applicability=context.strategy_applicability,
        allowlist=tuple(context.allowlist) if context.allowlist is not None else None,
    )


@router.post("/check", response_model=RiskDecisionResponse)
def check(request: RiskCheckRequest) -> RiskDecisionResponse:
    """Ask the RiskManager to approve or deny a proposed order intent."""
    decision = run_risk_check(request.intent, request.context)
    return RiskDecisionResponse(
        approved=decision["approved"],
        mode=decision["mode"],
        reasons=decision["reasons"],
        checks=decision["checks"],
        approved_volume=decision.get("approved_volume"),
    )
