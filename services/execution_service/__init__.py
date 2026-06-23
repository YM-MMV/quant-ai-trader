"""Execution service.

M13 adds paper-trade execution with full audit logging: an approved (or
rejected) :class:`~services.models.RiskDecision` is turned into a logged
:class:`~services.execution_service.trade_log.PaperTrade` and a matching trail
of :class:`~services.execution_service.audit_log.AuditEvent` records. No real
MT5 orders are ever placed here — paper only.
"""
import importlib
from typing import TYPE_CHECKING

from services.execution_service.audit_log import (
    AuditEvent,
    AuditEventType,
    AuditLog,
)
from services.execution_service.base_gateway import (
    AccountInfo,
    ExecutionGateway,
    GatewayError,
    GatewayNotConnectedError,
    OrderCheckResult,
    OrderRejectedError,
    OrderResult,
    Position,
    Quote,
    approval_problem,
)
from services.execution_service.mock_mt5_gateway import MockMT5Gateway
from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import PaperTrade, TradeLogStore

# The real MT5 gateway is the ONLY module that imports the (Windows-only)
# MetaTrader5 SDK at load. Expose its symbols lazily so that importing this
# package — and therefore the whole paper pipeline — never drags in the broker
# SDK. The gateway loads only on attribute access here, or when its submodule is
# imported directly (the live path). Enforced by the smoke pipeline import guard.
_LAZY_MT5_GATEWAY = {
    "MT5Gateway",
    "LIVE_LOCKS",
    "LiveTradingDisabledError",
    "MT5ConnectionError",
    "MT5GatewayError",
    "MT5NotAvailableError",
}


def __getattr__(name: str):
    if name in _LAZY_MT5_GATEWAY:
        module = importlib.import_module("services.execution_service.mt5_gateway")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_LAZY_MT5_GATEWAY))


if TYPE_CHECKING:  # for type checkers/IDEs only; not executed at runtime
    from services.execution_service.mt5_gateway import (
        LIVE_LOCKS,
        LiveTradingDisabledError,
        MT5ConnectionError,
        MT5Gateway,
        MT5GatewayError,
        MT5NotAvailableError,
    )

__all__ = [
    "PaperExecutionService",
    "PaperTrade",
    "TradeLogStore",
    "AuditLog",
    "AuditEvent",
    "AuditEventType",
    "ExecutionGateway",
    "MockMT5Gateway",
    "MT5Gateway",
    "LIVE_LOCKS",
    "LiveTradingDisabledError",
    "MT5GatewayError",
    "MT5NotAvailableError",
    "MT5ConnectionError",
    "AccountInfo",
    "Quote",
    "Position",
    "OrderCheckResult",
    "OrderResult",
    "approval_problem",
    "GatewayError",
    "GatewayNotConnectedError",
    "OrderRejectedError",
]
