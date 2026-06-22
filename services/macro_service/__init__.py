"""Macro service (M25): optional macro/news/risk context.

Currently houses the **optional** WorldMonitor client, which provides advisory
macro risk context only — never trading signals. WorldMonitor is disabled by
default; nothing in core code or tests depends on it. See
``docs/WORLDMONITOR_SETUP.md``.
"""
from services.macro_service.worldmonitor_client import (
    DisabledWorldMonitor,
    MockWorldMonitor,
    RealWorldMonitor,
    RiskContext,
    WorldMonitorClient,
    WorldMonitorConnectionError,
    WorldMonitorError,
    WorldMonitorNotAvailableError,
    load_worldmonitor,
    worldmonitor_configured,
)

__all__ = [
    "RiskContext",
    "WorldMonitorClient",
    "DisabledWorldMonitor",
    "MockWorldMonitor",
    "RealWorldMonitor",
    "load_worldmonitor",
    "worldmonitor_configured",
    "WorldMonitorError",
    "WorldMonitorNotAvailableError",
    "WorldMonitorConnectionError",
]
