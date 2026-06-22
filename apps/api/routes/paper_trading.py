"""Paper-trading route (M20): create a paper trade — only after risk approval.

This is the only state-changing route, and it is **paper-only**. It delegates to
``apps.agent.tools.create_paper_trade``, which re-runs the deterministic
RiskManager itself: a rejected intent creates no trade, and there is no way to
pass a pre-approved decision (no self-bypass). No live order is ever sent.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from apps.agent.tools import create_paper_trade
from apps.api.routes.risk import OrderIntentIn, RiskContextIn

router = APIRouter(tags=["paper-trading"])


class PaperTradeRequest(BaseModel):
    intent: OrderIntentIn
    context: RiskContextIn = Field(default_factory=RiskContextIn)
    timeframe: str = Field("H1", min_length=1)


class PaperTradeResponse(BaseModel):
    created: bool
    approved: bool
    # Present only when the risk check approved and a trade was created:
    trade: Optional[dict] = None
    risk_decision: Optional[dict] = None
    # Present only when the risk check denied:
    reasons: Optional[list[str]] = None
    checks: Optional[dict[str, bool]] = None


@router.post("/paper-trades", response_model=PaperTradeResponse)
def create(request: PaperTradeRequest) -> PaperTradeResponse:
    """Create a paper trade if — and only if — the risk gate approves the intent."""
    ctx = request.context
    result = create_paper_trade(
        request.intent.to_tool_dict(),
        timeframe=request.timeframe,
        reference_price=ctx.reference_price,
        account_balance=ctx.account_balance,
        spread_points=ctx.spread_points,
        volatility=ctx.volatility,
        realized_daily_loss=ctx.realized_daily_loss,
        trades_today=ctx.trades_today,
        open_trades=tuple(tuple(p) for p in ctx.open_trades),
        strategy_approved=ctx.strategy_approved,
        strategy_applicability=ctx.strategy_applicability,
        allowlist=tuple(ctx.allowlist) if ctx.allowlist is not None else None,
    )
    return PaperTradeResponse(**result)
