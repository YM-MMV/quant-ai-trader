"""Tests for candle querying with DuckDB (services/data_service/query.py).

Uses fake candle data only — no MT5, no OpenBB, no network.
"""
from datetime import timedelta

import pytest

from services.data_service.query import CandleQuery
from services.data_service.sample_data import generate_candles, timeframe_minutes
from services.data_service.storage import CandleStore


@pytest.fixture
def populated_store(tmp_path):
    """A store with 600 fake EURUSD M15 candles."""
    store = CandleStore(tmp_path)
    df = generate_candles("EURUSD", "M15", n=600)
    store.save(df)
    return store, df


def test_last_500(populated_store):
    store, df = populated_store
    q = CandleQuery(store)
    last = q.last_n("EURUSD", "M15", 500)

    assert len(last) == 500
    assert last["timestamp"].is_monotonic_increasing
    # Should be the newest 500: max matches overall max, and it ends the series.
    assert last["timestamp"].max() == df["timestamp"].max()
    assert last["timestamp"].min() == df["timestamp"].sort_values().iloc[-500]


def test_last_n_more_than_available_returns_all(populated_store):
    store, df = populated_store
    q = CandleQuery(store)
    last = q.last_n("EURUSD", "M15", 10_000)
    assert len(last) == len(df)


def test_last_n_invalid(populated_store):
    store, _ = populated_store
    q = CandleQuery(store)
    with pytest.raises(ValueError):
        q.last_n("EURUSD", "M15", 0)


def test_date_range_inclusive(populated_store):
    store, df = populated_store
    q = CandleQuery(store)
    ts = df["timestamp"].sort_values().reset_index(drop=True)
    start = ts.iloc[100]
    end = ts.iloc[199]

    out = q.date_range("EURUSD", "M15", start, end)
    assert len(out) == 100  # indices 100..199 inclusive
    assert out["timestamp"].min() == start
    assert out["timestamp"].max() == end
    assert out["timestamp"].is_monotonic_increasing


def test_date_range_partial_window(populated_store):
    store, df = populated_store
    q = CandleQuery(store)
    ts = df["timestamp"].sort_values().reset_index(drop=True)
    minutes = timeframe_minutes("M15")
    # A window starting before all data and ending mid-series.
    start = ts.iloc[0] - timedelta(minutes=minutes * 5)
    end = ts.iloc[49]
    out = q.date_range("EURUSD", "M15", start, end)
    assert len(out) == 50


def test_date_range_outside_returns_empty(populated_store):
    store, df = populated_store
    q = CandleQuery(store)
    far_future = df["timestamp"].max() + timedelta(days=3650)
    out = q.date_range("EURUSD", "M15", far_future, far_future + timedelta(days=1))
    assert len(out) == 0


def test_date_range_bad_bounds(populated_store):
    store, df = populated_store
    q = CandleQuery(store)
    with pytest.raises(ValueError):
        q.date_range("EURUSD", "M15", df["timestamp"].max(), df["timestamp"].min())


def test_query_missing_file_raises(tmp_path):
    q = CandleQuery(CandleStore(tmp_path))
    with pytest.raises(FileNotFoundError):
        q.last_n("EURUSD", "M15", 10)
