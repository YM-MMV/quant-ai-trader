"""Kronos prediction service (M9/M15): candlestick model, mocked first.

M15 adds the :class:`~services.kronos_service.base.KronosPredictor` interface and
its validated :class:`~services.kronos_service.base.KronosPrediction` output,
plus a deterministic :class:`~services.kronos_service.mock_kronos.MockKronos`
stand-in used until the real model is integrated.
"""
from services.kronos_service.base import (
    DEFAULT_LOOKBACK,
    DEFAULT_PRED_LEN,
    KronosPrediction,
    KronosPredictor,
    PredDirection,
)
from services.kronos_service.mock_kronos import MockKronos
from services.kronos_service.real_kronos import (
    DisabledKronos,
    KronosDisabledError,
    KronosUnavailableError,
    RealKronos,
    kronos_available,
    load_kronos,
    load_predictions,
    save_prediction,
)

__all__ = [
    "KronosPrediction",
    "KronosPredictor",
    "PredDirection",
    "MockKronos",
    "RealKronos",
    "DisabledKronos",
    "load_kronos",
    "kronos_available",
    "save_prediction",
    "load_predictions",
    "KronosUnavailableError",
    "KronosDisabledError",
    "DEFAULT_LOOKBACK",
    "DEFAULT_PRED_LEN",
]
