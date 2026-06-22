"""Kronos prediction interface (M15).

Kronos is a candlestick foundation model that, given a lookback window of
recent bars, forecasts the next ``pred_len`` bars. Real integration comes later;
this module defines the *contract* both the mock (M15) and the eventual real
client implement, plus the prediction object they return.

A :class:`KronosPrediction` is a small, validated forecast container carrying
everything a strategy needs to use Kronos as an **optional filter**: the
expected return / high / low / close, an expected volatility, and a
``confidence_proxy``. It is advisory only — like a strategy signal, it never
bypasses the RiskManager. The ``is_mock`` flag makes it unmistakable in logs
when a forecast came from the deterministic stub rather than the real model.

No real Kronos dependency, no MT5, no network — the interface is pure Python and
the concrete mock (see :mod:`services.kronos_service.mock_kronos`) is
deterministic.
"""
from __future__ import annotations

import abc
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_LOOKBACK = 64
DEFAULT_PRED_LEN = 1


class PredDirection(str, Enum):
    """Sign of the predicted return."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class KronosPrediction(BaseModel):
    """A validated Kronos forecast for one symbol/timeframe (the M15 schema).

    ``predicted_return`` is the fractional move over the forecast horizon
    (``0.01`` = +1%). The price fields are absolute levels for the end of the
    horizon; ``predicted_volatility`` is a non-negative fractional spread and
    ``confidence_proxy`` is a 0–1 self-assessment (not a calibrated probability —
    hence "proxy"). Invariants: ``low <= close <= high`` and all prices > 0.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    lookback: int = Field(..., gt=0, description="bars of history fed to the model")
    pred_len: int = Field(..., gt=0, description="bars ahead forecast")
    predicted_return: float = Field(..., description="fractional return over horizon")
    predicted_high: float = Field(..., gt=0)
    predicted_low: float = Field(..., gt=0)
    predicted_close: float = Field(..., gt=0)
    predicted_volatility: float = Field(..., ge=0.0)
    confidence_proxy: float = Field(..., ge=0.0, le=1.0)
    model_name: str = "kronos-mock"
    is_mock: bool = True

    @model_validator(mode="after")
    def _check_price_bounds(self) -> "KronosPrediction":
        if self.predicted_high < self.predicted_low:
            raise ValueError("predicted_high must be >= predicted_low")
        if not (self.predicted_low <= self.predicted_close <= self.predicted_high):
            raise ValueError("predicted_close must lie within [predicted_low, predicted_high]")
        return self

    # -- filter helpers (so strategies can use Kronos as a gate) ----------- #
    @property
    def direction(self) -> PredDirection:
        if self.predicted_return > 0:
            return PredDirection.UP
        if self.predicted_return < 0:
            return PredDirection.DOWN
        return PredDirection.FLAT

    @property
    def is_bullish(self) -> bool:
        return self.direction is PredDirection.UP

    @property
    def is_bearish(self) -> bool:
        return self.direction is PredDirection.DOWN

    def is_confident(self, threshold: float = 0.5) -> bool:
        """True if the confidence proxy meets ``threshold``."""
        return self.confidence_proxy >= threshold

    def agrees_with(self, side: str) -> bool:
        """Does the forecast direction agree with a proposed ``BUY``/``SELL``?

        A ``FLAT`` forecast agrees with neither. Case-insensitive; accepts the
        adapter ``SignalSide`` values or plain strings.
        """
        s = str(getattr(side, "value", side)).upper()
        if s == "BUY":
            return self.is_bullish
        if s == "SELL":
            return self.is_bearish
        return False


class KronosPredictor(abc.ABC):
    """Abstract Kronos client. The mock and the real model both implement this."""

    @abc.abstractmethod
    def predict(
        self,
        candles: Any,
        *,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        lookback: int = DEFAULT_LOOKBACK,
        pred_len: int = DEFAULT_PRED_LEN,
    ) -> KronosPrediction:
        """Forecast the next ``pred_len`` bars from the recent ``lookback`` window.

        ``candles`` may be a DataFrame (canonical schema) or a sequence of close
        prices. ``symbol``/``timeframe`` are inferred from a DataFrame when not
        given. Implementations must always return a valid
        :class:`KronosPrediction` (never ``None``).
        """


__all__ = [
    "KronosPrediction",
    "KronosPredictor",
    "PredDirection",
    "DEFAULT_LOOKBACK",
    "DEFAULT_PRED_LEN",
]
