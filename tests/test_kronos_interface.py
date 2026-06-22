"""Tests for real-Kronos integration + interface fallback (M16).

These run **without a GPU and without the real Kronos package**: the real
wrapper's prediction logic is exercised by injecting a fake underlying repo
predictor (a stand-in for the model's ``KronosPredictor``), and the fallback
paths verify that the system degrades to the mock / disabled mode when Kronos
is not installed.
"""
from datetime import datetime

import pandas as pd
import pytest

from services.data_service.sample_data import generate_candles
from services.kronos_service.base import KronosPrediction, KronosPredictor
from services.kronos_service.mock_kronos import MockKronos
from services.kronos_service import real_kronos
from services.kronos_service.real_kronos import (
    DisabledKronos,
    KronosDisabledError,
    KronosUnavailableError,
    RealKronos,
    kronos_available,
    load_kronos,
    load_predictions,
    save_prediction,
)

REQUIRED_FIELDS = [
    "symbol", "timeframe", "predicted_return", "predicted_high",
    "predicted_low", "predicted_close", "predicted_volatility",
    "confidence_proxy", "model_name", "is_mock",
]


class FakeRepoPredictor:
    """Stand-in for the Kronos repo's KronosPredictor.predict (no torch/GPU)."""

    def __init__(self, forecast: pd.DataFrame):
        self.forecast = forecast
        self.calls: list[dict] = []

    def predict(self, *, df, x_timestamp, y_timestamp, pred_len, T, top_p, sample_count):
        self.calls.append({
            "rows": len(df), "pred_len": pred_len, "cols": list(df.columns),
            "x_len": len(x_timestamp), "y_len": len(y_timestamp),
        })
        return self.forecast.iloc[:pred_len].reset_index(drop=True)


def make_forecast(closes, highs=None, lows=None) -> pd.DataFrame:
    n = len(closes)
    highs = highs or [c * 1.001 for c in closes]
    lows = lows or [c * 0.999 for c in closes]
    return pd.DataFrame({
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [1.0] * n, "amount": [1.0] * n,
    })


# --------------------------------------------------------------------------- #
# Availability + factory fallback  (Done-when: mock fallback works)
# --------------------------------------------------------------------------- #
def test_kronos_not_available_in_this_env():
    # The optional package is not installed in CI/test environments.
    assert kronos_available() is False


def test_auto_mode_falls_back_to_mock():
    predictor = load_kronos(mode="auto")
    assert isinstance(predictor, MockKronos)
    pred = predictor.predict(generate_candles("EURUSD", "H1", n=60), lookback=50)
    assert pred.is_mock is True


def test_mock_mode_returns_mock():
    assert isinstance(load_kronos(mode="mock"), MockKronos)


def test_disabled_mode_refuses_to_predict():
    predictor = load_kronos(mode="disabled")
    assert isinstance(predictor, DisabledKronos)
    with pytest.raises(KronosDisabledError):
        predictor.predict(generate_candles("EURUSD", "H1", n=60))


def test_real_mode_raises_when_unavailable():
    with pytest.raises(KronosUnavailableError):
        load_kronos(mode="real")


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        load_kronos(mode="bogus")


def test_auto_mode_uses_real_when_available(monkeypatch):
    monkeypatch.setattr(real_kronos, "kronos_available", lambda: True)
    predictor = load_kronos(mode="auto", predictor=FakeRepoPredictor(make_forecast([1.1])))
    assert isinstance(predictor, RealKronos)


# --------------------------------------------------------------------------- #
# RealKronos prediction logic (fake underlying predictor — no package needed)
# --------------------------------------------------------------------------- #
def candles_df(symbol="EURUSD", timeframe="M15", n=400):
    return generate_candles(symbol, timeframe, n=n, seed=11)


def test_real_kronos_implements_interface():
    rk = RealKronos(predictor=FakeRepoPredictor(make_forecast([1.1])))
    assert isinstance(rk, KronosPredictor)


def test_real_kronos_produces_valid_prediction():
    forecast = make_forecast([1.0860, 1.0865, 1.0868],
                             highs=[1.0870, 1.0872, 1.0875],
                             lows=[1.0850, 1.0848, 1.0842])
    rk = RealKronos(model_name="Kronos-small",
                    predictor=FakeRepoPredictor(forecast))
    df = candles_df()
    pred = rk.predict(df, symbol="EURUSD", timeframe="M15", lookback=400, pred_len=3)

    assert pred.is_mock is False
    assert pred.model_name == "kronos-small"
    assert pred.symbol == "EURUSD"
    assert pred.timeframe == "M15"
    for field in REQUIRED_FIELDS:
        assert field in pred.model_dump()
    # Band aggregation: high = max forecast high, low = min forecast low.
    assert pred.predicted_high == pytest.approx(1.0875, abs=1e-6)
    assert pred.predicted_low == pytest.approx(1.0842, abs=1e-6)
    assert pred.predicted_close == pytest.approx(1.0868, abs=1e-6)
    assert pred.predicted_low <= pred.predicted_close <= pred.predicted_high
    assert 0.0 <= pred.confidence_proxy <= 1.0


