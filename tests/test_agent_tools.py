"""Tests for the AI agent tool interface (M19) — paper-only, no live execution.

Every tool is exercised as a plain Python callable (no LLM, no network). The
safety-critical properties are asserted explicitly:

* there is **no** live-execution tool, and none of the forbidden tool names exist;
* ``create_paper_trade`` creates a trade only after the risk gate approves, and
  there is no way to hand it a pre-approved decision (no self-bypass);
* the agent config is paper-only and cannot be escalated.
"""
import inspect

import pytest

from apps.agent import tools as t
from apps.agent.agent_config import (
    DEFAULT_AGENT_CONFIG,
    AgentConfig,
    AgentPermissionError,
    AgentPermissions,
    FORBIDDEN_TOOLS,
    assert_no_live_tools,
    assert_paper_only,
)
from services.models import TradingMode

EXPECTED_TOOLS = {
    "get_candles", "get_market_features", "get_kronos_prediction",
    "list_strategy_inventory", "list_strategy_adapters", "run_backtest",
    "score_backtest", "validate_strategy", "propose_order_intent",
    "risk_check_order_intent", "create_paper_trade",
}


def clean_intent(symbol="EURUSD", side="buy"):
    return t.propose_order_intent(symbol, side, 0.01, 1.095, 1.11)


# --------------------------------------------------------------------------- #
# Tool registry & the "no live execution" guarantee (Done-when)
# --------------------------------------------------------------------------- #
def test_all_expected_tools_present_and_callable():
    assert set(t.available_tools()) == EXPECTED_TOOLS
    for name in EXPECTED_TOOLS:
        assert callable(t.get_tool(name))


def test_no_live_execution_tool_exists():
    # No live-execution tool by name, in the registry, or as a module attribute.
    assert "execute_live_trade" not in t.AGENT_TOOLS
    assert not hasattr(t, "execute_live_trade")
    assert set(t.AGENT_TOOLS) & FORBIDDEN_TOOLS == set()
    # The structural guard does not raise for the real registry.
    assert_no_live_tools(set(t.AGENT_TOOLS))


def test_forbidden_tool_set_is_rejected():
    with pytest.raises(AgentPermissionError):
        assert_no_live_tools({"execute_live_trade"})


def test_get_tool_unknown_raises():
    with pytest.raises(KeyError):
        t.get_tool("nope")


# --------------------------------------------------------------------------- #
# Agent config: paper-only, non-escalatable
# --------------------------------------------------------------------------- #
def test_default_config_is_paper_only():
    assert DEFAULT_AGENT_CONFIG.trading_mode is TradingMode.PAPER
    perms = DEFAULT_AGENT_CONFIG.permissions
    assert perms.can_research and perms.can_propose and perms.can_backtest
    assert perms.can_create_paper_trades
    assert not perms.can_execute_live
    assert not perms.can_modify_risk_config
    assert not perms.can_approve_risk_bypass
    assert_paper_only(DEFAULT_AGENT_CONFIG)  # does not raise


def test_config_is_frozen():
    with pytest.raises(Exception):
        DEFAULT_AGENT_CONFIG.permissions.can_execute_live = True  # type: ignore[misc]


def test_assert_paper_only_rejects_escalated_config():
    bad = AgentConfig(permissions=AgentPermissions(can_execute_live=True))
    with pytest.raises(AgentPermissionError):
        assert_paper_only(bad)
    live = AgentConfig(trading_mode=TradingMode.LIVE)
    with pytest.raises(AgentPermissionError):
        assert_paper_only(live)


# --------------------------------------------------------------------------- #
# Research tools
# --------------------------------------------------------------------------- #
def test_get_candles():
    out = t.get_candles("EURUSD", "H1", 10)
    assert out["count"] == 10
    assert len(out["candles"]) == 10
    first = out["candles"][0]
    assert {"timestamp", "open", "high", "low", "close"} <= set(first)
    assert first["high"] >= first["low"]


def test_get_candles_is_deterministic():
    a = t.get_candles("EURUSD", "H1", 5)
    b = t.get_candles("EURUSD", "H1", 5)
    assert a == b


def test_get_market_features():
    out = t.get_market_features("EURUSD", "H1", 150)
    assert out["symbol"] == "EURUSD"
    assert out["features"]  # non-empty after warm-up
    assert all(v == v for v in out["features"].values())  # no NaN


def test_get_kronos_prediction_defaults_to_mock():
    out = t.get_kronos_prediction("EURUSD", "H1", 120)
    assert out["is_mock"] is True
    assert out["symbol"] == "EURUSD"


def test_list_strategy_inventory():
    out = t.list_strategy_inventory()
    assert out["count"] > 0
    item = out["strategies"][0]
    assert {"name", "category", "mt5_applicability", "porting_status"} <= set(item)


def test_list_strategy_adapters():
    out = t.list_strategy_adapters()
    assert out["count"] > 0
    names = [a["name"] for a in out["adapters"]]
    assert "macd_oscillator" in names


