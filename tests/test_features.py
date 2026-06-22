"""Tests for feature engineering (services/data_service/features.py).

Two themes:
* correctness / warm-up (NaN) handling and edge cases, and
* **no look-ahead** — proven two ways: (1) prefix-stability (a feature at bar t
  is identical whether computed on the full series or only bars 0..t), and
  (2) future-mutation (changing bars > t never changes the feature at bar t).

Uses fake candle data only — no MT5, no OpenBB, no network.
"""
import numpy as np
import pandas as pd
import pytest

from services.data_service import features as F
from services.data_service.sample_data import generate_candles


@pytest.fixture
def candles():
    return generate_candles("EURUSD", "M15", n=400, seed=11)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_frame(obj) -> pd.DataFrame:
    return obj.to_frame() if isinstance(obj, pd.Series) else obj


def assert_prefix_stable(fn, *series, cut_points=(60, 120, 250)):
    """A causal feature at index k must not change when later bars are absent.

    Compute on the full series and on each truncated prefix [0..k]; the value at
    k must match (NaN-equal) between the two.
    """
    full = _as_frame(fn(*series))
    for k in cut_points:
        truncated = _as_frame(fn(*[s.iloc[: k + 1] for s in series]))
        a = full.iloc[k]
        b = truncated.iloc[k]
        for col in full.columns:
            assert _equal_nan(a[col], b[col]), f"{fn.__name__}[{col}] differs at {k}"


def assert_future_proof(fn, *series, k=200):
    """Mutating bars after k must not change the feature value at/below k."""
    base = _as_frame(fn(*series))
    mutated_inputs = []
    for s in series:
        m = s.copy()
        m.iloc[k + 1 :] = m.iloc[k] * 5.0 + 1.0  # wild future values
        mutated_inputs.append(m)
    after = _as_frame(fn(*mutated_inputs))
    for col in base.columns:
        left = base[col].iloc[: k + 1].to_numpy(dtype="float64")
        right = after[col].iloc[: k + 1].to_numpy(dtype="float64")
        assert np.allclose(left, right, equal_nan=True), f"{fn.__name__}[{col}] leaked future"


def _equal_nan(x, y) -> bool:
    if isinstance(x, float) and isinstance(y, float):
        return np.allclose(x, y, equal_nan=True)
    return x == y


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #
def test_simple_returns_values_and_warmup():
    close = pd.Series([100.0, 110.0, 99.0])
    r = F.simple_returns(close)
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.10)
    assert r.iloc[2] == pytest.approx((99.0 - 110.0) / 110.0)


def test_log_returns_values():
    close = pd.Series([100.0, 110.0])
    r = F.log_returns(close)
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(np.log(110.0 / 100.0))


def test_rolling_volatility_warmup(candles):
    vol = F.rolling_volatility(candles["close"], window=20)
    # Needs 20 log returns -> first 20 rows NaN (return[0] is itself NaN).
    assert vol.iloc[:20].isna().all()
    assert not np.isnan(vol.iloc[21])
    assert (vol.dropna() >= 0).all()


# --------------------------------------------------------------------------- #
# ATR / RSI
# --------------------------------------------------------------------------- #
def test_atr_warmup_and_positive(candles):
    a = F.atr(candles["high"], candles["low"], candles["close"], period=14)
    assert a.iloc[:13].isna().all()
    assert (a.dropna() > 0).all()


def test_true_range_first_bar_uses_high_low(candles):
    tr = F.true_range(candles["high"], candles["low"], candles["close"])
    assert tr.iloc[0] == pytest.approx(candles["high"].iloc[0] - candles["low"].iloc[0])


def test_rsi_range_and_warmup(candles):
    r = F.rsi(candles["close"], period=14)
    assert r.iloc[:14].isna().all()
    valid = r.dropna()
    assert ((valid >= 0) & (valid <= 100)).all()


def test_rsi_all_gains_is_100():
    close = pd.Series(np.linspace(1.0, 2.0, 60))  # strictly increasing
    r = F.rsi(close, period=14)
    assert r.dropna().iloc[-1] == pytest.approx(100.0)


def test_rsi_flat_is_neutral_50():
    close = pd.Series([1.2345] * 60)  # perfectly flat
    r = F.rsi(close, period=14)
    assert (r.dropna() == 50.0).all()


# --------------------------------------------------------------------------- #
# MACD / Bollinger
# --------------------------------------------------------------------------- #
def test_macd_hist_is_difference(candles):
    m = F.macd(candles["close"])
    assert set(m.columns) == {"macd", "macd_signal", "macd_hist"}
    valid = m.dropna()
    assert np.allclose(valid["macd_hist"], valid["macd"] - valid["macd_signal"])


