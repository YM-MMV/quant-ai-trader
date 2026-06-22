"""Tests for the mock Kronos predictor and its interface (M15).

Covers: the prediction schema + invariants, the mock's determinism and the
direction it infers from trends, edge cases (flat/short windows), and a demo
showing a strategy adapter consuming a Kronos prediction as an optional filter.
No real Kronos, no MT5, no network.
"""
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from services.data_service.sample_data import generate_candles
from services.kronos_service.base import (
    KronosPrediction,
    KronosPredictor,
    PredDirection,
)
from services.kronos_service.mock_kronos import MockKronos
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)

REQUIRED_FIELDS = [
    "symbol", "timeframe", "lookback", "pred_len", "predicted_return",
    "predicted_high", "predicted_low", "predicted_close",
    "predicted_volatility", "confidence_proxy", "model_name", "is_mock",
]


def rising_candles(n: int = 120):
    # Deterministic seed → reproducible series; this seed trends upward enough
    # for the mock to read a positive drift over the window.
    return generate_candles("EURUSD", "H1", n=n, seed=7)


# --------------------------------------------------------------------------- #
# Prediction schema / invariants
# --------------------------------------------------------------------------- #
def test_prediction_has_all_required_fields():
    pred = MockKronos().predict(rising_candles(), lookback=64, pred_len=1)
    dumped = pred.model_dump()
    for field in REQUIRED_FIELDS:
        assert field in dumped, f"missing required field: {field}"


def test_prediction_is_flagged_mock():
    pred = MockKronos().predict(rising_candles())
    assert pred.is_mock is True
    assert pred.model_name == "kronos-mock"


def test_prediction_rejects_close_outside_band():
    with pytest.raises(ValidationError):
        KronosPrediction(
            symbol="EURUSD", timeframe="H1", lookback=10, pred_len=1,
            predicted_return=0.0, predicted_high=1.10, predicted_low=1.05,
            predicted_close=1.20,  # above high → invalid
            predicted_volatility=0.01, confidence_proxy=0.5,
        )


def test_prediction_rejects_inverted_band():
    with pytest.raises(ValidationError):
        KronosPrediction(
            symbol="EURUSD", timeframe="H1", lookback=10, pred_len=1,
            predicted_return=0.0, predicted_high=1.00, predicted_low=1.10,
            predicted_close=1.05, predicted_volatility=0.01, confidence_proxy=0.5,
        )


def test_prediction_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        KronosPrediction(
            symbol="EURUSD", timeframe="H1", lookback=10, pred_len=1,
            predicted_return=0.0, predicted_high=1.10, predicted_low=1.00,
            predicted_close=1.05, predicted_volatility=0.01, confidence_proxy=1.5,
        )


# --------------------------------------------------------------------------- #
# Mock behaviour
# --------------------------------------------------------------------------- #
def test_mock_is_an_instance_of_the_interface():
    assert isinstance(MockKronos(), KronosPredictor)


def test_mock_is_deterministic():
    candles = rising_candles()
    a = MockKronos().predict(candles, lookback=64, pred_len=1)
    b = MockKronos().predict(candles, lookback=64, pred_len=1)
    assert a == b
    assert a.model_dump() == b.model_dump()


def test_mock_infers_symbol_and_timeframe_from_frame():
    pred = MockKronos().predict(generate_candles("XAUUSD", "M15", n=80, seed=3))
    assert pred.symbol == "XAUUSD"
    assert pred.timeframe == "M15"


def test_explicit_symbol_overrides_frame():
    pred = MockKronos().predict(rising_candles(), symbol="GBPUSD", timeframe="H4")
    assert pred.symbol == "GBPUSD"
    assert pred.timeframe == "H4"


def test_prices_are_positive_and_ordered():
    pred = MockKronos().predict(rising_candles())
    assert pred.predicted_low > 0
    assert pred.predicted_low <= pred.predicted_close <= pred.predicted_high
    assert pred.predicted_volatility >= 0.0
    assert 0.0 <= pred.confidence_proxy <= 1.0


def test_uptrend_predicts_up():
    closes = [1.0 + 0.001 * i for i in range(50)]  # strictly increasing
    pred = MockKronos().predict(closes, symbol="EURUSD", timeframe="H1")
    assert pred.direction is PredDirection.UP
    assert pred.predicted_return > 0
    assert pred.predicted_close > closes[-1]


def test_downtrend_predicts_down():
    closes = [2.0 - 0.001 * i for i in range(50)]  # strictly decreasing
    pred = MockKronos().predict(closes, symbol="EURUSD", timeframe="H1")
    assert pred.direction is PredDirection.DOWN
    assert pred.predicted_return < 0
    assert pred.predicted_close < closes[-1]


