"""Smoke tests for scripts/trade_demo.py (the orchestration loop)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trade_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("trade_demo", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass can resolve its own module namespace.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_unknown_adapter_exits_two():
    assert _load().main(["--adapter", "nope"]) == 2


def test_paper_open_path_with_assume_approved(tmp_path, capsys):
    rc = _load().main([
        "--adapter", "macd_oscillator", "--iterations", "150",
        "--assume-approved", "--log-dir", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PAPER OPEN" in out
    # Trades were persisted to the paper trade log.
    trades = (tmp_path / "trades.jsonl").read_text().strip().splitlines()
    assert trades, "expected at least one paper trade record"
    first = json.loads(trades[0])
    assert first["symbol"] == "EURUSD"


def test_unapproved_strategy_is_gated_out(tmp_path, capsys):
    rc = _load().main([
        "--adapter", "macd_oscillator", "--iterations", "120",
        "--skip-validation", "--log-dir", str(tmp_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # No approvals: every actionable signal is rejected by the gate.
    assert "PAPER OPEN" not in out
    assert "strategy is not approved" in out


def test_live_mode_refused_without_locks():
    # The script must never flip the live switches; with defaults it bails out.
    rc = _load().main([
        "--adapter", "macd_oscillator", "--mode", "live", "--skip-validation",
    ])
    assert rc == 3


def test_live_levels_reanchor_sl_tp_to_live_quote():
    """The invalid-stops fix: a market order's SL/TP are anchored to the live
    quote (with distances preserved), not the stale historical bar levels."""
    mod = _load()

    class _Quote:
        bid, ask = 4130.0, 4130.5

    class _FakeGateway:
        def get_quote(self, symbol):
            return _Quote()

        def min_stop_distance(self, symbol):
            return 0.0

    trader = mod.DemoTrader(
        adapter=None, risk=None, paper=None, symbol="XAUUSD", timeframe="H1",
        volume=0.01, balance=10_000.0, risk_pct=1.0, allowlist=("XAUUSD",),
        spread_points=10, strategy_approved=True, mode=mod.TradingMode.LIVE,
        gateway=_FakeGateway(),
    )
    # Historical SELL signal at close 4286: SL 4319 (above), TP 4230 (below).
    # At the live price 4130 the *old* TP 4230 would be ABOVE -> invalid for a
    # sell. Re-anchored, SL/TP straddle the live bid with distances preserved.
    entry, sl, tp = trader._live_levels(mod.Side.SELL, 4286.0, 4319.0, 4230.0)
    assert entry == 4130.0          # sell fills at bid
    assert sl > entry and tp < entry
    assert sl == pytest.approx(4163.0, abs=0.01)   # 4130 + |4319-4286|
    assert tp == pytest.approx(4074.0, abs=0.01)   # 4130 - |4286-4230|


def test_live_levels_enforce_min_stop_distance():
    mod = _load()

    class _FakeGateway:
        def get_quote(self, symbol):
            class Q: bid, ask = 4130.0, 4130.5
            return Q()

        def min_stop_distance(self, symbol):
            return 5.0   # broker requires >= $5 away

    trader = mod.DemoTrader(
        adapter=None, risk=None, paper=None, symbol="XAUUSD", timeframe="H1",
        volume=0.01, balance=10_000.0, risk_pct=1.0, allowlist=("XAUUSD",),
        spread_points=10, strategy_approved=True, mode=mod.TradingMode.LIVE,
        gateway=_FakeGateway(),
    )
    # Tiny signal distances ($1) get widened to the broker minimum ($5).
    entry, sl, tp = trader._live_levels(mod.Side.BUY, 4130.0, 4129.0, 4131.0)
    assert entry == 4130.5          # buy fills at ask
    assert sl == pytest.approx(4125.5, abs=0.01)   # 4130.5 - 5
    assert tp == pytest.approx(4135.5, abs=0.01)   # 4130.5 + 5
