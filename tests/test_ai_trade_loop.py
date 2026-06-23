"""Tests for the always-on AI trade loop (M29) — offline, mock brain + gateway."""
from __future__ import annotations

from datetime import datetime

from apps.agent.ai_decider import AIDecider
from apps.agent.llm_runner import LLMResponse, MockLLMClient, ToolUseBlock
from scripts.ai_trade_loop import LiveTrader, main
from services.config_loader import load_risk_config
from services.data_service.sample_data import generate_candles
from services.execution_service.mock_mt5_gateway import MockMT5Gateway
from services.risk_service.risk_manager import RiskManager

WIN = generate_candles("XAUUSD", "H1", n=120, seed=7)
NOW = datetime(2024, 1, 4, 8, 0)


def _decider(payload: dict) -> AIDecider:
    client = MockLLMClient(
        responder=lambda **k: LLMResponse(
            content=[ToolUseBlock("d", "submit_decision", payload)], stop_reason="tool_use",
        )
    )
    return AIDecider(symbol="XAUUSD", timeframe="H1", client=client, require_validation=False)


def _live(decider: AIDecider, gw: MockMT5Gateway) -> LiveTrader:
    return LiveTrader(
        decider=decider, gateway=gw, risk=RiskManager(load_risk_config()),
        symbol="XAUUSD", timeframe="H1", risk_pct=1.0, allowlist=("XAUUSD",), spread_points=10.0,
    )


def test_paper_loop_runs_offline():
    rc = main(["--once", "--source", "sample", "--mock-ai", "--symbol", "XAUUSD", "--mode", "paper"])
    assert rc == 0


def test_live_router_opens_position():
    gw = MockMT5Gateway(balance=100_000.0)
    gw.connect()
    lt = _live(_decider({"action": "open", "side": "buy", "rationale": "x"}), gw)
    lt.tick(WIN, NOW)
    assert len(gw.positions("XAUUSD")) == 1
    assert lt.opens == 1


def test_live_router_does_not_stack_same_side():
    gw = MockMT5Gateway(balance=100_000.0)
    gw.connect()
    lt = _live(_decider({"action": "open", "side": "buy", "rationale": "x"}), gw)
    lt.tick(WIN, NOW)
    lt.tick(WIN, NOW)
    assert len(gw.positions("XAUUSD")) == 1  # the second buy must not stack


def test_live_router_closes_position():
    gw = MockMT5Gateway(balance=100_000.0)
    gw.connect()
    _live(_decider({"action": "open", "side": "buy", "rationale": "x"}), gw).tick(WIN, NOW)
    closer = _live(_decider({"action": "close", "rationale": "exit"}), gw)
    closer.tick(WIN, NOW)
    assert len(gw.positions("XAUUSD")) == 0
    assert closer.closes == 1


def test_live_router_flips_on_opposite_signal():
    gw = MockMT5Gateway(balance=100_000.0)
    gw.connect()
    _live(_decider({"action": "open", "side": "buy", "rationale": "x"}), gw).tick(WIN, NOW)
    flip = _live(_decider({"action": "open", "side": "sell", "rationale": "x"}), gw)
    flip.tick(WIN, NOW)
    positions = gw.positions("XAUUSD")
    assert len(positions) == 1 and positions[0].side == "sell"


def test_demo_mode_refused_without_locks(capsys):
    # Without the env locks set, demo/live is refused before any gateway call.
    rc = main(["--once", "--source", "sample", "--mock-ai", "--symbol", "XAUUSD", "--mode", "demo"])
    assert rc == 3
    assert "refused" in capsys.readouterr().out.lower()