def test_flat_series_predicts_flat_zero_confidence():
    closes = [1.2345] * 40
    pred = MockKronos().predict(closes, symbol="EURUSD", timeframe="H1")
    assert pred.direction is PredDirection.FLAT
    assert pred.predicted_return == 0.0
    assert pred.predicted_volatility == 0.0
    assert pred.confidence_proxy == 0.0
    # Degenerate band collapses to the close.
    assert pred.predicted_high == pred.predicted_low == pred.predicted_close


def test_single_close_is_valid_flat():
    pred = MockKronos().predict([1.5], symbol="EURUSD", timeframe="H1")
    assert pred.lookback == 1
    assert pred.direction is PredDirection.FLAT


def test_lookback_limits_window():
    closes = [1.0 + 0.001 * i for i in range(200)]
    pred = MockKronos().predict(closes, symbol="EURUSD", timeframe="H1", lookback=30)
    assert pred.lookback == 30


def test_longer_horizon_scales_return_and_vol():
    candles = rising_candles()
    short = MockKronos().predict(candles, lookback=64, pred_len=1)
    long = MockKronos().predict(candles, lookback=64, pred_len=10)
    # More bars ahead ⇒ larger magnitude move and wider volatility band.
    assert abs(long.predicted_return) >= abs(short.predicted_return)
    assert long.predicted_volatility >= short.predicted_volatility


def test_empty_input_raises():
    with pytest.raises(ValueError):
        MockKronos().predict([], symbol="EURUSD", timeframe="H1")


def test_invalid_lookback_and_pred_len_raise():
    with pytest.raises(ValueError):
        MockKronos().predict(rising_candles(), lookback=0)
    with pytest.raises(ValueError):
        MockKronos().predict(rising_candles(), pred_len=0)


# --------------------------------------------------------------------------- #
# Filter helpers
# --------------------------------------------------------------------------- #
def test_agrees_with_and_confidence_helpers():
    up = MockKronos().predict([1.0 + 0.001 * i for i in range(50)],
                              symbol="EURUSD", timeframe="H1")
    assert up.agrees_with("BUY") is True
    assert up.agrees_with(SignalSide.BUY) is True
    assert up.agrees_with("SELL") is False
    assert up.is_confident(0.0) is True
    assert up.is_confident(1.1) is False


def test_flat_agrees_with_neither():
    flat = MockKronos().predict([1.0] * 30, symbol="EURUSD", timeframe="H1")
    assert flat.agrees_with("BUY") is False
    assert flat.agrees_with("SELL") is False


# --------------------------------------------------------------------------- #
# Strategy adapters can consume a Kronos prediction  (Done-when)
# --------------------------------------------------------------------------- #
class KronosFilteredAdapter(StrategyAdapter):
    """Demo adapter: wants to BUY, but only if Kronos agrees (else abstains)."""

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="kronos_filtered_demo", version="1.0.0",
            source_strategy="Kronos Filter Demo", category="test", min_candles=2,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        entry = float(candles["close"].iloc[-1])
        if kronos_prediction is not None and not kronos_prediction.agrees_with("BUY"):
            return self.none_signal("Kronos does not confirm the long bias")
        return self.make_signal(
            SignalSide.BUY, 0.6, "baseline long, Kronos-confirmed",
            suggested_stop_loss=entry * 0.99, suggested_take_profit=entry * 1.02,
        )


def test_adapter_consumes_confirming_prediction():
    candles = generate_candles("EURUSD", "H1", n=60, seed=1)
    up = MockKronos().predict([1.0 + 0.001 * i for i in range(50)],
                              symbol="EURUSD", timeframe="H1")
    signal = KronosFilteredAdapter().generate_signal(candles, kronos_prediction=up)
    assert signal.side is SignalSide.BUY


def test_adapter_abstains_when_prediction_disagrees():
    candles = generate_candles("EURUSD", "H1", n=60, seed=1)
    down = MockKronos().predict([2.0 - 0.001 * i for i in range(50)],
                                symbol="EURUSD", timeframe="H1")
    signal = KronosFilteredAdapter().generate_signal(candles, kronos_prediction=down)
    assert signal.side is SignalSide.NONE
    assert "Kronos" in signal.reason


def test_adapter_works_without_a_prediction():
    candles = generate_candles("EURUSD", "H1", n=60, seed=1)
    signal = KronosFilteredAdapter().generate_signal(candles, kronos_prediction=None)
    assert signal.side is SignalSide.BUY
