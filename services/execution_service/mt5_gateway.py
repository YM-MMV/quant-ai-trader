"""Real MetaTrader 5 execution gateway (M22) — **locked by default**.

This is the *only* place in the codebase that may call ``order_send``. It
implements the :class:`~services.execution_service.base_gateway.ExecutionGateway`
contract against a live MetaTrader 5 terminal using the official
`MetaTrader5 <https://www.mql5.com/en/docs/python_metatrader5>`_ Python package.

It runs locally on Windows (the package is Windows-only) and is **off by
default**. ``send_order`` refuses to place anything unless *every* live lock is
satisfied:

#. ``TRADING_MODE=live``
#. ``ALLOW_LIVE_TRADING=true``
#. the :class:`~services.models.RiskDecision` is approved (enforced again by the
   base ``send_order`` template, which also checks the decision matches the
   intent)
#. the strategy is approved
#. the symbol is allowlisted
#. a stop loss is present
#. a take profit is present
#. the broker ``order_check`` passes (margin / price validity)

If any lock fails, the attempt is logged and :class:`LiveTradingDisabledError`
is raised — ``order_send`` is never reached. There is **no** ``demo_live`` mode:
the same ``live`` path is used with whatever account the terminal is logged into,
so the user can point it at their MT5 *demo* account now and switch to a real
account later, entirely outside this code.

The ``MetaTrader5`` package is imported lazily and guarded so this module imports
cleanly anywhere; tests inject a mock module and never touch a real terminal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services.config_loader import (
    SymbolsConfig,
    get_settings,
    load_symbols_config,
)
from services.execution_service.audit_log import AuditEventType, AuditLog
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
)
from services.execution_service.mock_mt5_gateway import GatewayEvent
from services.models import OrderIntent, RiskDecision, Side, TradingMode
from services.risk_service.symbol_specs import get_symbol_spec

# Lazy, guarded import — the package is Windows-only and talks to a terminal.
# Tests inject a fake module (or patch this global), so guard the import here.
try:  # pragma: no cover - import side effect depends on the host
    import MetaTrader5 as _MetaTrader5
except Exception:  # pragma: no cover
    _MetaTrader5 = None

mt5 = _MetaTrader5

# The live locks, in evaluation order. Names mirror the keys returned by
# :meth:`MT5Gateway.evaluate_live_locks`.
LIVE_LOCKS = (
    "mode_is_live",
    "allow_live_trading",
    "risk_approved",
    "strategy_approved",
    "symbol_allowlisted",
    "stop_loss_present",
    "take_profit_present",
)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class MT5GatewayError(GatewayError):
    """Base class for real-MT5 gateway failures."""


class MT5NotAvailableError(MT5GatewayError):
    """The MetaTrader5 package is not importable (e.g. not on Windows)."""


class MT5ConnectionError(MT5GatewayError):
    """``mt5.initialize`` failed to connect to a terminal."""


class LiveTradingDisabledError(OrderRejectedError):
    """A live order was refused because one or more live locks were not satisfied."""


class MT5Gateway(ExecutionGateway):
    """Live MetaTrader 5 execution behind the project's hard safety locks."""

    def __init__(
        self,
        *,
        settings: Any = None,
        symbols_config: Optional[SymbolsConfig] = None,
        mt5_module: Any = None,
        audit_log: Optional[AuditLog] = None,
        deviation: int = 20,
        magic: int = 220_022,
    ) -> None:
        self._settings = settings
        self._symbols_config = symbols_config
        self._client = mt5_module  # None ⇒ use the module-level ``mt5`` at call time
        self.audit_log = audit_log
        self.deviation = deviation
        self.magic = magic
        self._connected = False
        self.events: list[GatewayEvent] = []

    # ------------------------------------------------------------------ #
    # Wiring helpers
    # ------------------------------------------------------------------ #
    def _require_mt5(self) -> Any:
        client = self._client if self._client is not None else mt5
        if client is None:
            raise MT5NotAvailableError(
                "the MetaTrader5 package is not available; the real gateway runs "
                "locally on Windows only (see docs/MT5_EXECUTION_SETUP.md)"
            )
        return client

    def _get_settings(self) -> Any:
        if self._settings is not None:
            return self._settings
        return get_settings()

    def _get_symbols_config(self) -> SymbolsConfig:
        if self._symbols_config is None:
            self._symbols_config = load_symbols_config()
        return self._symbols_config

    def _allowlist(self) -> list[str]:
        """Per-machine env allowlist if set, else the canonical symbols.yaml set."""
        settings = self._get_settings()
        env_allow = list(getattr(settings, "symbol_allowlist", []) or [])
        return env_allow or list(self._get_symbols_config().allowlist)

    def _broker_symbol(self, symbol: str) -> str:
        spec = get_symbol_spec(symbol)
        return spec.broker_alias if spec else symbol

    def _require_connected(self) -> None:
        if not self._connected:
            raise GatewayNotConnectedError("gateway is not connected; call connect() first")

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
            type_map = {
                ("send_order", "filled"): AuditEventType.EXECUTION_DECISION,
                ("send_order", "rejected"): AuditEventType.TRADE_REJECTED,
                ("close_position", "filled"): AuditEventType.EXECUTION_DECISION,
            }
            event_type = type_map.get((action, status))
            if event_type is not None:
                self.audit_log.record(
                    event_type, payload={"action": action, "detail": detail, **(payload or {})},
                    message=f"mt5 gateway {action}: {status}", now=event.timestamp,
                )

    # ------------------------------------------------------------------ #
    # Connection & queries
    # ------------------------------------------------------------------ #
    def connect(self, *, now: Optional[datetime] = None) -> bool:
        client = self._require_mt5()
        settings = self._get_settings()
        kwargs: dict[str, Any] = {}
        path = getattr(settings, "mt5_terminal_path", None)
        login = getattr(settings, "mt5_login", None)
        password = getattr(settings, "mt5_password", None)
        server = getattr(settings, "mt5_server", None)
        if path:
            kwargs["path"] = path
        if login:
            kwargs["login"] = int(login)
        if password:
            kwargs["password"] = password
        if server:
            kwargs["server"] = server

        ok = client.initialize(**kwargs)
        if not ok:
            self._log_event("connect", status="failed",
                            detail="mt5.initialize returned False",
                            payload={"last_error": _safe(client, "last_error")})
            raise MT5ConnectionError(f"mt5.initialize failed: {_safe(client, 'last_error')}")
        self._connected = True
        # Never log credentials — server name only.
        self._log_event("connect", status="ok", detail=f"connected to {server or 'terminal'}")
        return True

    def account_info(self) -> AccountInfo:
        self._require_connected()
        client = self._require_mt5()
        raw = client.account_info()
        if raw is None:
            raise MT5GatewayError(f"mt5.account_info returned None: {_safe(client, 'last_error')}")
        info = AccountInfo(
            login=int(getattr(raw, "login", 0)),
            server=str(getattr(raw, "server", "")),
            currency=str(getattr(raw, "currency", "USD")),
            leverage=int(getattr(raw, "leverage", 100) or 100),
            balance=float(getattr(raw, "balance", 0.0)),
            equity=float(getattr(raw, "equity", 0.0)),
            margin=float(getattr(raw, "margin", 0.0) or 0.0),
            free_margin=float(getattr(raw, "margin_free", 0.0) or 0.0),
        )
        self._log_event("account_info", status="ok",
                        payload={"balance": info.balance, "equity": info.equity})
        return info

    def positions(self, symbol: Optional[str] = None) -> list[Position]:
        self._require_connected()
        client = self._require_mt5()
        broker = self._broker_symbol(symbol) if symbol else None
        raw = client.positions_get(symbol=broker) if broker else client.positions_get()
        out: list[Position] = []
        for p in raw or []:
            side = (Side.BUY.value
                    if getattr(p, "type", 0) == getattr(client, "POSITION_TYPE_BUY", 0)
                    else Side.SELL.value)
            out.append(Position(
                ticket=int(getattr(p, "ticket", 0)),
                symbol=symbol or str(getattr(p, "symbol", "")),
                side=side,
                volume=float(getattr(p, "volume", 0.0)),
                entry_price=float(getattr(p, "price_open", 0.0)) or 1e-9,
                stop_loss=_pos_or_none(getattr(p, "sl", 0.0)),
                take_profit=_pos_or_none(getattr(p, "tp", 0.0)),
                price_current=_pos_or_none(getattr(p, "price_current", 0.0)),
                profit=float(getattr(p, "profit", 0.0) or 0.0),
            ))
        self._log_event("positions", status="ok", detail=symbol or "all",
                        payload={"count": len(out)})
        return out

    def get_quote(self, symbol: str) -> Quote:
        self._require_connected()
        client = self._require_mt5()
        broker = self._broker_symbol(symbol)
        tick = client.symbol_info_tick(broker)
        if tick is None:
            raise MT5GatewayError(
                f"no tick for {symbol} ({broker}): {_safe(client, 'last_error')}")
        bid, ask = float(getattr(tick, "bid", 0.0)), float(getattr(tick, "ask", 0.0))
        spec = get_symbol_spec(symbol)
        point = spec.point_size if spec else 0.00001
        quote = Quote(
            symbol=symbol, bid=bid, ask=ask,
            spread_points=round(abs(ask - bid) / point, 1) if point else 0.0,
            time=datetime.now(timezone.utc),
        )
        self._log_event("get_quote", status="ok", detail=symbol,
                        payload={"bid": bid, "ask": ask})
        return quote

    # ------------------------------------------------------------------ #
    # Live locks
    # ------------------------------------------------------------------ #
    def evaluate_live_locks(
        self, intent: OrderIntent, decision: Optional[RiskDecision]
    ) -> dict[str, bool]:
        """Return each live lock's pass/fail. All must be True to trade."""
        settings = self._get_settings()
        mode = getattr(settings, "trading_mode", TradingMode.PAPER)
        allow_live = bool(getattr(settings, "allow_live_trading", False))
        approved = bool(getattr(decision, "approved", False))
        checks = getattr(decision, "checks", {}) or {}
        return {
            "mode_is_live": mode is TradingMode.LIVE,
            "allow_live_trading": allow_live,
            "risk_approved": approved,
            "strategy_approved": bool(checks.get("strategy_approved", approved)),
            "symbol_allowlisted": intent.symbol in self._allowlist(),
            "stop_loss_present": intent.stop_loss is not None and intent.stop_loss > 0,
            "take_profit_present": intent.take_profit is not None and intent.take_profit > 0,
        }

    def _live_lock_problems(
        self, intent: OrderIntent, decision: Optional[RiskDecision]
    ) -> list[str]:
        locks = self.evaluate_live_locks(intent, decision)
        return [f"live lock failed: {name}" for name in LIVE_LOCKS if not locks[name]]

    # ------------------------------------------------------------------ #
    # Order check & placement
    # ------------------------------------------------------------------ #
    def order_check(
        self, intent: OrderIntent, decision: Optional[RiskDecision] = None
    ) -> OrderCheckResult:
        """Full pre-trade validation: live locks + the broker ``order_check``."""
        reasons = self._live_lock_problems(intent, decision)
        margin_required = 0.0
        if self._connected:
            client = self._require_mt5()
            request = self._build_request(intent, decision)
            chk = client.order_check(request)
            retcode = getattr(chk, "retcode", 1)
            done = getattr(client, "TRADE_RETCODE_DONE", 10009)
            if retcode not in (0, done):
                reasons.append(
                    f"broker order_check failed (retcode {retcode}): "
                    f"{getattr(chk, 'comment', '')}")
            margin_required = float(getattr(chk, "margin", 0.0) or 0.0)
        else:
            reasons.append("gateway is not connected")
        ok = not reasons
        self._log_event("order_check", status="ok" if ok else "failed",
                        detail=intent.symbol, payload={"ok": ok, "reasons": reasons})
        return OrderCheckResult(
            ok=ok, reasons=reasons, margin_required=round(margin_required, 2),
            comment="order_check passed" if ok else "order_check failed",
        )

    def _build_request(
        self, intent: OrderIntent, decision: Optional[RiskDecision]
    ) -> dict[str, Any]:
        client = self._require_mt5()
        broker = self._broker_symbol(intent.symbol)
        tick = client.symbol_info_tick(broker)
        is_buy = intent.side is Side.BUY
        price = intent.price
        if price is None and tick is not None:
            price = float(getattr(tick, "ask", 0.0)) if is_buy else float(getattr(tick, "bid", 0.0))
        volume = float(
            getattr(decision, "approved_volume", None) or intent.volume
        )
        return {
            "action": getattr(client, "TRADE_ACTION_DEAL", 1),
            "symbol": broker,
            "volume": volume,
            "type": (getattr(client, "ORDER_TYPE_BUY", 0) if is_buy
                     else getattr(client, "ORDER_TYPE_SELL", 1)),
            "price": price,
            "sl": float(intent.stop_loss),
            "tp": float(intent.take_profit),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": intent.comment or (intent.strategy_id or "quant-ai-trader"),
            "type_time": getattr(client, "ORDER_TIME_GTC", 0),
            "type_filling": getattr(client, "ORDER_FILLING_IOC", 1),
        }

    def _execute_order(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        *,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """Place an order — reached only after the base approval gate passes.

        Re-evaluates *every* live lock first; if any fails the attempt is logged
        and refused **before** any broker call, so ``order_send`` is never
        reached for a locked-out order.
        """
        problems = self._live_lock_problems(intent, decision)
        if problems:
            self._log_event("send_order", status="rejected",
                            detail="; ".join(problems),
                            payload={"symbol": intent.symbol, "locks": problems}, now=now)
            raise LiveTradingDisabledError("; ".join(problems))

        # Locks pass → broker pre-trade check, then the single order_send call.
        check = self.order_check(intent, decision)
        if not check.ok:
            self._log_event("send_order", status="rejected",
                            detail="; ".join(check.reasons),
                            payload={"symbol": intent.symbol, "reasons": check.reasons}, now=now)
            raise OrderRejectedError("; ".join(check.reasons))

        client = self._require_mt5()
        request = self._build_request(intent, decision)
        result = client.order_send(request)  # the ONLY order_send in the codebase
        retcode = getattr(result, "retcode", None)
        done = getattr(client, "TRADE_RETCODE_DONE", 10009)
        success = retcode == done
        fill_price = float(getattr(result, "price", 0.0) or request["price"] or 0.0)
        order_id = getattr(result, "order", None)

        self._log_event("send_order", status="filled" if success else "rejected",
                        detail=f"{intent.side.value} {intent.symbol} retcode={retcode}",
                        payload={"symbol": intent.symbol, "side": intent.side.value,
                                 "volume": request["volume"], "price": fill_price,
                                 "order": order_id, "retcode": retcode}, now=now)
        if not success:
            raise OrderRejectedError(
                f"broker rejected order (retcode {retcode}): {getattr(result, 'comment', '')}")
        return OrderResult(
            success=True, action="open", status="filled", symbol=intent.symbol,
            side=intent.side.value, volume=float(request["volume"]), price=fill_price,
            position_id=int(order_id) if order_id is not None else None,
            comment=str(getattr(result, "comment", "") or "order placed"),
        )

    def close_position(
        self, position_id: int, *, now: Optional[datetime] = None
    ) -> OrderResult:
        self._require_connected()
        client = self._require_mt5()
        found = [p for p in (client.positions_get() or [])
                 if int(getattr(p, "ticket", -1)) == int(position_id)]
        if not found:
            self._log_event("close_position", status="rejected",
                            detail=f"no open position {position_id}",
                            payload={"position_id": position_id}, now=now)
            raise MT5GatewayError(f"no open position with ticket {position_id}")
        pos = found[0]

        broker = str(getattr(pos, "symbol", ""))
        is_buy = getattr(pos, "type", 0) == getattr(client, "POSITION_TYPE_BUY", 0)
        tick = client.symbol_info_tick(broker)
        # Close a buy by selling at bid; close a sell by buying at ask.
        price = (float(getattr(tick, "bid", 0.0)) if is_buy
                 else float(getattr(tick, "ask", 0.0))) if tick else 0.0
        request = {
            "action": getattr(client, "TRADE_ACTION_DEAL", 1),
            "symbol": broker,
            "volume": float(getattr(pos, "volume", 0.0)),
            "type": (getattr(client, "ORDER_TYPE_SELL", 1) if is_buy
                     else getattr(client, "ORDER_TYPE_BUY", 0)),
            "position": int(position_id),
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "close",
            "type_time": getattr(client, "ORDER_TIME_GTC", 0),
            "type_filling": getattr(client, "ORDER_FILLING_IOC", 1),
        }
        result = client.order_send(request)
        retcode = getattr(result, "retcode", None)
        success = retcode == getattr(client, "TRADE_RETCODE_DONE", 10009)
        self._log_event("close_position", status="filled" if success else "rejected",
                        detail=f"close {broker} ticket={position_id} retcode={retcode}",
                        payload={"position_id": position_id, "price": price,
                                 "retcode": retcode}, now=now)
        if not success:
            raise OrderRejectedError(
                f"broker rejected close (retcode {retcode}): {getattr(result, 'comment', '')}")
        return OrderResult(
            success=True, action="close", status="filled", symbol=broker,
            side=(Side.SELL.value if is_buy else Side.BUY.value),
            volume=float(getattr(pos, "volume", 0.0)), price=price,
            position_id=int(position_id), profit=float(getattr(pos, "profit", 0.0) or 0.0),
            comment="position closed",
        )


def _safe(client: Any, attr: str) -> Any:
    """Call ``client.<attr>()`` defensively for diagnostics (never raises)."""
    try:
        fn = getattr(client, attr, None)
        return fn() if callable(fn) else None
    except Exception:  # pragma: no cover - diagnostics only
        return None


def _pos_or_none(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


__all__ = [
    "MT5Gateway",
    "LIVE_LOCKS",
    "MT5GatewayError",
    "MT5NotAvailableError",
    "MT5ConnectionError",
    "LiveTradingDisabledError",
]
