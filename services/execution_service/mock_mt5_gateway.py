"""Mock MT5 execution gateway (M21) — deterministic, in-memory, **no broker package**.

A drop-in :class:`~services.execution_service.base_gateway.ExecutionGateway` used
to develop and test the execution path before any real order sending exists. It
simulates connecting, account/quote queries, and opening/closing positions
entirely in memory.

Guarantees (the milestone's rules):

* **Approval-gated.** Order placement goes through the base
  :meth:`~services.execution_service.base_gateway.ExecutionGateway.send_order`
  template, so an order without an approved, matching
  :class:`~services.models.RiskDecision` is rejected before any fill — the mock
  *cannot* send an unapproved order.
* **Logs everything.** Every method call (connect, queries, order checks, fills,
  rejections, closes) is appended to :attr:`events`. An optional
  :class:`~services.execution_service.audit_log.AuditLog` mirrors order events to
  the shared trail.
* **No real broker.** No MT5 broker package is imported here; fills come from a
  deterministic internal quote, nothing is sent anywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.execution_service.audit_log import AuditEventType, AuditLog
from services.execution_service.base_gateway import (
    AccountInfo,
    ExecutionGateway,
    GatewayError,
    GatewayNotConnectedError,
    OrderCheckResult,
    OrderResult,
    Position,
    Quote,
)
from services.models import OrderIntent, RiskDecision, Side
from services.risk_service.symbol_specs import get_symbol_spec

# Deterministic reference mid-prices for the mock feed (no market data needed).
DEFAULT_PRICES: dict[str, float] = {
    "EURUSD": 1.10000,
    "GBPUSD": 1.27000,
    "USDJPY": 150.000,
    "AUDUSD": 0.66000,
    "XAUUSD": 2000.00,
    "BTCUSD": 60000.0,
}
DEFAULT_SPREAD_POINTS = 10.0


class GatewayEvent(BaseModel):
    """One logged gateway action (the in-memory audit trail)."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    action: str
    status: str
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class MockMT5Gateway(ExecutionGateway):
    """An in-memory, deterministic stand-in for a real MT5 execution gateway."""

    def __init__(
        self,
        *,
        balance: float = 10_000.0,
        currency: str = "USD",
        leverage: int = 100,
        login: int = 1_000_000,
        server: str = "MockServer-Demo",
        prices: Optional[dict[str, float]] = None,
        spread_points: float = DEFAULT_SPREAD_POINTS,
        audit_log: Optional[AuditLog] = None,
    ) -> None:
        self._balance = float(balance)
        self._currency = currency
        self._leverage = int(leverage)
        self._login = int(login)
        self._server = server
        self._prices = dict(DEFAULT_PRICES)
        if prices:
            self._prices.update(prices)
        self._spread_points = float(spread_points)
        self._connected = False
        self._positions: dict[int, Position] = {}
        self._ticket = 0
        self.events: list[GatewayEvent] = []
        self.audit_log = audit_log

    # ------------------------------------------------------------------ #
    # Logging (the gateway logs everything it does)
    # ------------------------------------------------------------------ #
    def _log_event(
        self,
        action: str,
        *,
        status: str,
        detail: str = "",
        payload: Optional[dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> None:
        event = GatewayEvent(
            timestamp=now or datetime.now(timezone.utc),
            action=action, status=status, detail=detail, payload=payload or {},
        )
        self.events.append(event)
        if self.audit_log is not None:
            self._mirror_to_audit(event)

    def _mirror_to_audit(self, event: GatewayEvent) -> None:
        """Mirror order-relevant events to the shared AuditLog, if configured."""
        type_map = {
            ("send_order", "filled"): AuditEventType.EXECUTION_DECISION,
            ("send_order", "rejected"): AuditEventType.TRADE_REJECTED,
            ("close_position", "filled"): AuditEventType.EXECUTION_DECISION,
        }
        event_type = type_map.get((event.action, event.status))
        if event_type is not None:
            self.audit_log.record(
                event_type,
                payload={"action": event.action, "detail": event.detail, **event.payload},
                message=f"gateway {event.action}: {event.status}",
                now=event.timestamp,
            )

    def _require_connected(self) -> None:
        if not self._connected:
            raise GatewayNotConnectedError("gateway is not connected; call connect() first")

    # ------------------------------------------------------------------ #
    # Connection & queries
    # ------------------------------------------------------------------ #
    def connect(self, *, now: Optional[datetime] = None) -> bool:
        self._connected = True
        self._log_event("connect", status="ok",
                        detail=f"connected to {self._server}",
                        payload={"login": self._login, "server": self._server}, now=now)
        return True

    def get_quote(self, symbol: str) -> Quote:
        self._require_connected()
        spec = get_symbol_spec(symbol)
        point = spec.point_size if spec else 0.0001
        digits = spec.digits if spec else 5
        mid = self._prices.get(symbol, 1.0)
        half = self._spread_points * point / 2.0
        quote = Quote(
            symbol=symbol,
            bid=round(mid - half, digits),
            ask=round(mid + half, digits),
            spread_points=self._spread_points,
            time=datetime.now(timezone.utc),
        )
        self._log_event("get_quote", status="ok", detail=symbol,
                        payload={"bid": quote.bid, "ask": quote.ask})
        return quote

    def _contract_size(self, symbol: str) -> float:
        spec = get_symbol_spec(symbol)
        return spec.contract_size if spec else 1.0

    def _position_profit(self, pos: Position) -> tuple[float, float]:
        """Return (current_price, floating_profit) valuing ``pos`` at the quote."""
        spec = get_symbol_spec(pos.symbol)
        point = spec.point_size if spec else 0.0001
        digits = spec.digits if spec else 5
        mid = self._prices.get(pos.symbol, pos.entry_price)
        half = self._spread_points * point / 2.0
        bid, ask = round(mid - half, digits), round(mid + half, digits)
        contract = self._contract_size(pos.symbol)
        if pos.side == Side.BUY.value:
            current, profit = bid, (bid - pos.entry_price) * pos.volume * contract
        else:
            current, profit = ask, (pos.entry_price - ask) * pos.volume * contract
        return current, round(profit, 2)

    def positions(self, symbol: Optional[str] = None) -> list[Position]:
        self._require_connected()
        out: list[Position] = []
        for pos in self._positions.values():
            if symbol is not None and pos.symbol != symbol:
                continue
            current, profit = self._position_profit(pos)
            out.append(pos.model_copy(update={"price_current": current, "profit": profit}))
        self._log_event("positions", status="ok",
                        detail=symbol or "all", payload={"count": len(out)})
        return out

    def account_info(self) -> AccountInfo:
        self._require_connected()
        floating = 0.0
        margin = 0.0
        for pos in self._positions.values():
            _, profit = self._position_profit(pos)
            floating += profit
            margin += self._margin_required(pos.symbol, pos.volume, pos.entry_price)
        equity = round(self._balance + floating, 2)
        info = AccountInfo(
            login=self._login, server=self._server, currency=self._currency,
            leverage=self._leverage, balance=round(self._balance, 2), equity=equity,
            margin=round(margin, 2), free_margin=round(equity - margin, 2),
        )
        self._log_event("account_info", status="ok",
                        payload={"balance": info.balance, "equity": info.equity})
        return info

    def _margin_required(self, symbol: str, volume: float, price: float) -> float:
        contract = self._contract_size(symbol)
        return volume * contract * price / max(self._leverage, 1)

    # ------------------------------------------------------------------ #
    # Order check & placement
    # ------------------------------------------------------------------ #
    def order_check(
        self, intent: OrderIntent, decision: Optional[RiskDecision] = None
    ) -> OrderCheckResult:
        """Validate margin / price / approval without placing anything."""
        from services.execution_service.base_gateway import approval_problem

        reasons: list[str] = []
        if not self._connected:
            reasons.append("gateway is not connected")
        if get_symbol_spec(intent.symbol) is None:
            reasons.append(f"no contract spec for {intent.symbol!r}")

        quote = None
        margin_required = 0.0
        free_margin = 0.0
        if self._connected and get_symbol_spec(intent.symbol) is not None:
            quote = self.get_quote(intent.symbol)
            price = intent.price or (quote.ask if intent.side is Side.BUY else quote.bid)
            margin_required = self._margin_required(intent.symbol, intent.volume, price)
            free_margin = self.account_info().free_margin
            if margin_required > free_margin:
                reasons.append(
                    f"insufficient margin: need {margin_required:.2f}, free {free_margin:.2f}")

        problem = approval_problem(intent, decision)
        if problem is not None:
            reasons.append(problem)

        ok = not reasons
        result = OrderCheckResult(
            ok=ok, reasons=reasons, margin_required=round(margin_required, 2),
            free_margin=round(free_margin, 2),
            comment="order_check passed" if ok else "order_check failed",
        )
        self._log_event("order_check", status="ok" if ok else "failed",
                        detail=intent.symbol, payload={"ok": ok, "reasons": reasons})
        return result

    def _execute_order(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        *,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """Place an already-approved order (called only via the gated template)."""
        self._require_connected()
        if get_symbol_spec(intent.symbol) is None:
            self._log_event("send_order", status="rejected",
                            detail=f"no contract spec for {intent.symbol!r}",
                            payload={"symbol": intent.symbol})
            raise GatewayError(f"no contract spec for {intent.symbol!r}")

        quote = self.get_quote(intent.symbol)
        fill = quote.ask if intent.side is Side.BUY else quote.bid
        # An approved decision may have sized the position; honour it.
        volume = float(decision.approved_volume or intent.volume)

        self._ticket += 1
        ticket = self._ticket
        position = Position(
            ticket=ticket, symbol=intent.symbol, side=intent.side.value,
            volume=volume, entry_price=fill,
            stop_loss=intent.stop_loss, take_profit=intent.take_profit,
            open_time=now or datetime.now(timezone.utc), price_current=fill, profit=0.0,
        )
        self._positions[ticket] = position
        self._log_event("send_order", status="filled",
                        detail=f"opened {intent.side.value} {intent.symbol} @ {fill}",
                        payload={"ticket": ticket, "symbol": intent.symbol,
                                 "side": intent.side.value, "volume": volume, "price": fill},
                        now=now)
        return OrderResult(
            success=True, action="open", status="filled", symbol=intent.symbol,
            side=intent.side.value, volume=volume, price=fill, position_id=ticket,
            comment=f"opened paper position {ticket}",
        )

    def close_position(
        self, position_id: int, *, now: Optional[datetime] = None
    ) -> OrderResult:
        self._require_connected()
        pos = self._positions.get(position_id)
        if pos is None:
            self._log_event("close_position", status="rejected",
                            detail=f"no open position {position_id}",
                            payload={"position_id": position_id})
            raise GatewayError(f"no open position with ticket {position_id}")

        current, profit = self._position_profit(pos)
        self._balance = round(self._balance + profit, 2)
        del self._positions[position_id]
        self._log_event("close_position", status="filled",
                        detail=f"closed {pos.side} {pos.symbol} @ {current}",
                        payload={"ticket": position_id, "symbol": pos.symbol,
                                 "profit": profit, "price": current},
                        now=now)
        # Closing a position trades the opposite side.
        close_side = Side.SELL.value if pos.side == Side.BUY.value else Side.BUY.value
        return OrderResult(
            success=True, action="close", status="filled", symbol=pos.symbol,
            side=close_side, volume=pos.volume, price=current, position_id=position_id,
            profit=profit, comment=f"closed paper position {position_id}",
        )


__all__ = ["MockMT5Gateway", "GatewayEvent", "DEFAULT_PRICES", "DEFAULT_SPREAD_POINTS"]
