"""Degradation detection & the paused-strategy gate (M23).

Turns :class:`~services.monitoring_service.performance_monitor.PerformanceMetrics`
into a verdict: is a strategy still healthy, or has it *degraded* badly enough to
pause? A paused strategy must not be allowed to open new paper or MT5 orders.

Two pieces:

* :func:`evaluate_degradation` — pure: metrics + thresholds → a
  :class:`DegradationReport` (``active`` / ``paused`` + the breached rules).
* :class:`StrategyMonitor` — stateful: ingests trade logs, evaluates each
  strategy, and remembers which are paused. It is the gate executors consult via
  :meth:`can_trade` / :meth:`assert_can_trade` before creating an order. Pausing
  is **sticky**: a degraded strategy stays paused until a human resumes it (via
  :meth:`resume`) — a lucky trade never silently un-pauses it.

Pure, deterministic — no AI, no MT5, no network.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.monitoring_service.performance_monitor import (
    PerformanceMetrics,
    PerformanceMonitor,
)


class MonitorStatus(str, Enum):
    """Operational state of a strategy under monitoring."""

    ACTIVE = "active"
    PAUSED = "paused"


class StrategyPausedError(RuntimeError):
    """Raised when a paused strategy attempts to create a new order."""


class DegradationThresholds(BaseModel):
    """Limits below/above which a strategy is considered degraded.

    A rule is only enforced once at least ``min_trades`` closed trades exist
    (so a strategy is not paused on a tiny, noisy sample). Optional thresholds
    (``None``) are skipped, as are checks with no supporting samples.
    """

    model_config = ConfigDict(extra="forbid")

    min_trades: int = Field(20, ge=0)
    min_win_rate: float = Field(0.35, ge=0.0, le=1.0)
    min_profit_factor: float = Field(1.0, ge=0.0)
    max_drawdown_pct: float = Field(0.25, ge=0.0)          # fraction of peak equity
    max_losing_streak: int = Field(6, ge=1)
    min_signal_hit_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    min_kronos_accuracy: Optional[float] = Field(None, ge=0.0, le=1.0)


class DegradationReport(BaseModel):
    """The verdict for one strategy."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    status: MonitorStatus
    evaluated: bool                      # False ⇒ not enough trades to judge yet
    breaches: list[str] = Field(default_factory=list)
    metrics: PerformanceMetrics

    @property
    def is_paused(self) -> bool:
        return self.status is MonitorStatus.PAUSED


def evaluate_degradation(
    metrics: PerformanceMetrics,
    thresholds: Optional[DegradationThresholds] = None,
    *,
    strategy_id: str = "strategy",
) -> DegradationReport:
    """Decide whether ``metrics`` indicate a strategy should be paused."""
    cfg = thresholds or DegradationThresholds()

    if metrics.n_closed < cfg.min_trades:
        return DegradationReport(
            strategy_id=strategy_id, status=MonitorStatus.ACTIVE,
            evaluated=False, breaches=[], metrics=metrics,
        )

    breaches: list[str] = []
    if metrics.win_rate < cfg.min_win_rate:
        breaches.append(
            f"win_rate {metrics.win_rate:.3f} < {cfg.min_win_rate}")
    # profit_factor None ⇒ no losing trades ⇒ healthy, not a breach.
    if metrics.profit_factor is not None and metrics.profit_factor < cfg.min_profit_factor:
        breaches.append(
            f"profit_factor {metrics.profit_factor:.3f} < {cfg.min_profit_factor}")
    if metrics.max_drawdown_pct > cfg.max_drawdown_pct:
        breaches.append(
            f"max_drawdown_pct {metrics.max_drawdown_pct:.3f} > {cfg.max_drawdown_pct}")
    if metrics.current_losing_streak >= cfg.max_losing_streak:
        breaches.append(
            f"losing_streak {metrics.current_losing_streak} >= {cfg.max_losing_streak}")
    if (cfg.min_signal_hit_rate is not None and metrics.signal_hit_rate is not None
            and metrics.signal_hit_rate < cfg.min_signal_hit_rate):
        breaches.append(
            f"signal_hit_rate {metrics.signal_hit_rate:.3f} < {cfg.min_signal_hit_rate}")
    if (cfg.min_kronos_accuracy is not None and metrics.kronos_accuracy is not None
            and metrics.kronos_accuracy < cfg.min_kronos_accuracy):
        breaches.append(
            f"kronos_accuracy {metrics.kronos_accuracy:.3f} < {cfg.min_kronos_accuracy}")

    status = MonitorStatus.PAUSED if breaches else MonitorStatus.ACTIVE
    return DegradationReport(
        strategy_id=strategy_id, status=status, evaluated=True,
        breaches=breaches, metrics=metrics,
    )


