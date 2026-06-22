"""Feature engineering for trading signals and AI tools.

All features are **causal**: the value at bar ``t`` is computed only from bars
``<= t``. No function peeks at future candles, so there is no look-ahead bias.
Warm-up periods (where a rolling/expanding window is not yet full) are returned
as ``NaN`` rather than being back-filled, so consumers can drop or mask them.

Inputs are plain ``pandas`` Series/DataFrames (typically a candle frame from
``storage.load`` / ``sample_data.generate_candles``). Outputs are Series (or
small DataFrames for multi-line indicators) aligned to the input index.

Conventions:
* EMAs use ``adjust=False`` (recursive, causal) with ``min_periods`` so the
  warm-up region is ``NaN``.
* Rolling stats use ``min_periods == window`` for the same reason.
* "Wilder" smoothing (ATR, RSI) uses ``ewm(alpha=1/period)`` per Welles Wilder.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.data_service.sessions import label_sessions


# --------------------------------------------------------------------------- #
# Returns & volatility
# --------------------------------------------------------------------------- #
def simple_returns(close: pd.Series) -> pd.Series:
    """Simple (arithmetic) return: ``close_t / close_{t-1} - 1``.

    First value is NaN (no prior bar). Causal: uses only the current and the
    immediately preceding close.
    """
    return close.pct_change().rename("simple_return")


def log_returns(close: pd.Series) -> pd.Series:
    """Log return: ``ln(close_t / close_{t-1})``. First value NaN. Causal."""
    return np.log(close / close.shift(1)).rename("log_return")


def rolling_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling std-dev of log returns over ``window`` bars (sample std, ddof=1).

    A scale-free measure of recent variability. NaN until ``window+1`` closes
    are available (one extra for the return itself). Causal.
    """
    lr = log_returns(close)
    return lr.rolling(window, min_periods=window).std(ddof=1).rename("volatility")


# --------------------------------------------------------------------------- #
# ATR (Average True Range, Wilder)
# --------------------------------------------------------------------------- #
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range = max(high-low, |high-prev_close|, |low-prev_close|).

    First bar has no previous close, so it falls back to ``high-low``. Causal.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rename("true_range")


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range via Wilder's smoothing (``alpha = 1/period``).

    NaN for the first ``period`` bars (warm-up). Causal — the recursive EMA
    depends only on past and current true ranges.
    """
    tr = true_range(high, low, close)
    return (
        tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period)
        .mean()
        .rename("atr")
    )


# --------------------------------------------------------------------------- #
# RSI (Wilder)
# --------------------------------------------------------------------------- #
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder), 0–100. NaN warm-up of ``period`` bars.

    Edge cases once warmed up: no losses → 100, no gains → 0, perfectly flat
    (no gains and no losses) → 50 (neutral). Causal.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 (and warmed up): all-gains → 100.
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    # Perfectly flat → neutral 50 (avoids NaN from 0/0).
    out = out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    return out.rename("rsi")


# --------------------------------------------------------------------------- #
# MACD
# --------------------------------------------------------------------------- #
def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram (all causal EMAs).

    * ``macd``        = EMA(fast) - EMA(slow)
    * ``macd_signal`` = EMA(signal) of the MACD line
    * ``macd_hist``   = macd - macd_signal

    EMAs use ``adjust=False`` with ``min_periods`` so warm-up regions are NaN.
    """
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = line - sig
    return pd.DataFrame(
        {"macd": line, "macd_signal": sig, "macd_hist": hist}
    )


