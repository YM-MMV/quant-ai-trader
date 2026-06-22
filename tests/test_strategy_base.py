"""Tests for the strategy adapter base interfaces (services/strategy_service/base.py).

No trading, no MT5, no AI — deterministic logic on fake candles only.
"""
from datetime import datetime
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from services.data_service.sample_data import generate_candles
from services.models import SignalAction
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)

REPO = "https://github.com/je-suis-tm/quant-trading"


# --------------------------------------------------------------------------- #
# Test adapters
# --------------------------------------------------------------------------- #
class UpDownAdapter(StrategyAdapter):
    """Deterministic: BUY if last close > first close, SELL if lower, else NONE."""

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="updown",
            version="1.2.3",
            source_repo_url=REPO,
            source_strategy="Up/Down Demo",
            category="test",
            supported_symbols=["EURUSD"],
            supported_timeframes=["M15"],
            min_candles=5,
        )

    def _compute_signal(self, candles: Any, features: Any, kronos_prediction: Optional[Any]):
        close = candles["close"]
        last, first = float(close.iloc[-1]), float(close.iloc[0])
        if last > first:
            return self.make_signal(
                SignalSide.BUY, 0.7, "higher close than window start",
                suggested_stop_loss=last * 0.99, suggested_take_profit=last * 1.02,
                risk_notes=["demo only"], symbol="EURUSD", timeframe="M15",
            )
        if last < first:
            return self.make_signal(
                SignalSide.SELL, 0.6, "lower close than window start",
                suggested_stop_loss=last * 1.01, suggested_take_profit=last * 0.98,
            )
        return self.none_signal("flat")


class RaisingAdapter(StrategyAdapter):
    """Deliberately explodes in _compute_signal to test the fail-safe wrapper."""

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(name="raiser", version="0.0.1", min_candles=1)

    def _compute_signal(self, candles, features, kronos_prediction):
        raise ValueError("boom")


class BadReturnAdapter(StrategyAdapter):
    """Returns a non-signal; framework must coerce to NONE."""

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(name="bad", version="0.0.1", min_candles=1)

    def _compute_signal(self, candles, features, kronos_prediction):
        return "not a signal"  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# AdapterSignal invariants
# --------------------------------------------------------------------------- #
def test_none_signal_is_coerced_clean():
    s = AdapterSignal(
        side=SignalSide.NONE, confidence=0.9, suggested_stop_loss=1.0,
        suggested_take_profit=2.0, source_strategy="x", adapter_version="1",
    )
    assert s.confidence == 0.0
    assert s.suggested_stop_loss is None and s.suggested_take_profit is None
    assert not s.is_actionable


def test_actionable_signal_requires_reason():
    with pytest.raises(ValidationError):
        AdapterSignal(side=SignalSide.BUY, confidence=0.5, reason="  ",
                      source_strategy="x", adapter_version="1")


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        AdapterSignal(side=SignalSide.BUY, confidence=1.5, reason="r",
                      source_strategy="x", adapter_version="1")


def test_signal_carries_provenance():
    s = AdapterSignal(side=SignalSide.BUY, confidence=0.5, reason="r",
                      source_strategy="MACD", source_repo_url=REPO, adapter_version="2.0")
    assert s.source_strategy == "MACD" and s.source_repo_url == REPO and s.adapter_version == "2.0"


def test_to_model_signal_mapping():
    s = AdapterSignal(side=SignalSide.SELL, confidence=0.4, reason="down",
                      suggested_stop_loss=1.1, suggested_take_profit=1.0,
                      source_strategy="x", adapter_version="1")
    m = s.to_model_signal(symbol="EURUSD", timeframe="M15",
                          timestamp=datetime(2024, 1, 1), strategy_id="x-1")
    assert m.action is SignalAction.SELL
    assert m.confidence == 0.4 and m.rationale == "down"


def test_to_model_signal_none_is_hold():
    s = AdapterSignal(side=SignalSide.NONE, source_strategy="x", adapter_version="1")
    m = s.to_model_signal(symbol="EURUSD", timeframe="M15",
                          timestamp=datetime(2024, 1, 1), strategy_id="x-1")
    assert m.action is SignalAction.HOLD


