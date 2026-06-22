"""Backtest routes (M20): run a strategy adapter with realistic friction.

Backtests use the local deterministic engine via the agent tools; the response
includes both the raw metrics and a single comparable score.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apps.agent.tools import run_backtest, score_backtest

router = APIRouter(prefix="/backtests", tags=["backtests"])


class BacktestRunRequest(BaseModel):
    strategy: str = Field(..., min_length=1, description="strategy adapter name")
    symbol: str = Field("EURUSD", min_length=1)
    timeframe: str = Field("H1", min_length=1)
    n: int = Field(400, ge=20, le=5000)
    stop_fraction: float = Field(0.01, gt=0, le=0.5)
    reward_ratio: float = Field(2.0, gt=0, le=10)


class ScoreOut(BaseModel):
    score: float
    grade: str
    components: dict[str, float]
    has_trades: bool


class BacktestRunResponse(BaseModel):
    strategy: str
    symbol: str
    timeframe: str
    n_bars: int
    num_trades: int
    rejected_signals: int
    metrics: dict
    final_equity: float | None = None
    score: ScoreOut


@router.post("/run", response_model=BacktestRunResponse)
def run(request: BacktestRunRequest) -> BacktestRunResponse:
    """Backtest a named strategy adapter and score the result."""
    try:
        result = run_backtest(
            request.strategy, request.symbol, request.timeframe, request.n,
            stop_fraction=request.stop_fraction, reward_ratio=request.reward_ratio,
        )
    except KeyError as exc:  # unknown adapter name
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    score = score_backtest(result)
    return BacktestRunResponse(**result, score=ScoreOut(**score))
