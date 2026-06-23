"""Tests for the LLM runner (M29) — offline, no network, mock client only."""
from __future__ import annotations

from apps.agent.llm_runner import (
    AgentDecision,
    LLMResponse,
    MockLLMClient,
    TextBlock,
    ToolUseBlock,
    run_decision,
)


def _submit(**kw) -> LLMResponse:
    return LLMResponse(content=[ToolUseBlock("s", "submit_decision", kw)], stop_reason="tool_use")


def _call(name: str, **inp) -> LLMResponse:
    return LLMResponse(content=[ToolUseBlock("c", name, inp)], stop_reason="tool_use")


# -- AgentDecision parsing --------------------------------------------------- #
def test_open_without_side_fails_safe_to_hold():
    assert AgentDecision.from_tool_input({"action": "open", "rationale": "x"}).action == "hold"


def test_valid_open_is_actionable():
    d = AgentDecision.from_tool_input({"action": "open", "side": "buy", "rationale": "x"})
    assert d.is_open and d.side == "buy"


def test_action_and_side_are_normalised():
    assert AgentDecision.from_tool_input({"action": "OPEN", "side": "BUY", "rationale": "x"}).is_open
    assert AgentDecision.from_tool_input({"action": "weird", "rationale": "x"}).action == "hold"


# -- the tool-calling loop --------------------------------------------------- #
def test_run_decision_dispatches_tools_then_submits():
    script = [_call("list_strategy_adapters"), _submit(action="hold", rationale="no edge")]
    res = run_decision("go", client=MockLLMClient(script))
    assert res.decision.action == "hold"
    assert ("list_strategy_adapters", {}) in res.tool_calls
    assert res.iterations == 2


def test_run_decision_records_open():
    res = run_decision("go", client=MockLLMClient([
        _submit(action="open", side="sell", strategy="macd_oscillator",
                stop_loss=1.2, take_profit=0.8, rationale="x"),
    ]))
    assert res.decision.is_open and res.decision.side == "sell"


def test_tool_error_is_fed_back_not_crashed():
    # get_candles(n=-1) raises inside the tool; the loop must keep going.
    script = [_call("get_candles", symbol="XAUUSD", n=-1), _submit(action="hold", rationale="x")]
    res = run_decision("go", client=MockLLMClient(script))
    assert res.decision.action == "hold"


def test_exhausted_script_yields_hold():
    res = run_decision("go", client=MockLLMClient([]))
    assert res.decision.action == "hold"


def test_json_decision_in_text_is_parsed():
    text = 'Final: {"action": "hold", "rationale": "flat"}'
    res = run_decision("go", client=MockLLMClient([
        LLMResponse(content=[TextBlock(text)], stop_reason="end_turn"),
    ]))
    assert res.decision.action == "hold"