# --------------------------------------------------------------------------- #
# Metadata / support checks
# --------------------------------------------------------------------------- #
def test_metadata_defaults_source_strategy_to_name():
    meta = AdapterMetadata(name="foo", version="1")
    assert meta.source_strategy == "foo"


def test_supports_symbol_and_timeframe():
    a = UpDownAdapter()
    assert a.supports_symbol("EURUSD") and not a.supports_symbol("GBPUSD")
    assert a.supports_timeframe("M15") and not a.supports_timeframe("H1")


def test_supports_any_when_unrestricted():
    a = RaisingAdapter()  # supported_* = None
    assert a.supports_symbol("ANYTHING")
    assert a.supports_timeframe("W1")


# --------------------------------------------------------------------------- #
# validate_inputs
# --------------------------------------------------------------------------- #
def test_validate_inputs_ok():
    a = UpDownAdapter()
    candles = generate_candles("EURUSD", "M15", n=20)
    assert a.validate_inputs(candles) is None


def test_validate_inputs_rejects_empty_and_short():
    a = UpDownAdapter()
    assert a.validate_inputs(None) is not None
    assert a.validate_inputs(generate_candles("EURUSD", "M15", n=1).iloc[0:0]) is not None
    short = generate_candles("EURUSD", "M15", n=3)  # < min_candles (5)
    assert "insufficient" in a.validate_inputs(short)


def test_validate_inputs_rejects_unsupported_symbol_timeframe():
    a = UpDownAdapter()
    assert "not supported" in a.validate_inputs(generate_candles("GBPUSD", "M15", n=20))
    assert "not supported" in a.validate_inputs(generate_candles("EURUSD", "H1", n=20))


def test_validate_inputs_missing_columns():
    a = UpDownAdapter()
    candles = generate_candles("EURUSD", "M15", n=20).drop(columns=["close"])
    assert "missing candle columns" in a.validate_inputs(candles)


def test_validate_inputs_feature_length_mismatch():
    a = UpDownAdapter()
    candles = generate_candles("EURUSD", "M15", n=20)
    assert a.validate_inputs(candles, features=candles.iloc[:10]) is not None


# --------------------------------------------------------------------------- #
# generate_signal: fail-safe behaviour
# --------------------------------------------------------------------------- #
def test_generate_signal_actionable_buy():
    a = UpDownAdapter()
    candles = generate_candles("EURUSD", "M15", n=50, start_price=1.0)
    # Force an uptrend so the deterministic rule yields BUY.
    candles.loc[candles.index[-1], "close"] = candles["close"].iloc[0] + 0.05
    sig = a.generate_signal(candles)
    assert sig.side is SignalSide.BUY
    assert sig.is_actionable and sig.confidence > 0
    assert sig.source_strategy == "Up/Down Demo"
    assert sig.source_repo_url == REPO
    assert sig.adapter_version == "1.2.3"
    assert sig.reason


def test_generate_signal_insufficient_data_is_none():
    a = UpDownAdapter()
    sig = a.generate_signal(generate_candles("EURUSD", "M15", n=2))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_generate_signal_unsupported_symbol_is_none():
    a = UpDownAdapter()
    sig = a.generate_signal(generate_candles("GBPUSD", "M15", n=20))
    assert sig.side is SignalSide.NONE


def test_generate_signal_swallows_adapter_errors():
    a = RaisingAdapter()
    sig = a.generate_signal(generate_candles("EURUSD", "M15", n=10))
    assert sig.side is SignalSide.NONE
    assert "adapter error" in sig.reason and "boom" in sig.reason


def test_generate_signal_rejects_non_signal_return():
    a = BadReturnAdapter()
    sig = a.generate_signal(generate_candles("EURUSD", "M15", n=10))
    assert sig.side is SignalSide.NONE
    assert "non-signal" in sig.reason


# --------------------------------------------------------------------------- #
# Determinism (no AI / stable logic)
# --------------------------------------------------------------------------- #
def test_generate_signal_is_deterministic():
    a = UpDownAdapter()
    candles = generate_candles("EURUSD", "M15", n=60, seed=3)
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())


def test_cannot_instantiate_abstract_base():
    with pytest.raises(TypeError):
        StrategyAdapter()  # type: ignore[abstract]
