"""Tests for the append-only audit log (M13).

Verifies that every required audit event type can be recorded and replayed, that
the execution pipeline emits the expected trail for approved and rejected
trades, and that the log is append-only and round-trips through disk.
"""
from datetime import datetime

import pytest

from services.config_loader import RiskConfig
from services.execution_service.audit_log import (
    AuditEvent,
    AuditEventType,
    AuditLog,
)
from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import TradeLogStore
from services.models import OrderIntent, OrderType, Side, TradingMode
from services.risk_service.risk_manager import RiskContext, RiskManager

NOW = datetime(2024, 1, 2, 12, 0, 0)


def make_config(**overrides) -> RiskConfig:
    base = dict(
        require_stop_loss=True, require_take_profit=True,
        max_daily_loss=100.0, max_open_trades=3, max_spread_points=30,
        max_risk_per_trade_pct=1.0, max_total_exposure_pct=5.0,
        kill_switch_enabled=True, max_trades_per_day=10, max_volatility=None,
    )
    base.update(overrides)
    return RiskConfig(**base)


def make_intent(**overrides) -> OrderIntent:
    base = dict(
        symbol="EURUSD", side=Side.BUY, order_type=OrderType.MARKET,
        volume=0.1, stop_loss=1.0950, take_profit=1.1100, strategy_id="macd-1",
    )
    base.update(overrides)
    return OrderIntent(**base)


def make_context(**overrides) -> RiskContext:
    base = dict(
        mode=TradingMode.PAPER, allowlist=("EURUSD",), account_balance=10_000.0,
        reference_price=1.1000, spread_points=10, strategy_approved=True,
        strategy_applicability="direct",
    )
    base.update(overrides)
    return RiskContext(**base)


def decide(intent=None, context=None):
    return RiskManager(make_config()).evaluate(
        intent or make_intent(), context or make_context(), now=NOW)


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


# --------------------------------------------------------------------------- #
# Basic recording / replay
# --------------------------------------------------------------------------- #
def test_record_and_replay(log):
    log.system_mode(TradingMode.PAPER, now=NOW)
    events = log.events()
    assert len(events) == 1
    assert events[0].event_type is AuditEventType.SYSTEM_MODE
    assert events[0].mode is TradingMode.PAPER
    assert events[0].event_id == "AUD-000001"


def test_event_ids_are_monotonic(log):
    log.system_mode(TradingMode.PAPER, now=NOW)
    log.config_snapshot(make_config(), mode=TradingMode.PAPER, now=NOW)
    ids = [e.event_id for e in log.events()]
    assert ids == ["AUD-000001", "AUD-000002"]


def test_empty_log_reads_empty(tmp_path):
    assert AuditLog(tmp_path / "nope.jsonl").events() == []


# --------------------------------------------------------------------------- #
# Every required audit event type  (Done-when: all event kinds recorded)
# --------------------------------------------------------------------------- #
def test_all_required_event_types_can_be_recorded(log):
    intent = make_intent()
    decision = decide(intent)
    log.signal_proposed(intent, mode=TradingMode.PAPER, now=NOW)
    log.strategy_output({"action": "buy", "confidence": 0.7}, now=NOW)
    log.risk_decision(decision, now=NOW)
    log.execution_decision({"trade_id": "PT-1"}, mode=TradingMode.PAPER, now=NOW)
    log.trade_rejected({"trade_id": "PT-2"}, reasons=["spread"], now=NOW)
    log.config_snapshot(make_config(), mode=TradingMode.PAPER, now=NOW)
    log.system_mode(TradingMode.PAPER, now=NOW)

    recorded = {e.event_type for e in log.events()}
    assert recorded == set(AuditEventType)


def test_risk_decision_payload_is_serialized(log):
    log.risk_decision(decide(), now=NOW)
    event = log.events_of(AuditEventType.RISK_DECISION)[0]
    assert event.payload["decision"]["approved"] is True
    assert "checks" in event.payload["decision"]


def test_config_snapshot_is_captured(log):
    log.config_snapshot(make_config(max_open_trades=7), mode=TradingMode.PAPER, now=NOW)
    event = log.events_of(AuditEventType.CONFIG_SNAPSHOT)[0]
    assert event.payload["config"]["max_open_trades"] == 7


# --------------------------------------------------------------------------- #
# The execution pipeline writes a complete trail
# --------------------------------------------------------------------------- #
def test_execute_writes_full_audit_trail_for_approved(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    svc = PaperExecutionService(
        trade_log=TradeLogStore(tmp_path / "trades.jsonl"),
        audit_log=audit, config=make_config())
    svc.execute(decide(), make_context(), timeframe="H1", now=NOW)

    types = [e.event_type for e in audit.events()]
    # First call logs mode + config, then proposal, risk decision, execution.
    assert AuditEventType.SYSTEM_MODE in types
    assert AuditEventType.CONFIG_SNAPSHOT in types
    assert AuditEventType.SIGNAL_PROPOSED in types
    assert AuditEventType.RISK_DECISION in types
    assert AuditEventType.EXECUTION_DECISION in types
    assert AuditEventType.TRADE_REJECTED not in types


def test_execute_audits_rejected_trade(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    svc = PaperExecutionService(
        trade_log=TradeLogStore(tmp_path / "trades.jsonl"), audit_log=audit)
    ctx = make_context(spread_points=999)
    svc.execute(decide(context=ctx), ctx, timeframe="H1", now=NOW)

    rejected = audit.events_of(AuditEventType.TRADE_REJECTED)
    assert len(rejected) == 1
    assert rejected[0].payload["reasons"]
    assert AuditEventType.EXECUTION_DECISION not in [e.event_type for e in audit.events()]


def test_config_and_mode_logged_once(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    svc = PaperExecutionService(
        trade_log=TradeLogStore(tmp_path / "trades.jsonl"),
        audit_log=audit, config=make_config())
    svc.execute(decide(), make_context(), timeframe="H1", now=NOW)
    svc.execute(decide(), make_context(), timeframe="H1", now=NOW)
    assert len(audit.events_of(AuditEventType.SYSTEM_MODE)) == 1
    assert len(audit.events_of(AuditEventType.CONFIG_SNAPSHOT)) == 1


# --------------------------------------------------------------------------- #
# Append-only / persistence
# --------------------------------------------------------------------------- #
def test_log_is_append_only_across_instances(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).system_mode(TradingMode.PAPER, now=NOW)
    AuditLog(path).system_mode(TradingMode.MT5_DEMO, now=NOW)
    events = AuditLog(path).events()
    assert len(events) == 2
    assert [e.mode for e in events] == [TradingMode.PAPER, TradingMode.MT5_DEMO]


def test_event_dict_round_trip(log):
    log.risk_decision(decide(), message="hello", now=NOW)
    original = log.events()[0]
    restored = AuditEvent.from_dict(original.to_dict())
    assert restored == original
