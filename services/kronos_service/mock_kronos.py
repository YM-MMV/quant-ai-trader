"""Deterministic mock Kronos predictor (M15).

A stand-in for the real Kronos candlestick model so the rest of the system can
build against the :class:`~services.kronos_service.base.KronosPredictor`
interface before the real model is integrated. It is **pure arithmetic over the
input closes** — no randomness, no network, no real Kronos dependency — so the
same candles always yield the same prediction (essential for tests).

How the forecast is derived from the recent ``lookback`` window:

* per-bar returns over the window give a mean drift and a volatility (population
  std of returns);
* ``predicted_return`` compounds the mean drift over ``pred_len`` bars (clamped
  to a sane range so prices stay positive);
* ``predicted_close`` applies that return to the last close; ``predicted_high`` /
  ``predicted_low`` band it by the predicted volatility;
* ``confidence_proxy`` is a signal-to-noise ratio (|drift| vs volatility) mapped
  smoothly into ``[0, 1)`` — strong, low-noise drift ⇒ higher confidence.

The forecast is advisory only and clearly flagged ``is_mock=True``.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from services.kronos_service.base import (
    DEFAULT_LOOKBACK,
    DEFAULT_PRED_LEN,
    KronosPrediction,
    KronosPredictor,
)

# Keep predicted prices positive and sane regardless of input noise.
_MAX_ABS_RETURN = 0.5


def _extract_closes(candles: Any) -> list[float]:
    """Pull a list of close prices from a DataFrame, Series, or sequence."""
    if candles is None:
        return []
    columns = getattr(candles, "columns", None)
    if columns is not None:  # DataFrame-like
        if "close" not in columns:
            return []
        return [float(x) for x in candles["close"].tolist()]
    if hasattr(candles, "tolist"):  # Series / ndarray
        return [float(x) for x in candles.tolist()]
    try:
        return [float(x) for x in candles]
    except (TypeError, ValueError):
        return []


def _infer_column(candles: Any, key: str) -> Optional[str]:
    columns = getattr(candles, "columns", None)
    if columns is not None and key in columns and len(candles):
        return str(candles[key].iloc[-1])
    return None


class MockKronos(KronosPredictor):
    """Deterministic Kronos stand-in (see module docstring)."""

    MODEL_NAME = "kronos-mock"

    def __init__(self, *, max_band: float = 0.95) -> None:
        # Cap the high/low band so predicted_low can never go non-positive.
        self.max_band = max(0.0, min(max_band, 0.99))

    def predict(
        self,
        candles: Any,
        *,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        lookback: int = DEFAULT_LOOKBACK,
        pred_len: int = DEFAULT_PRED_LEN,
    ) -> KronosPrediction:
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        if pred_len <= 0:
            raise ValueError("pred_len must be positive")

        closes = _extract_closes(candles)
        if not closes:
            raise ValueError("MockKronos.predict needs at least one close price")

        symbol = symbol or _infer_column(candles, "symbol") or "UNKNOWN"
        timeframe = timeframe or _infer_column(candles, "timeframe") or "UNKNOWN"

        window = closes[-lookback:]
        used = len(window)
        last_close = window[-1]

        returns = [
            (window[i] - window[i - 1]) / window[i - 1]
            for i in range(1, used)
            if window[i - 1] != 0
        ]
        if returns:
            mean_r = sum(returns) / len(returns)
            var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            vol_bar = math.sqrt(var)
        else:
            mean_r = 0.0
            vol_bar = 0.0

        predicted_return = (1.0 + mean_r) ** pred_len - 1.0
        predicted_return = max(-_MAX_ABS_RETURN, min(_MAX_ABS_RETURN, predicted_return))
        predicted_vol = vol_bar * math.sqrt(pred_len)

        predicted_close = last_close * (1.0 + predicted_return)
        band = min(predicted_vol, self.max_band)
        predicted_high = predicted_close * (1.0 + band)
        predicted_low = predicted_close * (1.0 - band)

        snr = abs(mean_r) / (vol_bar + 1e-12) if returns else 0.0
        confidence = snr / (1.0 + snr)  # smooth map into [0, 1)

        return KronosPrediction(
            symbol=symbol,
            timeframe=timeframe,
            lookback=used,
            pred_len=pred_len,
            predicted_return=round(predicted_return, 8),
            predicted_high=round(predicted_high, 6),
            predicted_low=round(predicted_low, 6),
            predicted_close=round(predicted_close, 6),
            predicted_volatility=round(predicted_vol, 8),
            confidence_proxy=round(confidence, 6),
            model_name=self.MODEL_NAME,
            is_mock=True,
        )


__all__ = ["MockKronos"]