class StrategyMonitor:
    """Stateful degradation monitor + the gate that blocks paused strategies."""

    def __init__(
        self,
        *,
        thresholds: Optional[DegradationThresholds] = None,
        window: Optional[int] = None,
        initial_equity: float = 10_000.0,
    ) -> None:
        self.thresholds = thresholds or DegradationThresholds()
        self.monitor = PerformanceMonitor(window=window, initial_equity=initial_equity)
        self._status: dict[str, MonitorStatus] = {}
        self._reasons: dict[str, list[str]] = {}

    # -- evaluation -------------------------------------------------------- #
    def update(self, trades: Any) -> dict[str, DegradationReport]:
        """Recompute per-strategy degradation and pause any that breach.

        Pausing is sticky: a strategy already paused stays paused regardless of
        this run's verdict (it must be resumed manually).
        """
        reports: dict[str, DegradationReport] = {}
        for strategy, metrics in self.monitor.metrics_by_strategy(trades).items():
            report = evaluate_degradation(metrics, self.thresholds, strategy_id=strategy)
            already_paused = self._status.get(strategy) is MonitorStatus.PAUSED
            if report.is_paused or already_paused:
                self._status[strategy] = MonitorStatus.PAUSED
                if report.breaches:
                    self._reasons[strategy] = report.breaches
            else:
                self._status[strategy] = MonitorStatus.ACTIVE
            reports[strategy] = report
        return reports

    # -- the gate ---------------------------------------------------------- #
    def status(self, strategy_id: str) -> MonitorStatus:
        return self._status.get(strategy_id, MonitorStatus.ACTIVE)

    def is_paused(self, strategy_id: str) -> bool:
        return self.status(strategy_id) is MonitorStatus.PAUSED

    def can_trade(self, strategy_id: str) -> bool:
        """True if ``strategy_id`` is allowed to open new orders."""
        return not self.is_paused(strategy_id)

    def assert_can_trade(self, strategy_id: str) -> None:
        """Raise :class:`StrategyPausedError` if the strategy is paused.

        Executors (paper executor, MT5 gateway) call this before creating an
        order so a degraded strategy cannot open new paper or MT5 positions.
        """
        if self.is_paused(strategy_id):
            reasons = ", ".join(self._reasons.get(strategy_id, [])) or "degraded performance"
            raise StrategyPausedError(
                f"strategy {strategy_id!r} is paused ({reasons}); it cannot open new orders"
            )

    def paused_strategies(self) -> list[str]:
        return sorted(s for s, st in self._status.items() if st is MonitorStatus.PAUSED)

    # -- manual control ---------------------------------------------------- #
    def pause(self, strategy_id: str, *, reason: str = "manual pause") -> None:
        self._status[strategy_id] = MonitorStatus.PAUSED
        self._reasons[strategy_id] = [reason]

    def resume(self, strategy_id: str) -> None:
        """Re-activate a paused strategy (a deliberate human action)."""
        self._status[strategy_id] = MonitorStatus.ACTIVE
        self._reasons.pop(strategy_id, None)


__all__ = [
    "MonitorStatus",
    "DegradationThresholds",
    "DegradationReport",
    "evaluate_degradation",
    "StrategyMonitor",
    "StrategyPausedError",
]
