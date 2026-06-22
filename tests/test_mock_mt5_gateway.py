"""Tests for the mock MT5 execution gateway (M21).

Everything runs in memory — no MetaTrader5, no network. The central assertion is
the safety one: an order without an approved, matching RiskDecision can never be
sent, and every action is logged.
"""
import pytest

from services.execution_service.base_gateway import (
    ExecutionGateway,
    GatewayError,
    GatewayNotConnectedError,
    OrderRejectedError,
    approval_problem,
)
from services.execution_service.mock_mt5_gateway import GatewayEvent, MockMT5Gateway
from services.models import OrderIntent, RiskDecision, Side, TradingMode


def make_intent(symbol="EURUSD", side=Side.BUY, volume=0.1):
    return OrderIntent(
        symbol=symbol, side=side, volume=volume, stop_loss=1.095, take_profit=1.11
    )


def approved_for(intent, volume=None):
    return RiskDecision(
        intent=intent, approved=True, mode=TradingMode.PAPER,
        approved_volume=volume or intent.volume,
    )


def denied_for(intent, reasons=("symbol not allowlisted",)):
    return RiskDecision(
        intent=intent, approved=False, mode=TradingMode.PAPER, reasons=list(reasons)
    )


@pytest.fixture
def gw():
    g = MockMT5Gateway()
    g.connect()
    return g


# --------------------------------------------------------------------------- #
# No real MetaTrader5 dependency (a hard rule)
# --------------------------------------------------------------------------- #
def test_no_metatrader5_import():
    import services.execution_service.base_gateway as base
    import services.execution_service.mock_mt5_gateway as mock

    for module in (base, mock):
        src = module.__file__
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        assert "import MetaTrader5" not in text
        assert "MetaTrader5" not in text


def test_mock_implements_interface():
    assert isinstance(MockMT5Gateway(), ExecutionGateway)


# --------------------------------------------------------------------------- #
# Connection & queries
# --------------------------------------------------------------------------- #
def test_connect():
    g = MockMT5Gateway()
    assert g.connect() is True


def test_queries_require_connection():
    g = MockMT5Gateway()  # not connected
    with pytest.raises(GatewayNotConnectedError):
        g.get_quote("EURUSD")
    with pytest.raises(GatewayNotConnectedError):
        g.account_info()
    with pytest.raises(GatewayNotConnectedError):
        g.positions()


def test_account_info_has_no_secrets(gw):
    info = gw.account_info()
    dumped = info.model_dump()
    assert "password" not in dumped
    assert info.balance == 10_000.0
    assert info.currency == "USD"


def test_get_quote(gw):
    q = gw.get_quote("EURUSD")
    assert q.ask > q.bid > 0
    assert q.symbol == "EURUSD"


def test_positions_empty_initially(gw):
    assert gw.positions() == []


# --------------------------------------------------------------------------- #
# The approval gate: unapproved orders cannot be sent (core requirement)
# --------------------------------------------------------------------------- #
def test_send_order_rejects_without_decision(gw):
    with pytest.raises(OrderRejectedError):
        gw.send_order(make_intent(), None)
    assert gw.positions() == []  # nothing created


def test_send_order_rejects_unapproved_decision(gw):
    intent = make_intent()
    with pytest.raises(OrderRejectedError):
        gw.send_order(intent, denied_for(intent))
    assert gw.positions() == []


def test_send_order_rejects_mismatched_decision(gw):
    # A decision approved for a *different* intent must not authorise this order.
    intent = make_intent(symbol="EURUSD")
    other = make_intent(symbol="GBPUSD")
    with pytest.raises(OrderRejectedError):
        gw.send_order(intent, approved_for(other))
    assert gw.positions() == []


def test_rejected_order_is_logged(gw):
    intent = make_intent()
    with pytest.raises(OrderRejectedError):
        gw.send_order(intent, denied_for(intent))
    rejects = [e for e in gw.events if e.action == "send_order" and e.status == "rejected"]
    assert len(rejects) == 1