# --------------------------------------------------------------------------- #
# Bollinger Bands
# --------------------------------------------------------------------------- #
def bollinger_bands(
    close: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands plus band width.

    * ``bb_mid``   = SMA(window)
    * ``bb_upper`` = mid + num_std * rolling std (ddof=0, population)
    * ``bb_lower`` = mid - num_std * rolling std
    * ``bb_width`` = (upper - lower) / mid  (relative width; NaN if mid == 0)

    NaN until ``window`` closes are available. Causal.
    """
    mid = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width}
    )


def bollinger_band_width(
    close: pd.Series, window: int = 20, num_std: float = 2.0
) -> pd.Series:
    """Convenience accessor for just the Bollinger band width. Causal."""
    return bollinger_bands(close, window=window, num_std=num_std)["bb_width"].rename(
        "bb_width"
    )


# --------------------------------------------------------------------------- #
# Spread / range / trend
# --------------------------------------------------------------------------- #
def spread_percentile(spread: pd.Series, window: int = 100) -> pd.Series:
    """Percentile rank (0–1) of the current spread within the trailing window.

    1.0 means the current spread is the widest in the last ``window`` bars
    (unusually costly to trade); 0.0 means the tightest. NaN until the window
    is full. Causal — the window only contains past and current bars.
    """
    return (
        spread.rolling(window, min_periods=window)
        .rank(pct=True)
        .rename("spread_percentile")
    )


def range_expansion(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    """Current bar range relative to the average range of the trailing window.

    ``(high-low) / mean(high-low over window)``. > 1 = expansion (bigger than
    typical), < 1 = contraction. NaN warm-up of ``window`` bars. Causal.
    """
    rng = (high - low).abs()
    avg = rng.rolling(window, min_periods=window).mean()
    return (rng / avg.replace(0.0, np.nan)).rename("range_expansion")


def trend_score(close: pd.Series, fast: int = 20, slow: int = 50) -> pd.Series:
    """Bounded trend strength in [-1, 1] via ``tanh`` of a scaled EMA spread.

    ``tanh((EMA_fast - EMA_slow) / rolling_std(close, slow))``. Positive → up
    trend, negative → down trend, ~0 → ranging. Scaling by recent std makes it
    comparable across instruments/volatility regimes. NaN warm-up of ``slow``
    bars. Causal (EMAs and rolling std are causal).
    """
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    scale = close.rolling(slow, min_periods=slow).std(ddof=1).replace(0.0, np.nan)
    raw = (ema_fast - ema_slow) / scale
    return np.tanh(raw).rename("trend_score")


def higher_timeframe_trend_placeholder(
    close: pd.Series, value: float = 0.0
) -> pd.Series:
    """Placeholder for a higher-timeframe trend signal (neutral by default).

    Real HTF context requires resampling to a coarser timeframe and carefully
    aligning *closed* HTF bars back to this timeframe (using an unclosed HTF bar
    would leak future information). That alignment is a later milestone; until
    then this returns a constant neutral series so downstream feature frames
    have a stable column. Causal (constant).
    """
    return pd.Series(value, index=close.index, dtype="float64", name="htf_trend")


# --------------------------------------------------------------------------- #
# Assemble a full feature frame (for signals / AI tools)
# --------------------------------------------------------------------------- #
def compute_features(
    df: pd.DataFrame,
    *,
    vol_window: int = 20,
    atr_period: int = 14,
    rsi_period: int = 14,
    bb_window: int = 20,
    spread_window: int = 100,
    range_window: int = 20,
) -> pd.DataFrame:
    """Compute all features for a candle frame, returning one row per candle.

    Requires the columns ``high``, ``low``, ``close``, ``spread`` and
    (for labelling) ``timestamp``. The output is index-aligned to ``df`` and
    includes the timestamp and session label for convenience. Every feature is
    causal, so the frame is safe to use directly for signal generation.
    """
    required = {"high", "low", "close", "spread", "timestamp"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_features missing columns: {sorted(missing)}")

    high, low, close, spread = df["high"], df["low"], df["close"], df["spread"]

    out = pd.DataFrame(index=df.index)
    out["timestamp"] = df["timestamp"].to_numpy()
    out["simple_return"] = simple_returns(close)
    out["log_return"] = log_returns(close)
    out["volatility"] = rolling_volatility(close, window=vol_window)
    out["atr"] = atr(high, low, close, period=atr_period)
    out["rsi"] = rsi(close, period=rsi_period)
    out = out.join(macd(close))
    out = out.join(bollinger_bands(close, window=bb_window))
    out["spread_percentile"] = spread_percentile(spread, window=spread_window)
    out["range_expansion"] = range_expansion(high, low, window=range_window)
    out["trend_score"] = trend_score(close)
    out["htf_trend"] = higher_timeframe_trend_placeholder(close)
    out["session"] = label_sessions(df["timestamp"]).to_numpy()
    return out


__all__ = [
    "simple_returns",
    "log_returns",
    "rolling_volatility",
    "true_range",
    "atr",
    "rsi",
    "macd",
    "bollinger_bands",
    "bollinger_band_width",
    "spread_percentile",
    "range_expansion",
    "trend_score",
    "higher_timeframe_trend_placeholder",
    "compute_features",
]
