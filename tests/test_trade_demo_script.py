"""Smoke tests for scripts/trade_demo.py (the orchestration loop)."""
import importlib.util
import json
import sys
from pathlib import Path

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
