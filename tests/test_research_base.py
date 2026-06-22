"""Tests for the research-adapter base framework."""
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from services.models import SignalAction
from services.strategy_service.research_adapters.base import (
    MT5Applicability,
    NotExecutableError,
    OutputType,
    ResearchAdapter,
    ResearchAdapterMetadata,
    ResearchOutput,
    ensure_not_executed,
)

REPO = "https://github.com/je-suis-tm/quant-trading"


def _output(applicability, output_type, **kw):
    return ResearchOutput(
        name="x", output_type=output_type, applicability=applicability,
        summary="s", reason="r", source_strategy="X", adapter_version="1", **kw,
    )


# --------------------------------------------------------------------------- #
# Executability barrier
# --------------------------------------------------------------------------- #
def test_research_only_signal_is_not_executable():
    out = _output(MT5Applicability.RESEARCH_ONLY, OutputType.SIGNAL)
    assert out.is_executable() is False


def test_adaptable_signal_is_not_executable():
    out = _output(MT5Applicability.ADAPTABLE, OutputType.SIGNAL)
    assert out.is_executable() is False


def test_direct_non_signal_is_not_executable():
    out = _output(MT5Applicability.DIRECT, OutputType.REPORT)
    assert out.is_executable() is False


def test_only_direct_signal_is_executable():
    out = _output(MT5Applicability.DIRECT, OutputType.SIGNAL, data={"side": "buy"})
    assert out.is_executable() is True


def test_to_strategy_signal_blocked_for_research():
    out = _output(MT5Applicability.RESEARCH_ONLY, OutputType.SIGNAL)
    with pytest.raises(NotExecutableError):
        out.to_strategy_signal(
            symbol="EURUSD", timeframe="H1", timestamp=datetime(2024, 1, 1),
            strategy_id="x",
        )


def test_ensure_not_executed_blocks_research():
    out = _output(MT5Applicability.RESEARCH_ONLY, OutputType.REPORT)
    with pytest.raises(NotExecutableError):
        ensure_not_executed(out)


def test_to_strategy_signal_allows_direct_signal():
    out = _output(MT5Applicability.DIRECT, OutputType.SIGNAL,
                  data={"side": "sell", "confidence": 0.5})
    sig = out.to_strategy_signal(
        symbol="EURUSD", timeframe="H1", timestamp=datetime(2024, 1, 1),
        strategy_id="x",
    )
    assert sig.action is SignalAction.SELL and sig.confidence == 0.5


# --------------------------------------------------------------------------- #
# Model invariants
# --------------------------------------------------------------------------- #
def test_output_requires_summary_and_reason():
    with pytest.raises(ValidationError):
        ResearchOutput(
            name="x", output_type=OutputType.REPORT,
            applicability=MT5Applicability.RESEARCH_ONLY,
            summary="", reason="", source_strategy="X", adapter_version="1",
        )


def test_metadata_tradable_flag_only_direct():
    meta = ResearchAdapterMetadata(
        name="m", version="1", source_strategy="M", output_type=OutputType.SIGNAL,
        applicability=MT5Applicability.DIRECT, reason="r",
    )
    assert meta.tradable_on_mt5 is True
    meta2 = ResearchAdapterMetadata(
        name="m", version="1", source_strategy="M", output_type=OutputType.SIGNAL,
        applicability=MT5Applicability.RESEARCH_ONLY, reason="r",
    )
    assert meta2.tradable_on_mt5 is False


# --------------------------------------------------------------------------- #
# Fail-safe run wrapper
# --------------------------------------------------------------------------- #
class _BoomAdapter(ResearchAdapter):
    def get_metadata(self) -> ResearchAdapterMetadata:
        return ResearchAdapterMetadata(
            name="boom", version="1", source_strategy="Boom",
            output_type=OutputType.REPORT,
            applicability=MT5Applicability.RESEARCH_ONLY,
            required_datasets=["x"], reason="r",
        )

    def _analyze(self, **inputs: Any) -> ResearchOutput:
        raise ValueError("kaboom")


def test_run_is_fail_safe_on_exception():
    out = _BoomAdapter().run()
    assert isinstance(out, ResearchOutput)
    assert out.output_type is OutputType.REPORT
    assert out.data_available is False
    assert "kaboom" in out.summary
    assert out.is_executable() is False


def test_cannot_instantiate_abstract_base():
    with pytest.raises(TypeError):
        ResearchAdapter()  # type: ignore[abstract]
