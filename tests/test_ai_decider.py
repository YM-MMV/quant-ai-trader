"""Tests for the AI decider (M29) — offline, deterministic mock brain."""
from __future__ import annotations

from apps.agent.ai_decider import AIDecider
from apps.agent.llm_runner import LLMResponse, MockLLMClient, ToolUseBlock
from services.data_service.sample_data import generate_candles
from services.strategy_service.base import SignalSide

WIN = generate_candles("XAUUSD", "H1", n=120, seed=7)


def _client(payload: dict) -> MockLLMClient:
    return MockLLMClient(
        responder=lambda **k: LLMResponse(
            content=[ToolUseBlock("d", "submit_decision", payload)], stop_reason="tool_use",
        )
    )


def _decider(payload: dict, **kw) -> AIDecider:
    return AIDecider(symbol="XAUUSD", timeframe="H1", client=_client(payload), **kw)


def test_hold_is_non_actionable():
    sig = _decider({"action": "hold", "rationale": "flat"}, require_validation=False).generate_signal(WIN)
    assert sig.side is SignalSide.NONE and not sig.is_actionable


def test_buy_signal_has_oriented_levels():
    dec = _decider({"action": "open", "side": "buy", "rationale": "x", "confidence": 0.7},
                   require_validation=False)
    sig = dec.generate_signal(WIN)
    close = float(WIN["close"].iloc[-1])
    assert sig.is_actionable and sig.side is SignalSide.BUY
    assert sig.suggested_stop_loss < close < sig.suggested_take_profit
    assert dec.last_decision.action == "open"


def test_sell_signal_has_oriented_levels():
    sig = _decider({"action": "open", "side": "sell", "rationale": "x"},
                   require_validation=False).generate_signal(WIN)
    close = float(WIN["close"].iloc[-1])
    assert sig.suggested_take_profit < close < sig.suggested_stop_loss


def test_validation_gate_blocks_unvalidated_strategy():
    # An unknown strategy makes the deterministic validate gate fail → abstain,
    # so an actionable signal always corresponds to a validated strategy.
    sig = _decider({"action": "open", "side": "buy", "strategy": "no_such_strategy", "rationale": "x"},
                   require_validation=True).generate_signal(WIN)
    assert sig.side is SignalSide.NONE


def test_validate_bars_is_forwarded_to_the_gate(monkeypatch):
    # The depth control must reach the deterministic gate, else it re-validates
    # at the shallow default (600) and the 100-trade gate blocks every trade.
    captured: dict = {}

    def fake_validate(strategy, **kwargs):
        captured["strategy"] = strategy
        captured.update(kwargs)
        return {"approved": True}

    monkeypatch.setattr("apps.agent.ai_decider.validate_strategy", fake_validate)
    dec = _decider(
        {"action": "open", "side": "buy", "strategy": "rsi_pattern", "rationale": "x"},
        require_validation=True, validate_bars=5000,
    )
    sig = dec.generate_signal(WIN)
    assert captured["strategy"] == "rsi_pattern"
    assert captured["n"] == 5000          # depth forwarded, not the 600 default
    assert captured["source"] == "sample"
    assert sig.is_actionable              # approved gate ⇒ the signal goes through


def test_validate_source_overrides_source_for_the_gate(monkeypatch):
    # Live decisions read `source` (e.g. mt5) but the gate validates on
    # `validate_source` (e.g. deep local history) — they must not be conflated.
    captured: dict = {}

    def fake_validate(strategy, **kwargs):
        captured.update(kwargs)
        return {"approved": True}

    monkeypatch.setattr("apps.agent.ai_decider.validate_strategy", fake_validate)
    dec = _decider(
        {"action": "open", "side": "buy", "strategy": "rsi_pattern", "rationale": "x"},
        require_validation=True, source="mt5", validate_source="local",
    )
    dec.generate_signal(WIN)
    assert captured["source"] == "local"   # not "mt5"


def test_decider_fails_safe_on_model_error():
    def boom(**k):
        raise RuntimeError("model down")

    dec = AIDecider(symbol="XAUUSD", timeframe="H1",
                    client=MockLLMClient(responder=boom), require_validation=False)
    sig = dec.generate_signal(WIN)
    assert sig.side is SignalSide.NONE
    assert "error" in sig.reason.lower()