def test_approval_problem_helper():
    intent = make_intent()
    assert approval_problem(intent, approved_for(intent)) is None
    assert approval_problem(intent, None) is not None
    assert approval_problem(intent, denied_for(intent)) is not None


# --------------------------------------------------------------------------- #
# Approved order placement & lifecycle
# --------------------------------------------------------------------------- #
def test_send_order_approved_opens_position(gw):
    intent = make_intent()
    result = gw.send_order(intent, approved_for(intent))
    assert result.success is True
    assert result.action == "open"
    assert result.status == "filled"
    assert result.position_id is not None
    positions = gw.positions()
    assert len(positions) == 1
    assert positions[0].symbol == "EURUSD"
    assert positions[0].side == "buy"


def test_send_order_honours_approved_volume(gw):
    intent = make_intent(volume=0.1)
    result = gw.send_order(intent, approved_for(intent, volume=0.05))
    assert result.volume == 0.05


def test_close_position_returns_profit(gw):
    intent = make_intent()
    result = gw.send_order(intent, approved_for(intent))
    close = gw.close_position(result.position_id)
    assert close.success is True
    assert close.action == "close"
    assert close.profit is not None
    assert gw.positions() == []


def test_close_unknown_position_raises(gw):
    with pytest.raises(GatewayError):
        gw.close_position(99999)


def test_account_equity_reflects_floating_pnl(gw):
    intent = make_intent(symbol="EURUSD", side=Side.BUY)
    gw.send_order(intent, approved_for(intent))
    # With a fresh open, equity = balance + (bid-ask)*size*contract (spread cost).
    info = gw.account_info()
    assert info.equity < info.balance  # immediate spread cost shows as floating loss


def test_close_updates_balance(gw):
    intent = make_intent(symbol="XAUUSD")
    result = gw.send_order(intent, approved_for(intent))
    close = gw.close_position(result.position_id)
    info = gw.account_info()
    assert info.balance == pytest.approx(10_000.0 + close.profit, abs=1e-6)


# --------------------------------------------------------------------------- #
# order_check (pre-trade validation, places nothing)
# --------------------------------------------------------------------------- #
def test_order_check_ok_for_approved(gw):
    intent = make_intent()
    result = gw.order_check(intent, approved_for(intent))
    assert result.ok is True
    assert result.reasons == []
    assert gw.positions() == []  # check never places


def test_order_check_flags_unapproved(gw):
    intent = make_intent()
    result = gw.order_check(intent, denied_for(intent))
    assert result.ok is False
    assert any("not approved" in r for r in result.reasons)


def test_order_check_flags_insufficient_margin():
    g = MockMT5Gateway(balance=1.0, leverage=1)  # tiny account
    g.connect()
    intent = make_intent(symbol="XAUUSD", volume=50.0)
    result = g.order_check(intent, approved_for(intent))
    assert result.ok is False
    assert any("margin" in r for r in result.reasons)


# --------------------------------------------------------------------------- #
# Logging: the gateway logs everything
# --------------------------------------------------------------------------- #
def test_logs_every_action(gw):
    intent = make_intent()
    gw.get_quote("EURUSD")
    gw.account_info()
    result = gw.send_order(intent, approved_for(intent))
    gw.close_position(result.position_id)
    actions = {e.action for e in gw.events}
    assert {"connect", "get_quote", "account_info", "send_order", "close_position"} <= actions
    assert all(isinstance(e, GatewayEvent) for e in gw.events)


def test_audit_log_mirroring(tmp_path):
    from services.execution_service.audit_log import AuditLog, AuditEventType

    audit = AuditLog(tmp_path / "audit.jsonl")
    g = MockMT5Gateway(audit_log=audit)
    g.connect()
    intent = make_intent()
    result = g.send_order(intent, approved_for(intent))
    g.close_position(result.position_id)
    # The fill and close were mirrored to the shared audit trail.
    exec_events = audit.events_of(AuditEventType.EXECUTION_DECISION)
    assert len(exec_events) == 2
