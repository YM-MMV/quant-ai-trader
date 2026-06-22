"""Deterministic fake candle generation for development and tests.

This produces plausibly-shaped OHLCV data **without any network access or real
broker/data feed** (no MT5, no OpenBB). It exists so storage/query code can be
built and tested before real data sources are integrated (later milestones).

The generated frame uses the canonical candle schema (see
``services.data_service.storage.REQUIRED_COLUMNS``), mirroring MetaTrader 5's
rates layout: ``tick_volume``, ``spread``, ``real_volume`` plus ``symbol``,
``timeframe`` and ``source``.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pandas as pd

# Minutes-per-bar for the timeframes we support (mirrors config/timeframes.yaml).
TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

SAMPLE_SOURCE = "sample"


def timeframe_minutes(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; known: {sorted(TIMEFRAME_MINUTES)}"
        )
    return TIMEFRAME_MINUTES[timeframe]


def generate_candles(
    symbol: str = "EURUSD",
    timeframe: str = "M15",
    n: int = 500,
    *,
    start: datetime | None = None,
    start_price: float = 1.1000,
    seed: int = 42,
    source: str = SAMPLE_SOURCE,
) -> pd.DataFrame:
    """Generate ``n`` fake candles as a DataFrame in canonical schema.

    Deterministic for a given ``seed`` so tests are stable. Candles are spaced
    by the timeframe's minute duration and returned sorted ascending by
    timestamp. Prices follow a small bounded random walk; OHLC are internally
    consistent (low <= open/close <= high).
    """
    if n <= 0:
        raise ValueError("n must be positive")

    minutes = timeframe_minutes(timeframe)
    if start is None:
        # Anchor in the past so ranges/last-N tests have headroom. Fixed (no
        # wall-clock) to keep generation deterministic.
        start = datetime(2024, 1, 1, 0, 0, 0)

    rng = random.Random(seed)
    rows = []
    price = float(start_price)
    # Pip size: 0.01 for JPY/metal-ish, else 0.0001. Kept simple for fakes.
    pip = 0.01 if symbol.endswith("JPY") or symbol.startswith("XAU") else 0.0001

    for i in range(n):
        ts = start + timedelta(minutes=minutes * i)
        open_ = price
        drift = rng.uniform(-10, 10) * pip
        close = max(pip, open_ + drift)
        high = max(open_, close) + abs(rng.uniform(0, 8)) * pip
        low = min(open_, close) - abs(rng.uniform(0, 8)) * pip
        low = max(low, pip / 2)  # never non-positive

        rows.append(
            {
                "timestamp": pd.Timestamp(ts),
                "open": round(open_, 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(close, 5),
                "tick_volume": rng.randint(50, 5000),
                "spread": rng.randint(1, 30),
                "real_volume": rng.randint(0, 1000),
                "symbol": symbol,
                "timeframe": timeframe,
                "source": source,
            }
        )
        price = close  # next bar opens at this close

    return pd.DataFrame(rows)
