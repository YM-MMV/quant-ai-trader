"""Execution gateway interface (M21) — the contract before any real MT5 orders.

An :class:`ExecutionGateway` is the single seam through which the system could
ever talk to a broker. This module defines that contract plus the typed result
models; the mock implementation lives in
:mod:`services.execution_service.mock_mt5_gateway`, and a real MT5 gateway would
implement the same interface later (behind the hard live-trading locks).

**The approval gate is enforced here, once, for every gateway.** :meth:`send_order`
is a concrete template method: it refuses to place an order unless it is handed
an *approved* :class:`~services.models.RiskDecision` whose ``intent`` matches the
submitted one. Subclasses implement :meth:`_execute_order` (the actual placement)
and the logging hook :meth:`_log_event`; they cannot weaken the gate. This means
an unapproved order can never reach a broker through any gateway — not just the
mock.

No MT5 broker package is imported here (or in the mock); this is pure interface
+ data. The separate, read-only data bridge lives in the ``mt5_data`` module.
"""
from __future__ import annotations

import abc
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.models import OrderIntent, RiskDecision


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class GatewayError(RuntimeError):
    """Base class for execution-gateway failures."""


class GatewayNotConnectedError(GatewayError):
    """An operation was attempted before :meth:`ExecutionGateway.connect`."""


class OrderRejectedError(GatewayError):
    """An order was refused — most importantly, without an approved RiskDecision."""


# --------------------------------------------------------------------------- #
# Result models (typed, JSON-serialisable; no secrets)
# --------------------------------------------------------------------------- #
class _GwModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInfo(_GwModel):
    """Account summary. Carries no password/credentials — login id only."""

    login: int
    server: str
    currency: str = "USD"
    leverage: int = Field(100, gt=0)
    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0


class Quote(_GwModel):
    """A two-sided price for a symbol at a moment in time."""

    symbol: str = Field(..., min_length=1)
    bid: float = Field(..., gt=0)
    ask: float = Field(..., gt=0)
    spread_points: float = Field(0.0, ge=0)
    time: Optional[datetime] = None


class Position(_GwModel):
    """An open position held by the gateway."""

    ticket: int
    symbol: str = Field(..., min_length=1)
    side: str = Field(..., description="buy | sell")
    volume: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    stop_loss: Optional[float] = Field(None, gt=0)
    take_profit: Optional[float] = Field(None, gt=0)
    open_time: Optional[datetime] = None
    price_current: Optional[float] = Field(None, gt=0)
    profit: float = 0.0


class OrderCheckResult(_GwModel):
    """Pre-trade validation (margin / price / approval) — does not place anything."""

    ok: bool
    reasons: list[str] = Field(default_factory=list)
    margin_required: float = 0.0
    free_margin: float = 0.0
    comment: str = ""


class OrderResult(_GwModel):
    """The outcome of a send/close request."""

    success: bool
    action: str = Field(..., description="open | close")
    status: str = Field(..., description="filled | rejected")
    symbol: str
    side: str
    volume: float
    price: Optional[float] = None
    position_id: Optional[int] = None
    profit: Optional[float] = None
    reasons: list[str] = Field(default_factory=list)
    comment: str = ""


# --------------------------------------------------------------------------- #
# Approval gate (the one rule every gateway obeys)
# --------------------------------------------------------------------------- #
def approval_problem(
    intent: OrderIntent, decision: Optional[RiskDecision]
) -> Optional[str]:
    """Return why an order must be refused, or ``None`` if it may proceed.

    An order may only be sent when it carries a genuine, *approved* RiskDecision
    for *this* intent. This prevents both sending unapproved orders and reusing
    an approval issued for a different order.
    """
    if not isinstance(decision, RiskDecision):
        return "no RiskDecision supplied; orders require an approved risk decision"
    if not decision.approved:
        reasons = ", ".join(decision.reasons) or "unspecified"
        return f"RiskDecision is not approved ({reasons})"
    if decision.intent != intent:
        return "RiskDecision does not match the submitted order intent"
    return None


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class ExecutionGateway(abc.ABC):
    """Abstract broker gateway. The mock and any real MT5 gateway implement this."""

    # -- connection / queries (subclass-implemented) ----------------------- #
    @abc.abstractmethod
    def connect(self) -> bool:
        """Establish the gateway connection. Returns True on success."""

    @abc.abstractmethod
    def account_info(self) -> AccountInfo:
        """Return the current account summary (no credentials)."""

    @abc.abstractmethod
    def positions(self, symbol: Optional[str] = None) -> list[Position]:
        """Return open positions, optionally filtered by ``symbol``."""

    @abc.abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Return the current two-sided quote for ``symbol``."""

    @abc.abstractmethod
    def order_check(
        self, intent: OrderIntent, decision: Optional[RiskDecision] = None
    ) -> OrderCheckResult:
        """Validate an order (margin/price/approval) without placing it."""

    @abc.abstractmethod
    def close_position(
        self, position_id: int, *, now: Optional[datetime] = None
    ) -> OrderResult:
        """Close an open position by ticket id."""

    # -- order placement (template — gate enforced here) ------------------- #
    def send_order(
        self,
        intent: OrderIntent,
        decision: Optional[RiskDecision],
        *,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """Place an order — only with an approved RiskDecision for this intent.

        The approval gate is enforced here and cannot be overridden by a
        subclass. A rejected attempt is logged (via :meth:`_log_event`) and then
        raises :class:`OrderRejectedError`; an approved order is delegated to
        :meth:`_execute_order`.
        """
        problem = approval_problem(intent, decision)
        if problem is not None:
            self._log_event(
                "send_order",
                status="rejected",
                detail=problem,
                payload={
                    "symbol": getattr(intent, "symbol", None),
                    "side": getattr(getattr(intent, "side", None), "value", None),
                    "approved": bool(getattr(decision, "approved", False)),
                },
            )
            raise OrderRejectedError(problem)
        return self._execute_order(intent, decision, now=now, **kwargs)

    # -- subclass hooks ---------------------------------------------------- #
    @abc.abstractmethod
    def _execute_order(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        *,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """Actually place an already-approved order. Never called unapproved."""

    @abc.abstractmethod
    def _log_event(
        self,
        action: str,
        *,
        status: str,
        detail: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record a gateway action (the gateway logs *everything* it does)."""


__all__ = [
    "ExecutionGateway",
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