# --------------------------------------------------------------------------- #
# Backtest / validation tools
# --------------------------------------------------------------------------- #
def test_run_backtest_with_adapter_name():
    out = t.run_backtest("macd_oscillator", "EURUSD", "H1", 300)
    assert out["n_bars"] == 300
    assert out["num_trades"] >= 0
    assert "total_trades" in out["metrics"]


def test_run_backtest_with_callable_strategy():
    from services.backtest_service.simple_backtester import BacktestSignal, Direction

    def always_flat(window):
        return BacktestSignal(direction=Direction.NONE)

    out = t.run_backtest(always_flat, "EURUSD", "H1", 100)
    assert out["num_trades"] == 0
    assert out["strategy"] == "callable"


def test_run_backtest_unknown_adapter_raises():
    with pytest.raises(KeyError):
        t.run_backtest("does_not_exist", "EURUSD", "H1", 100)


def test_score_backtest_from_run_result():
    bt = t.run_backtest("macd_oscillator", "EURUSD", "H1", 300)
    score = t.score_backtest(bt)
    assert 0.0 <= score["score"] <= 1.0
    assert score["grade"] in {"A", "B", "C", "D", "F"}
    assert set(score["components"]) == {"profit_factor", "win_rate", "drawdown", "expectancy"}


def test_score_backtest_no_trades_is_zero():
    score = t.score_backtest({"metrics": {"total_trades": 0}})
    assert score["score"] == 0.0
    assert score["has_trades"] is False


def test_validate_strategy_returns_report():
    rep = t.validate_strategy("macd_oscillator", "EURUSD", "H1", 400)
    assert "approved" in rep
    assert isinstance(rep["results"], list)
    assert rep["strategy_id"] == "macd_oscillator"


# --------------------------------------------------------------------------- #
# Proposal / risk / paper execution
# --------------------------------------------------------------------------- #
def test_propose_order_intent_builds_valid_intent():
    intent = clean_intent()
    assert intent["symbol"] == "EURUSD"
    assert intent["side"] == "buy"
    assert intent["stop_loss"] == 1.095
    assert intent["take_profit"] == 1.11


def test_propose_order_intent_requires_stop_and_target():
    # The OrderIntent model refuses to exist without SL/TP.
    with pytest.raises(Exception):
        t.propose_order_intent("EURUSD", "buy", 0.01, None, 1.11)  # type: ignore[arg-type]


def test_risk_check_approves_clean_intent():
    decision = t.risk_check_order_intent(
        clean_intent(), reference_price=1.10, account_balance=10_000
    )
    assert decision["approved"] is True
    assert decision["reasons"] == []
    assert decision["mode"] == "paper"


def test_risk_check_denies_non_allowlisted_symbol():
    decision = t.risk_check_order_intent(
        clean_intent(), reference_price=1.10, account_balance=10_000, allowlist=()
    )
    assert decision["approved"] is False
    assert any("allowlisted" in r for r in decision["reasons"])


# --------------------------------------------------------------------------- #
# The paper-trade gate (the core safety property)
# --------------------------------------------------------------------------- #
def test_create_paper_trade_only_after_approval():
    res = t.create_paper_trade(clean_intent(), reference_price=1.10, account_balance=10_000)
    assert res["created"] is True
    assert res["approved"] is True
    assert res["trade"]["status"] == "open"
    assert res["risk_decision"]["approved"] is True


def test_create_paper_trade_refuses_when_risk_denies():
    res = t.create_paper_trade(
        clean_intent(), reference_price=1.10, account_balance=10_000, allowlist=()
    )
    assert res["created"] is False
    assert res["approved"] is False
    assert res["reasons"]


def test_create_paper_trade_cannot_be_handed_an_approval():
    # No 'decision'/'approved' parameter exists — the agent cannot self-approve;
    # the risk check is always re-run inside the tool.
    params = set(inspect.signature(t.create_paper_trade).parameters)
    assert "decision" not in params
    assert "approved" not in params


def test_create_paper_trade_writes_to_execution_service(tmp_path):
    from services.execution_service.audit_log import AuditLog
    from services.execution_service.paper_execution import PaperExecutionService
    from services.execution_service.trade_log import TradeLogStore
    from services.config_loader import load_risk_config

    service = PaperExecutionService(
        trade_log=TradeLogStore(tmp_path / "trades.jsonl"),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        config=load_risk_config(),
    )
    t.create_paper_trade(
        clean_intent(), reference_price=1.10, account_balance=10_000,
        execution_service=service,
    )
    # The approved trade was logged by the (injected) execution service.
    records = service.trade_log.records()
    assert len(records) >= 1
    assert records[0].status.value == "open"


def test_create_paper_trade_blocked_when_permission_off():
    no_paper = AgentConfig(permissions=AgentPermissions(can_create_paper_trades=False))
    with pytest.raises(AgentPermissionError):
        t.create_paper_trade(
            clean_intent(), reference_price=1.10, account_balance=10_000, config=no_paper
        )
