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


def test_min_trades_is_forwarded_to_the_gate(monkeypatch):
    captured: dict = {}

    def fake_validate(strategy, **kwargs):
        captured.update(kwargs)
        return {"approved": True}

    monkeypatch.setattr("apps.agent.ai_decider.validate_strategy", fake_validate)
    dec = _decider(
        {"action": "open", "side": "buy", "strategy": "rsi_pattern", "rationale": "x"},
        require_validation=True, min_trades=15,
    )
    dec.generate_signal(WIN)
    assert captured["min_trades"] == 15


def test_force_position_trades_best_candidate_when_ai_holds(monkeypatch):
    # AI holds, but force_position takes the best-scoring live candidate.
    from services.strategy_service.base import AdapterSignal

    class _FakeAdapter:
        def supports_timeframe(self, tf):
            return True

        def generate_signal(self, window):
            return AdapterSignal(
                side=SignalSide.BUY, reason="live buy", source_strategy="fake",
                adapter_version="1", symbol="XAUUSD", timeframe="H1",
            )

    class _FakeRegistry:
        def names(self):
            return ["fake"]

        def get(self, name):
            return _FakeAdapter()

    monkeypatch.setattr("apps.agent.tools._adapter_registry", lambda: _FakeRegistry())
    monkeypatch.setattr("apps.agent.tools.run_backtest", lambda *a, **k: {"metrics": {}})
    monkeypatch.setattr("apps.agent.tools.score_backtest", lambda bt: {"score": 0.7})

    dec = _decider({"action": "hold", "rationale": "flat"},
                   require_validation=False, force_position=True)
    sig = dec.generate_signal(WIN)
    assert sig.is_actionable and sig.side is SignalSide.BUY
    assert "FORCED" in sig.reason


def test_force_position_off_keeps_hold():
    # Default (no force): a hold stays a non-actionable NONE.
    dec = _decider({"action": "hold", "rationale": "flat"}, require_validation=False)
    assert dec.generate_signal(WIN).side is SignalSide.NONE


def test_decider_fails_safe_on_model_error():
    def boom(**k):
        raise RuntimeError("model down")

    dec = AIDecider(symbol="XAUUSD", timeframe="H1",
                    client=MockLLMClient(responder=boom), require_validation=False)
    sig = dec.generate_signal(WIN)
    assert sig.side is SignalSide.NONE
    assert "error" in sig.reason.lower()
