"""Backtest service: deterministic, realistic-friction strategy evaluation.

A small local backtester (used before integrating QuantDinger). No MT5, no
network, no AI — same inputs always produce the same result. Frictionless runs
are not the intent: every fill is priced through a :class:`CostModel`.
"""
from services.backtest_service.costs import CostModel, pip_size, point_size
from services.backtest_service.metrics import (
    BacktestMetrics,
    compute_metrics,
    max_drawdown,
)
from services.backtest_service.simple_backtester import (
    BacktestConfig,
    BacktestReport,
    BacktestSignal,
    ClosePolicy,
    Direction,
    SimpleBacktester,
    Trade,
)

__all__ = [
    "CostModel",
    "point_size",
    "pip_size",
    "BacktestMetrics",
    "compute_metrics",
    "max_drawdown",
    "BacktestConfig",
    "BacktestReport",
    "BacktestSignal",
    "ClosePolicy",
    "Direction",
    "SimpleBacktester",
    "Trade",
]
