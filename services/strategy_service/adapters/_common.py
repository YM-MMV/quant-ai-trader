"""Shared helpers for the technical-indicator strategy adapters (M8).

These keep each ported adapter small, consistent, and *cost-aware*. None of the
helpers look past the final candle — they only read history up to and including
the current bar, preserving the framework's no-look-ahead guarantee.

Responsibilities:

* **pip / point sizing** — translate broker "points" (e.g. the ``spread``
  column) into price terms for a given symbol.
* **spread + slippage awareness** — a conservative round-trip trading-cost
  estimate, plus human-readable risk notes, so adapters can refuse edges that
  do not clear their costs (see :func:`clears_cost`).
* **ATR-based stop-loss / take-profit suggestions** — uniform, volatility-scaled
  hints (the RiskManager still re-validates them downstream).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from services.data_service.features import atr as _atr_series
from services.strategy_service.base import SignalSide

# Provenance shared by every ported adapter.
REPO_URL = "https://github.com/je-suis-tm/quant-trading"

# Forex, Gold (metal) and Crypto — the asset classes M8 targets.
ASSET_CLASSES = ["forex", "metal", "crypto"]

# Conservative slippage allowance (in broker points) layered on top of the
# quoted spread to approximate true round-trip cost.
SLIPPAGE_POINTS = 5.0


# --------------------------------------------------------------------------- #
# Pip / point sizing
# --------------------------------------------------------------------------- #
def pip_size(symbol: str) -> float:
    """Price value of one pip for ``symbol`` (simplified convention).

    JPY pairs and metals (XAU/XAG) quote to 2 dp → pip 0.01; everything else
    (major FX, most crypto pairs) → 0.0001. This mirrors the convention used by
    the sample-data generator.
    """
    s = (symbol or "").upper()
    if s.endswith("JPY") or s.startswith("XAU") or s.startswith("XAG"):
        return 0.01
    return 0.0001


def point_size(symbol: str) -> float:
    """Price value of one broker *point* (1/10 of a pip on 3/5-digit feeds)."""
    return pip_size(symbol) / 10.0


def last_symbol(candles: pd.DataFrame) -> str:
    """Best-effort symbol of the most recent candle (``""`` if absent)."""
    if "symbol" in getattr(candles, "columns", []):
        return str(candles["symbol"].iloc[-1])
    return ""


# --------------------------------------------------------------------------- #
# Spread / slippage awareness
# --------------------------------------------------------------------------- #
def latest_spread_price(candles: pd.DataFrame) -> Optional[float]:
    """Most recent quoted spread in price terms, or ``None`` if unavailable."""
    if "spread" not in getattr(candles, "columns", []):
        return None
    try:
        points = float(candles["spread"].iloc[-1])
    except (TypeError, ValueError):
        return None
    if points <= 0:
        return None
    return points * point_size(last_symbol(candles))


def trading_cost_price(candles: pd.DataFrame) -> Optional[float]:
    """One-way trading cost ≈ spread + slippage allowance, in price terms.

    Returns ``None`` when the spread is unknown so callers can decide whether to
    proceed conservatively or abstain.
    """
    spread = latest_spread_price(candles)
    if spread is None:
        return None
    return spread + SLIPPAGE_POINTS * point_size(last_symbol(candles))


def clears_cost(edge: float, candles: pd.DataFrame, *, multiple: float = 1.0) -> bool:
    """True if a price ``edge`` comfortably exceeds round-trip trading costs.

    Used by breakout adapters to avoid firing on moves too small to pay for the
    spread + slippage. If costs are unknown, returns ``True`` (the SL/TP buffer
    is relied on instead) — this never *fabricates* a blocking cost.
    """
    one_way = trading_cost_price(candles)
    if one_way is None:
        return True
    return abs(edge) >= multiple * 2.0 * one_way


def cost_notes(candles: pd.DataFrame) -> list[str]:
    """Risk note(s) quantifying the spread + slippage the trade must overcome."""
    one_way = trading_cost_price(candles)
    if one_way is None:
        return ["spread unavailable; rely on SL/TP buffer to cover trading costs"]
    return [
        f"round-trip spread+slippage ≈ {2.0 * one_way:.5f} price; "
        "edge must exceed this to be profitable"
    ]


# --------------------------------------------------------------------------- #
# ATR-based stop-loss / take-profit
# --------------------------------------------------------------------------- #
def atr_value(candles: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Latest ATR value (Wilder) from the candle frame, or ``None`` if NaN."""
    try:
        series = _atr_series(
            candles["high"], candles["low"], candles["close"], period=period
        )
    except Exception:  # noqa: BLE001 — fail soft; caller falls back
        return None
    value = series.iloc[-1]
    if value != value:  # NaN
        return None
    return float(value)


def sl_tp_from_atr(
    side: SignalSide,
    entry: float,
    atr: Optional[float],
    *,
    sl_mult: float = 1.5,
    tp_mult: float = 2.5,
) -> tuple[float, float]:
    """Volatility-scaled SL/TP hints placed on the correct side of ``entry``.

    Falls back to a small fraction of price when ATR is unavailable, and clamps
    both levels strictly positive (required by :class:`AdapterSignal`).
    """
    if atr is None or atr <= 0:
        atr = abs(entry) * 0.002 or 1e-6
    floor = max(abs(entry) * 1e-4, 1e-6)

    if side is SignalSide.BUY:
        sl = entry - sl_mult * atr
        tp = entry + tp_mult * atr
    else:
        sl = entry + sl_mult * atr
        tp = entry - tp_mult * atr

    return round(max(sl, floor), 6), round(max(tp, floor), 6)


def positive(value: float, *, like: float = 1.0) -> float:
    """Clamp a price level strictly positive (for AdapterSignal's ``gt=0``)."""
    floor = max(abs(like) * 1e-4, 1e-6)
    return round(max(value, floor), 6)


def clamp_confidence(value: float, *, lo: float = 0.1, hi: float = 0.85) -> float:
    """Squash a raw strength score into a modest confidence band."""
    return max(lo, min(hi, float(value)))


def median_price(candles: pd.DataFrame) -> pd.Series:
    """Per-bar median price ``(high + low) / 2`` as a float Series."""
    return (candles["high"].astype(float) + candles["low"].astype(float)) / 2.0


def _is_num(*values: object) -> bool:
    """True only if every value is a real (non-NaN) number."""
    for v in values:
        if v is None:
            return False
        try:
            f = float(v)
        except (TypeError, ValueError):
            return False
        if f != f:  # NaN
            return False
    return True


__all__ = [
    "REPO_URL",
    "ASSET_CLASSES",
    "SLIPPAGE_POINTS",
    "pip_size",
    "point_size",
    "last_symbol",
    "latest_spread_price",
    "trading_cost_price",
    "clears_cost",
    "cost_notes",
    "atr_value",
    "sl_tp_from_atr",
    "positive",
    "clamp_confidence",
    "median_price",
]
