"""Smoke tests for scripts/validate_strategy.py (callable entrypoint)."""
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_strategy.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_strategy", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_exits_zero(capsys):
    rc = _load().main(["--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "macd_oscillator" in out


def test_unknown_adapter_exits_two():
    assert _load().main(["--adapter", "does_not_exist"]) == 2


def test_validation_run_returns_int_verdict():
    # On random-walk sample candles a strategy won't pass — expect a clean 1.
    rc = _load().main(["--adapter", "macd_oscillator", "--n", "300", "--seed", "1"])
    assert rc in (0, 1)


@pytest.mark.parametrize("split", ["0.1", "0.99"])
def test_bad_split_rejected(split):
    assert _load().main(["--adapter", "macd_oscillator", "--split", split]) == 2
