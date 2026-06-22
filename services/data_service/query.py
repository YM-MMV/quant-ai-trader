"""Candle querying with DuckDB over Parquet.

DuckDB reads the Parquet files written by ``storage.py`` directly, so we can
answer "last N candles" and "date range" queries without loading whole files
into memory or feeding raw history to an LLM. All results are returned sorted
ascending by timestamp.
"""
from __future__ import annotations

from datetime import datetime

import duckdb
import pandas as pd

from services.data_service.storage import CandleStore


class CandleQuery:
    """DuckDB-backed read queries over a :class:`CandleStore`."""

    def __init__(self, store: CandleStore) -> None:
        self.store = store

    def _parquet_path(self, symbol: str, timeframe: str) -> str:
        path = self.store.path_for(symbol, timeframe)
        if not path.is_file():
            raise FileNotFoundError(
                f"no candles stored for {symbol}/{timeframe}: {path}"
            )
        # Escape single quotes for safe embedding in read_parquet('...').
        # (symbol/timeframe are already restricted to a safe charset upstream.)
        return str(path).replace("'", "''")

    def last_n(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """Return the most recent ``n`` candles, sorted ascending by timestamp."""
        if n <= 0:
            raise ValueError("n must be positive")
        src = self._parquet_path(symbol, timeframe)
        con = duckdb.connect()
        try:
            # Take the newest N (DESC + LIMIT), then re-sort ascending for output.
            df = con.execute(
                f"SELECT * FROM read_parquet('{src}') "
                "ORDER BY timestamp DESC LIMIT ?",
                [n],
            ).df()
        finally:
            con.close()
        return df.sort_values("timestamp").reset_index(drop=True)

    def date_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Return candles with ``start <= timestamp <= end`` (inclusive), ascending."""
        if end < start:
            raise ValueError("end must be >= start")
        src = self._parquet_path(symbol, timeframe)
        con = duckdb.connect()
        try:
            df = con.execute(
                f"SELECT * FROM read_parquet('{src}') "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp ASC",
                [pd.Timestamp(start), pd.Timestamp(end)],
            ).df()
        finally:
            con.close()
        return df.reset_index(drop=True)


__all__ = ["CandleQuery"]
