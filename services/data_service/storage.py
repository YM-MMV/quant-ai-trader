"""Candle storage on Parquet.

Historical candles are persisted as Parquet files (one file per
symbol/timeframe), never fed raw into an LLM. This module owns the on-disk
layout, schema validation, and save/load. Querying lives in ``query.py``
(DuckDB over these Parquet files).

Layout:  ``<base_dir>/<symbol>/<timeframe>.parquet``

Canonical schema (mirrors MetaTrader 5 rates + provenance):
    timestamp, open, high, low, close,
    tick_volume, spread, real_volume,
    symbol, timeframe, source
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
    "symbol",
    "timeframe",
    "source",
)

# Symbol/timeframe become path segments — keep them to safe characters so they
# can never traverse the tree or inject into a query path.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class CandleSchemaError(ValueError):
    """Raised when a candle frame is missing required columns or is malformed."""


def _check_segment(name: str, kind: str) -> str:
    if not name or not _SAFE_SEGMENT.match(name):
        raise ValueError(f"unsafe {kind} {name!r}: must match [A-Za-z0-9._-]+")
    # "." and ".." pass the charset check but are path-traversal segments.
    if set(name) == {"."}:
        raise ValueError(f"unsafe {kind} {name!r}: dot-only segment not allowed")
    return name


def validate_candles(df: pd.DataFrame) -> None:
    """Validate that ``df`` has the required columns and is non-empty."""
    if df is None or len(df) == 0:
        raise CandleSchemaError("candle frame is empty")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise CandleSchemaError(f"missing required columns: {missing}")


class CandleStore:
    """Reads and writes candle Parquet files under a base directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def path_for(self, symbol: str, timeframe: str) -> Path:
        _check_segment(symbol, "symbol")
        _check_segment(timeframe, "timeframe")
        return self.base_dir / symbol / f"{timeframe}.parquet"

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self.path_for(symbol, timeframe).is_file()

    def save(
        self,
        df: pd.DataFrame,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> Path:
        """Validate, sort ascending by timestamp, and write candles to Parquet.

        ``symbol``/``timeframe`` may be omitted if the frame contains exactly one
        of each (inferred from the data). Overwrites any existing file.
        """
        validate_candles(df)

        symbol = symbol or _single_value(df, "symbol")
        timeframe = timeframe or _single_value(df, "timeframe")

        # Guard against mixing instruments/timeframes in one file.
        if df["symbol"].nunique() != 1 or df["symbol"].iloc[0] != symbol:
            raise CandleSchemaError(
                f"frame must contain exactly symbol {symbol!r}"
            )
        if df["timeframe"].nunique() != 1 or df["timeframe"].iloc[0] != timeframe:
            raise CandleSchemaError(
                f"frame must contain exactly timeframe {timeframe!r}"
            )

        out = df.copy()
        out["timestamp"] = pd.to_datetime(out["timestamp"])
        out = out.sort_values("timestamp").reset_index(drop=True)

        path = self.path_for(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(path, engine="pyarrow", index=False)
        return path

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Load all candles for a symbol/timeframe, sorted ascending."""
        path = self.path_for(symbol, timeframe)
        if not path.is_file():
            raise FileNotFoundError(f"no candles stored for {symbol}/{timeframe}: {path}")
        df = pd.read_parquet(path, engine="pyarrow")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)


def _single_value(df: pd.DataFrame, column: str) -> str:
    values = df[column].unique()
    if len(values) != 1:
        raise CandleSchemaError(
            f"cannot infer {column}: frame has {len(values)} distinct values {list(values)!r}"
        )
    return str(values[0])


__all__ = [
    "REQUIRED_COLUMNS",
    "CandleSchemaError",
    "CandleStore",
    "validate_candles",
]
