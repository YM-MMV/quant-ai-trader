"""Tests for the local pricer-tick → OHLCV candle source."""
from __future__ import annotations

import pandas as pd
import pytest

import apps.agent.tools as tools
from services.data_service.local_data import (
    LocalDataError,
    ingest_symbol,
    load_local_candles,
    resample_ticks_to_candles,
)
from services.data_service.storage import REQUIRED_COLUMNS, CandleStore


def _ticks(day: str = "2026-05-11") -> pd.DataFrame:
    """Six XAUUSD-ish ticks at 30s spacing -> three 1-minute bars (2 ticks each)."""
    times = pd.date_range(f"{day} 00:00:00", periods=6, freq="30s")
    bids = [4691.0, 4692.0, 4690.0, 4693.0, 4694.0, 4692.5]
    asks = [b + 0.3 for b in bids]  # 0.3 spread -> 300 points at point_size 0.001
    return pd.DataFrame({"time": times.astype(str), "bid": bids, "ask": asks, "sym": "XAUUSD"})


def test_resample_builds_canonical_ohlc():
    bars = resample_ticks_to_candles(_ticks(), symbol="XAUUSD", timeframe="M1")
    assert list(bars.columns) == list(REQUIRED_COLUMNS)
    assert len(bars) == 3  # 6 ticks @30s over 3 minutes

    first = bars.iloc[0]
    assert first["open"] == pytest.approx((4691.0 + 4691.3) / 2)   # mid of tick 0
    assert first["high"] == pytest.approx((4692.0 + 4692.3) / 2)   # mid of tick 1
    assert first["low"] == pytest.approx((4691.0 + 4691.3) / 2)
    assert first["close"] == pytest.approx((4692.0 + 4692.3) / 2)
    assert first["tick_volume"] == 2
    assert first["spread"] == 300                                   # 0.3 / 0.001
    assert (bars["symbol"] == "XAUUSD").all()
    assert (bars["timeframe"] == "M1").all()
    assert (bars["source"] == "local").all()


def test_resample_rejects_missing_columns():
    with pytest.raises(LocalDataError):
        resample_ticks_to_candles(
            pd.DataFrame({"time": [], "bid": []}), symbol="XAUUSD", timeframe="M1"
        )


def test_resample_rejects_empty():
    with pytest.raises(LocalDataError):
        resample_ticks_to_candles(pd.DataFrame(), symbol="XAUUSD", timeframe="M1")


def test_load_local_candles_round_trip(tmp_path):
    bars = resample_ticks_to_candles(_ticks(), symbol="XAUUSD", timeframe="M1")
    CandleStore(tmp_path).save(bars, "XAUUSD", "M1")
    got = load_local_candles("XAUUSD", "M1", 2, store_dir=tmp_path)
    assert len(got) == 2  # tail(n)
    assert list(got.columns) == list(REQUIRED_COLUMNS)


def test_load_local_missing_raises(tmp_path):
    with pytest.raises(LocalDataError):
        load_local_candles("XAUUSD", "M1", 10, store_dir=tmp_path)


def test_ingest_symbol_writes_store(tmp_path):
    tick_dir = tmp_path / "ticks"
    tick_dir.mkdir()
    for day in ("2026-05-11", "2026-05-12"):
        _ticks(day).to_parquet(tick_dir / f"XAUUSD_{day.replace('-', '_')}.parquet")
    store_dir = tmp_path / "hist"

    written = ingest_symbol("XAUUSD", ["M1", "M5"], tick_dir=tick_dir, store_dir=store_dir)

    assert set(written) == {"M1", "M5"}
    m1_path, m1_n = written["M1"]
    assert m1_path.is_file()
    assert m1_n == 6  # 3 bars/day across 2 distinct days, no double counting
    # And it reads back through the public loader.
    assert len(load_local_candles("XAUUSD", "M1", 100, store_dir=store_dir)) == 6


def test_ingest_no_files_raises(tmp_path):
    with pytest.raises(LocalDataError):
        ingest_symbol("XAUUSD", ["M1"], tick_dir=tmp_path)


def test_candle_frame_routes_local(monkeypatch):
    captured: dict = {}

    def fake_loader(symbol, timeframe, n):
        captured.update(symbol=symbol, timeframe=timeframe, n=n)
        return pd.DataFrame({c: [] for c in REQUIRED_COLUMNS})

    monkeypatch.setattr("services.data_service.local_data.load_local_candles", fake_loader)
    tools._candle_frame("XAUUSD", "M5", 123, source="local")
    assert captured == {"symbol": "XAUUSD", "timeframe": "M5", "n": 123}
