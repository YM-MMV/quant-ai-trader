"""Shared pytest fixtures for the strategy-adapter tests (M8).

Provides small factories for hand-built candle frames in the canonical schema so
each adapter test can construct exactly the price path it needs to force a BUY,
a SELL, or an abstention — without any network/broker access.
"""
from datetime import datetime

import pandas as pd
import pytest

from services.data_service.sample_data import TIMEFRAME_MINUTES

_SCHEMA_EXTRAS = {"tick_volume": 100, "real_volume": 0, "source": "test"}


def _build(opens, highs, lows, closes, symbol, timeframe, spread, start):
    n = len(closes)
    minutes = TIMEFRAME_MINUTES[timeframe]
    ts = pd.date_range(start=start, periods=n, freq=f"{minutes}min")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": [_SCHEMA_EXTRAS["tick_volume"]] * n,
            "spread": [spread] * n,
            "real_volume": [_SCHEMA_EXTRAS["real_volume"]] * n,
            "symbol": [symbol] * n,
            "timeframe": [timeframe] * n,
            "source": [_SCHEMA_EXTRAS["source"]] * n,
        }
    )


@pytest.fixture
def make_candles():
    """Factory: explicit OHLC arrays → canonical candle DataFrame."""

    def factory(
        opens, highs, lows, closes, *,
        symbol="EURUSD", timeframe="H1", spread=10, start=datetime(2024, 1, 1),
    ):
        return _build(
            list(map(float, opens)), list(map(float, highs)),
            list(map(float, lows)), list(map(float, closes)),
            symbol, timeframe, spread, start,
        )

    return factory


@pytest.fixture
def from_close():
    """Factory: a close path → candle DataFrame with synthesized OHLC.

    Open defaults to the prior close; high/low wrap the open/close by ``pad`` so
    every bar is internally consistent (low <= open/close <= high).
    """

    def factory(
        closes, *, opens=None, highs=None, lows=None,
        symbol="EURUSD", timeframe="H1", spread=10,
        start=datetime(2024, 1, 1), pad=0.0005,
    ):
        closes = list(map(float, closes))
        n = len(closes)
        opens = [closes[0]] + closes[:-1] if opens is None else list(map(float, opens))
        highs = (
            [max(opens[i], closes[i]) + pad for i in range(n)]
            if highs is None else list(map(float, highs))
        )
        lows = (
            [min(opens[i], closes[i]) - pad for i in range(n)]
            if lows is None else list(map(float, lows))
        )
        return _build(opens, highs, lows, closes, symbol, timeframe, spread, start)

    return factory