def test_real_kronos_return_is_relative_to_last_close():
    df = candles_df()
    last_close = float(df["close"].iloc[-1])
    target = round(last_close * 1.002, 5)
    rk = RealKronos(predictor=FakeRepoPredictor(make_forecast([target])))
    pred = rk.predict(df, symbol="EURUSD", timeframe="M15", lookback=300, pred_len=1)
    assert pred.predicted_return == pytest.approx((target - last_close) / last_close, rel=1e-3)


def test_real_kronos_clamps_lookback_to_max_context():
    fake = FakeRepoPredictor(make_forecast([1.1, 1.1]))
    rk = RealKronos(max_context=512, predictor=fake)
    df = candles_df(n=1000)
    pred = rk.predict(df, symbol="EURUSD", timeframe="M15", lookback=900, pred_len=2)
    # Only the most recent 512 candles are fed to the model.
    assert fake.calls[0]["rows"] == 512
    assert pred.lookback == 512


def test_real_kronos_feeds_ohlc_columns():
    fake = FakeRepoPredictor(make_forecast([1.1]))
    rk = RealKronos(predictor=fake)
    rk.predict(candles_df(), symbol="EURUSD", timeframe="M15", lookback=256, pred_len=1)
    cols = fake.calls[0]["cols"]
    assert {"open", "high", "low", "close"} <= set(cols)
    assert fake.calls[0]["y_len"] == 1


def test_real_kronos_requires_ohlc_dataframe():
    rk = RealKronos(predictor=FakeRepoPredictor(make_forecast([1.1])))
    with pytest.raises(ValueError):
        rk.predict([1.0, 1.1, 1.2], symbol="EURUSD", timeframe="M15")


def test_real_kronos_rejects_bad_params():
    rk = RealKronos(predictor=FakeRepoPredictor(make_forecast([1.1])))
    with pytest.raises(ValueError):
        rk.predict(candles_df(), lookback=0)
    with pytest.raises(ValueError):
        rk.predict(candles_df(), pred_len=0)


def test_real_kronos_unavailable_without_injected_predictor():
    # No package installed and no injected predictor → clear error on predict.
    rk = RealKronos()
    with pytest.raises(KronosUnavailableError):
        rk.predict(candles_df(), symbol="EURUSD", timeframe="M15", pred_len=1)


# --------------------------------------------------------------------------- #
# Interface compatibility: mock and real return the same schema
# --------------------------------------------------------------------------- #
def test_mock_and_real_share_schema():
    df = candles_df()
    mock_pred = MockKronos().predict(df, lookback=256, pred_len=4)
    real_pred = RealKronos(predictor=FakeRepoPredictor(make_forecast([1.1, 1.1, 1.1, 1.1]))) \
        .predict(df, symbol="EURUSD", timeframe="M15", lookback=256, pred_len=4)
    assert set(mock_pred.model_dump()) == set(real_pred.model_dump())
    assert mock_pred.is_mock is True
    assert real_pred.is_mock is False


# --------------------------------------------------------------------------- #
# Persistence to data/predictions/
# --------------------------------------------------------------------------- #
def test_save_and_load_prediction_round_trip(tmp_path):
    pred = MockKronos().predict(candles_df(), lookback=256, pred_len=2)
    path = save_prediction(pred, base_dir=tmp_path)
    assert path.is_file()
    assert path == tmp_path / pred.symbol / f"{pred.timeframe}.jsonl"

    loaded = load_predictions(pred.symbol, pred.timeframe, base_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0] == pred


def test_save_prediction_appends(tmp_path):
    p1 = MockKronos().predict(candles_df(), lookback=256, pred_len=1)
    p2 = MockKronos().predict(candles_df(), lookback=200, pred_len=1)
    save_prediction(p1, base_dir=tmp_path)
    save_prediction(p2, base_dir=tmp_path)
    assert len(load_predictions("EURUSD", "M15", base_dir=tmp_path)) == 2


def test_load_predictions_empty_when_missing(tmp_path):
    assert load_predictions("EURUSD", "M15", base_dir=tmp_path) == []