def test_bollinger_ordering_and_width(candles):
    b = F.bollinger_bands(candles["close"], window=20)
    valid = b.dropna()
    assert (valid["bb_upper"] >= valid["bb_mid"]).all()
    assert (valid["bb_mid"] >= valid["bb_lower"]).all()
    assert (valid["bb_width"] >= 0).all()
    assert b.iloc[:19].isna().all().all()


def test_bollinger_band_width_matches():
    close = generate_candles("EURUSD", "M15", n=100)["close"]
    w1 = F.bollinger_band_width(close, window=20)
    w2 = F.bollinger_bands(close, window=20)["bb_width"]
    assert np.allclose(w1.dropna(), w2.dropna())


# --------------------------------------------------------------------------- #
# Spread / range / trend
# --------------------------------------------------------------------------- #
def test_spread_percentile_bounds(candles):
    sp = F.spread_percentile(candles["spread"], window=50)
    assert sp.iloc[:49].isna().all()
    valid = sp.dropna()
    assert ((valid >= 0) & (valid <= 1)).all()


def test_spread_percentile_max_is_one():
    # Last value is the largest in its window -> percentile 1.0.
    spread = pd.Series([1, 2, 3, 4, 100], dtype="float64")
    sp = F.spread_percentile(spread, window=5)
    assert sp.iloc[-1] == pytest.approx(1.0)


def test_range_expansion_positive(candles):
    re = F.range_expansion(candles["high"], candles["low"], window=20)
    assert re.iloc[:19].isna().all()
    assert (re.dropna() > 0).all()


def test_trend_score_bounded(candles):
    ts = F.trend_score(candles["close"], fast=20, slow=50)
    valid = ts.dropna()
    assert ((valid >= -1) & (valid <= 1)).all()


def test_htf_placeholder_is_neutral_constant(candles):
    htf = F.higher_timeframe_trend_placeholder(candles["close"])
    assert (htf == 0.0).all()
    assert len(htf) == len(candles)


# --------------------------------------------------------------------------- #
# No look-ahead — prefix stability
# --------------------------------------------------------------------------- #
def test_prefix_stability_close_features(candles):
    close = candles["close"]
    assert_prefix_stable(F.simple_returns, close)
    assert_prefix_stable(F.log_returns, close)
    assert_prefix_stable(lambda c: F.rolling_volatility(c, 20), close)
    assert_prefix_stable(lambda c: F.rsi(c, 14), close)
    assert_prefix_stable(F.macd, close)
    assert_prefix_stable(lambda c: F.bollinger_bands(c, 20), close)
    assert_prefix_stable(F.trend_score, close)


def test_prefix_stability_hlc_features(candles):
    high, low, close = candles["high"], candles["low"], candles["close"]
    assert_prefix_stable(lambda h, l, c: F.atr(h, l, c, 14), high, low, close)
    assert_prefix_stable(lambda h, l: F.range_expansion(h, l, 20), high, low)


# --------------------------------------------------------------------------- #
# No look-ahead — future mutation cannot change the past
# --------------------------------------------------------------------------- #
def test_future_mutation_does_not_change_past(candles):
    close = candles["close"]
    assert_future_proof(F.simple_returns, close)
    assert_future_proof(F.log_returns, close)
    assert_future_proof(lambda c: F.rolling_volatility(c, 20), close)
    assert_future_proof(lambda c: F.rsi(c, 14), close)
    assert_future_proof(F.macd, close)
    assert_future_proof(lambda c: F.bollinger_bands(c, 20), close)
    assert_future_proof(F.trend_score, close)
    assert_future_proof(lambda c: F.spread_percentile(c, 50), candles["spread"])


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_short_series_all_nan_no_error():
    close = pd.Series([1.0, 1.01, 0.99, 1.02])  # shorter than any window
    assert F.rolling_volatility(close, 20).isna().all()
    assert F.rsi(close, 14).isna().all()
    assert F.bollinger_bands(close, 20).isna().all().all()
    assert F.trend_score(close, 20, 50).isna().all()


def test_constant_prices_returns_zero():
    close = pd.Series([1.5] * 50)
    assert (F.simple_returns(close).dropna() == 0).all()
    assert (F.log_returns(close).dropna() == 0).all()
    assert (F.rolling_volatility(close, 10).dropna() == 0).all()


# --------------------------------------------------------------------------- #
# Full feature frame
# --------------------------------------------------------------------------- #
def test_compute_features_frame(candles):
    feats = F.compute_features(candles)
    assert len(feats) == len(candles)
    for col in [
        "simple_return", "log_return", "volatility", "atr", "rsi",
        "macd", "macd_signal", "macd_hist", "bb_mid", "bb_upper",
        "bb_lower", "bb_width", "spread_percentile", "range_expansion",
        "trend_score", "htf_trend", "session", "timestamp",
    ]:
        assert col in feats.columns
    # Index-aligned to the input.
    assert feats.index.equals(candles.index)


def test_compute_features_requires_columns(candles):
    with pytest.raises(ValueError):
        F.compute_features(candles.drop(columns=["spread"]))
