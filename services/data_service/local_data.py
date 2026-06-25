"""Local historical market data from external pricer tick dumps.

The "pricer" exports raw **tick/quote** parquet files — one per symbol per day
(``<SYMBOL>_YYYY_MM_DD.parquet`` with ``time``/``bid``/``ask`` + L2 depth). This
module resamples the mid price into the project's canonical OHLCV candle schema
and persists it to a :class:`~services.data_service.storage.CandleStore`, so the
backtest/validation/loop read path is fast and identical to the ``mt5`` path.

This is the **``local`` candle source** — data only, never orders. The heavy
tick resampling is done **once** by :func:`ingest_symbol` (see
``scripts/ingest_pricer_history.py``); :func:`load_local_candles` is the cheap
per-call read used when ``source="local"``.

Canonical output schema matches :data:`services.data_service.storage.REQUIRED_COLUMNS`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from services.config_loader import PROJECT_ROOT
from services.data_service.sample_data import TIMEFRAME_MINUTES
from services.data_service.storage import REQUIRED_COLUMNS, CandleStore
from services.risk_service.symbol_specs import get_symbol_spec

# Resampled candles land here (git-ignored). Kept separate from data/raw (mt5
# downloads) so the two provenance sources never clobber one another.
DEFAULT_STORE_DIR = PROJECT_ROOT / "data" / "historical"
DEFAULT_TICK_COLUMNS = ("time", "bid", "ask")


class LocalDataError(RuntimeError):
    """Local historical data could not be read, resampled, or is empty."""


def _pandas_freq(timeframe: str) -> str:
    minutes = TIMEFRAME_MINUTES.get(timeframe.upper())
    if minutes is None:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; known: {sorted(TIMEFRAME_MINUTES)}"
        )
    return f"{minutes}min"


def resample_ticks_to_candles(
    ticks: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    source: str = "local",
    point_size: Optional[float] = None,
) -> pd.DataFrame:
    """Resample a tick frame (``time``, ``bid``, ``ask``) into canonical candles.

    OHLC is built from the **mid** price ``(bid+ask)/2``; bars are stamped at the
    bar *open* (left edge), matching MT5. ``tick_volume`` is the tick count in the
    bar, ``spread`` the mean bid/ask spread in **points** (rounded; needs a symbol
    spec for the point size), ``real_volume`` is 0 (unavailable from quotes). Empty
    bars (weekends/gaps) are dropped. Output columns are exactly
    :data:`REQUIRED_COLUMNS`.
    """
    if ticks is None or len(ticks) == 0:
        raise LocalDataError("no ticks to resample")
    for col in ("time", "bid", "ask"):
        if col not in ticks.columns:
            raise LocalDataError(
                f"tick frame missing required column {col!r}; got {list(ticks.columns)}"
            )

    df = pd.DataFrame({
        "time": pd.to_datetime(ticks["time"]),
        "bid": ticks["bid"].astype(float),
        "ask": ticks["ask"].astype(float),
    })
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread_price"] = df["ask"] - df["bid"]
    df = df.set_index("time").sort_index()

    freq = _pandas_freq(timeframe)
    grouped = df.resample(freq, label="left", closed="left")
    ohlc = grouped["mid"].ohlc()
    tick_volume = grouped["mid"].count()
    spread_price = grouped["spread_price"].mean()

    out = ohlc.dropna(subset=["open"]).copy()  # drop bars with no ticks
    if len(out) == 0:
        raise LocalDataError(f"no candles produced for {symbol}/{timeframe}")

    if point_size is None:
        spec = get_symbol_spec(symbol)
        point_size = spec.point_size if spec else 0.0
    out["tick_volume"] = tick_volume.reindex(out.index).fillna(0).astype("int64")
    if point_size and point_size > 0:
        out["spread"] = (
            (spread_price.reindex(out.index) / point_size).round().fillna(0).astype("int64")
        )
    else:
        out["spread"] = 0
    out["real_volume"] = 0
    out["symbol"] = symbol
    out["timeframe"] = timeframe
    out["source"] = source

    out = out.reset_index().rename(columns={"time": "timestamp"})
    return out[list(REQUIRED_COLUMNS)]


def symbol_tick_files(tick_dir: str | Path, symbol: str) -> list[Path]:
    """Sorted per-day tick parquet files for ``symbol`` in ``tick_dir``."""
    return sorted(Path(tick_dir).glob(f"{symbol}_*.parquet"))


def ingest_symbol(
    symbol: str,
    timeframes: Iterable[str],
    *,
    tick_dir: str | Path,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict[str, tuple[Path, int]]:
    """Resample every tick file for ``symbol`` into candle stores per timeframe.

    Reads only the columns needed (``time``/``bid``/``ask``) so the multi-hundred-MB
    tick files stay manageable. Files are per-day and the standard timeframes all
    divide a day evenly, so per-day resampling concatenates without double-counting.
    Returns ``{timeframe: (written_path, n_bars)}``.
    """
    timeframes = list(timeframes)
    files = symbol_tick_files(tick_dir, symbol)
    if not files:
        raise LocalDataError(f"no tick files for {symbol!r} in {tick_dir}")

    spec = get_symbol_spec(symbol)
    point_size = spec.point_size if spec else 0.0

    per_tf: dict[str, list[pd.DataFrame]] = {tf: [] for tf in timeframes}
    for path in files:
        ticks = pd.read_parquet(path, columns=list(DEFAULT_TICK_COLUMNS))
        for tf in timeframes:
            per_tf[tf].append(
                resample_ticks_to_candles(
                    ticks, symbol=symbol, timeframe=tf, point_size=point_size
                )
            )
        if on_progress is not None:
            on_progress(path.name)

    store = CandleStore(store_dir)
    written: dict[str, tuple[Path, int]] = {}
    for tf, frames in per_tf.items():
        bars = pd.concat(frames, ignore_index=True)
        bars = (
            bars.drop_duplicates(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        written[tf] = (store.save(bars, symbol, tf), len(bars))
    return written


def load_local_candles(
    symbol: str,
    timeframe: str,
    n: int,
    *,
    store_dir: str | Path = DEFAULT_STORE_DIR,
) -> pd.DataFrame:
    """Load the most-recent ``n`` resampled candles for ``symbol``/``timeframe``."""
    store = CandleStore(store_dir)
    if not store.exists(symbol, timeframe):
        raise LocalDataError(
            f"no local candles for {symbol}/{timeframe} under {store_dir}; "
            f"run scripts/ingest_pricer_history.py first"
        )
    df = store.load(symbol, timeframe)
    if n and n > 0:
        df = df.tail(n).reset_index(drop=True)
    return df


__all__ = [
    "DEFAULT_STORE_DIR",
    "LocalDataError",
    "resample_ticks_to_candles",
    "symbol_tick_files",
    "ingest_symbol",
    "load_local_candles",
]
