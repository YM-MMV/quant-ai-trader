"""Tests for candle Parquet storage (services/data_service/storage.py).

Uses fake candle data only — no MT5, no OpenBB, no network.
"""
import pandas as pd
import pytest

from services.data_service.sample_data import generate_candles
from services.data_service.storage import (
    REQUIRED_COLUMNS,
    CandleSchemaError,
    CandleStore,
    validate_candles,
)


def test_sample_data_has_required_columns():
    df = generate_candles("EURUSD", "M15", n=10)
    for col in REQUIRED_COLUMNS:
        assert col in df.columns
    assert len(df) == 10


def test_sample_data_is_deterministic():
    a = generate_candles("EURUSD", "M15", n=20, seed=7)
    b = generate_candles("EURUSD", "M15", n=20, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_sample_ohlc_internally_consistent():
    df = generate_candles("EURUSD", "M15", n=200)
    assert (df["high"] >= df["low"]).all()
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
    assert (df[["open", "high", "low", "close"]] > 0).all().all()


def test_save_and_load_roundtrip(tmp_path):
    store = CandleStore(tmp_path)
    df = generate_candles("EURUSD", "M15", n=500)
    path = store.save(df)
    assert path.is_file()
    assert store.exists("EURUSD", "M15")

    loaded = store.load("EURUSD", "M15")
    assert len(loaded) == 500
    for col in REQUIRED_COLUMNS:
        assert col in loaded.columns
    assert loaded["symbol"].unique().tolist() == ["EURUSD"]
    assert loaded["timeframe"].unique().tolist() == ["M15"]


def test_save_path_layout(tmp_path):
    store = CandleStore(tmp_path)
    store.save(generate_candles("EURUSD", "M15", n=5))
    assert (tmp_path / "EURUSD" / "M15.parquet").is_file()


def test_load_sorted_ascending_even_if_input_shuffled(tmp_path):
    store = CandleStore(tmp_path)
    df = generate_candles("EURUSD", "M15", n=100)
    shuffled = df.sample(frac=1.0, random_state=1).reset_index(drop=True)
    assert not shuffled["timestamp"].is_monotonic_increasing  # really shuffled
    store.save(shuffled)
    loaded = store.load("EURUSD", "M15")
    assert loaded["timestamp"].is_monotonic_increasing


def test_save_infers_symbol_and_timeframe(tmp_path):
    store = CandleStore(tmp_path)
    df = generate_candles("XAUUSD", "H1", n=10)
    store.save(df)  # no explicit symbol/timeframe
    assert store.exists("XAUUSD", "H1")


def test_validate_missing_column_raises():
    df = generate_candles("EURUSD", "M15", n=5).drop(columns=["spread"])
    with pytest.raises(CandleSchemaError):
        validate_candles(df)


def test_save_missing_column_raises(tmp_path):
    store = CandleStore(tmp_path)
    df = generate_candles("EURUSD", "M15", n=5).drop(columns=["real_volume"])
    with pytest.raises(CandleSchemaError):
        store.save(df)


def test_save_empty_frame_raises(tmp_path):
    store = CandleStore(tmp_path)
    empty = generate_candles("EURUSD", "M15", n=1).iloc[0:0]
    with pytest.raises(CandleSchemaError):
        store.save(empty)


def test_save_mixed_symbols_raises(tmp_path):
    store = CandleStore(tmp_path)
    df = pd.concat(
        [generate_candles("EURUSD", "M15", n=5), generate_candles("GBPUSD", "M15", n=5)],
        ignore_index=True,
    )
    with pytest.raises(CandleSchemaError):
        store.save(df)  # cannot infer a single symbol


def test_load_missing_raises(tmp_path):
    store = CandleStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("EURUSD", "M15")


@pytest.mark.parametrize("bad", ["../etc", "..", ".", "a/b", "a\\b", "", "a b"])
def test_unsafe_symbol_rejected(tmp_path, bad):
    store = CandleStore(tmp_path)
    with pytest.raises(ValueError):
        store.path_for(bad, "M15")


@pytest.mark.parametrize("bad", ["..", ".", "../x"])
def test_unsafe_timeframe_rejected(tmp_path, bad):
    store = CandleStore(tmp_path)
    with pytest.raises(ValueError):
        store.path_for("EURUSD", bad)
