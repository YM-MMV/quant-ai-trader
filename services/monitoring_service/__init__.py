"""Monitoring service (M23): performance tracking & degradation detection.

Reads paper / live-demo trade logs, computes rolling performance metrics, and
flags strategies that have degraded so they can be paused before they do more
damage. Pure, deterministic analysis — no AI, no MT5, no network.
"""
from services.monitoring_service.performance_monitor import (
    PerformanceMetrics,
    PerformanceMonitor,
)
from services.monitoring_service.degradation import (
    DegradationReport,
    DegradationThresholds,
    MonitorStatus,
    StrategyMonitor,
    StrategyPausedError,
    evaluate_degradation,
)

__all__ = [
    "PerformanceMetrics",
    "PerformanceMonitor",
    "DegradationThresholds",
    "DegradationReport",
    "MonitorStatus",
    "StrategyMonitor",
    "StrategyPausedError",
    "evaluate_degradation",
]
