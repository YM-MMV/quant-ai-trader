"""Shared helpers for research adapters (deterministic, offline)."""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

# Asset classes our broker universe covers.
ASSET_CLASSES = ["forex", "metal", "crypto"]


def pick(inputs: dict, *keys: str) -> object:
    """First non-None value among ``keys`` (avoids ``df or x`` truthiness traps)."""
    for k in keys:
        v = inputs.get(k)
        if v is not None:
            return v
    return None


def to_close_series(data: object) -> Optional[pd.Series]:
    """Coerce candles / a price sequence into a float close Series, else None."""
    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        if "close" not in data.columns:
            return None
        return data["close"].astype(float).reset_index(drop=True)
    if isinstance(data, pd.Series):
        return data.astype(float).reset_index(drop=True)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        if len(data) == 0:
            return None
        return pd.Series([float(x) for x in data])
    return None


def log_returns(close: pd.Series) -> pd.Series:
    """Log returns with the leading NaN dropped."""
    return np.log(close / close.shift(1)).dropna()


def annualize_vol(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualised standard deviation of (per-bar) returns."""
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def ols_beta(y: pd.Series, x: pd.Series) -> tuple[float, float]:
    """Simple OLS slope/intercept of ``y`` on ``x`` (least squares)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xm, ym = x.mean(), y.mean()
    denom = float(((x - xm) ** 2).sum())
    beta = float(((x - xm) * (y - ym)).sum() / denom) if denom else 0.0
    alpha = float(ym - beta * xm)
    return beta, alpha


def align(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Trim two series to a common (tail-aligned) length."""
    n = min(len(a), len(b))
    return (
        a.iloc[-n:].reset_index(drop=True),
        b.iloc[-n:].reset_index(drop=True),
    )


__all__ = [
    "ASSET_CLASSES",
    "pick",
    "to_close_series",
    "log_returns",
    "annualize_vol",
    "ols_beta",
    "align",
]
