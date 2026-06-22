"""Execution service.

M13 adds paper-trade execution with full audit logging: an approved (or
rejected) :class:`~services.models.RiskDecision` is turned into a logged
:class:`~services.execution_service.trade_log.PaperTrade` and a matching trail
of :class:`~services.execution_service.audit_log.AuditEvent` records. No real
MT5 orders are ever placed here — paper only.
"""
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

__all__ = [
    "PaperExecutionService",
    "PaperTrade",
    "TradeLogStore",
    "AuditLog",
    "AuditEvent",
    "AuditEventType",
    "ExecutionGateway",
    "MockMT5Gateway",
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
