"""Real Kronos integration (M16) — optional, behind the M15 interface.

This wraps the pretrained `Kronos <https://github.com/shiyu-coder/Kronos>`_
candlestick foundation model behind the same
:class:`~services.kronos_service.base.KronosPredictor` interface the mock
implements, so the rest of the system never knows (or cares) which is running.

Design rules (from the milestone):

* **Optional.** The ``Kronos`` package + its heavy deps (torch, the HF weights)
  are imported lazily and guarded. If they are unavailable the system falls back
  to the mock or a disabled predictor — it never hard-crashes on a missing
  optional dependency. Use :func:`load_kronos` to get the right one.
* **Pretrained only, no fine-tuning.** We only ever call ``from_pretrained`` and
  ``predict``.
* **Recent candles only.** The lookback is clamped to the model's
  ``max_context`` (512 for Kronos-small/base) — we never feed years of history.
* **No GPU required.** Defaults to ``device="cpu"`` so it runs (slowly) anywhere;
  tests inject a fake underlying predictor and never load torch.
* Predictions are persisted under ``data/predictions/`` via
  :func:`save_prediction`.

The underlying repo also exposes a class called ``KronosPredictor``; to avoid a
name clash with our interface it is imported as ``_RepoPredictor``.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from services.config_loader import PROJECT_ROOT
from services.data_service.sample_data import timeframe_minutes
from services.kronos_service.base import (
    DEFAULT_LOOKBACK,
    DEFAULT_PRED_LEN,
    KronosPrediction,
    KronosPredictor,
)
from services.kronos_service.mock_kronos import MockKronos

DEFAULT_PREDICTIONS_DIR = PROJECT_ROOT / "data" / "predictions"
DEFAULT_MODEL = "Kronos-small"
DEFAULT_TOKENIZER = "NeoQuasar/Kronos-Tokenizer-base"
DEFAULT_MAX_CONTEXT = 512
# Hugging Face repo prefix for the pretrained weights.
_HF_PREFIX = "NeoQuasar"


class KronosUnavailableError(RuntimeError):
    """The real Kronos package or its dependencies could not be loaded."""


class KronosDisabledError(RuntimeError):
    """Kronos is explicitly disabled but a prediction was requested."""


def kronos_available() -> bool:
    """True if the real Kronos package can be imported (no model is loaded)."""
    try:  # pragma: no cover - depends on host having the optional package
        import importlib.util

        return importlib.util.find_spec("model") is not None and \
            importlib.util.find_spec("torch") is not None
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------------------- #
# Real predictor
# --------------------------------------------------------------------------- #
class RealKronos(KronosPredictor):
    """Pretrained Kronos behind the project's predictor interface.

    The underlying model is loaded lazily on the first :meth:`predict` (or a
    pre-built repo predictor can be injected via ``predictor=`` — used by tests
    so no weights/torch are needed).
    """

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        tokenizer_name: str = DEFAULT_TOKENIZER,
        device: str = "cpu",
        max_context: int = DEFAULT_MAX_CONTEXT,
        temperature: float = 1.0,
        top_p: float = 0.9,
        sample_count: int = 1,
        predictor: Any = None,
    ) -> None:
        self.model_name = model_name
        self.tokenizer_name = tokenizer_name
        self.device = device
        self.max_context = max_context
        self.temperature = temperature
        self.top_p = top_p
        self.sample_count = sample_count
        self._predictor = predictor  # injected or lazily loaded

    # -- model loading ----------------------------------------------------- #
    def _load_predictor(self) -> Any:
        """Import the Kronos package and build the repo predictor (lazy)."""
        if self._predictor is not None:
            return self._predictor
        try:  # pragma: no cover - exercised only where the package is installed
            from model import (  # type: ignore
                Kronos,
                KronosTokenizer,
                KronosPredictor as _RepoPredictor,
            )
        except Exception as exc:  # noqa: BLE001
            raise KronosUnavailableError(
                "the Kronos package is not installed; see docs/KRONOS_SETUP.md "
                "(install it or use mock mode)"
            ) from exc

        tokenizer = KronosTokenizer.from_pretrained(self.tokenizer_name)
        model = Kronos.from_pretrained(f"{_HF_PREFIX}/{self.model_name}")
        self._predictor = _RepoPredictor(
            model, tokenizer, device=self.device, max_context=self.max_context
        )
        return self._predictor

    # -- prediction -------------------------------------------------------- #
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

        columns = getattr(candles, "columns", None)
        if columns is None or not {"open", "high", "low", "close"} <= set(columns):
            raise ValueError(
                "RealKronos.predict needs a DataFrame with open/high/low/close "
                "columns (and a timestamp column)"
            )

        symbol = symbol or (str(candles["symbol"].iloc[-1])
                            if "symbol" in columns else "UNKNOWN")
        timeframe = timeframe or (str(candles["timeframe"].iloc[-1])
                                 if "timeframe" in columns else "UNKNOWN")

        # Recent candles only — never exceed the model's context window.
        effective_lookback = min(int(lookback), self.max_context)
        hist = candles.iloc[-effective_lookback:].reset_index(drop=True)

        x_df = hist[[c for c in ("open", "high", "low", "close", "volume", "amount")
                     if c in columns]].copy()
        x_timestamp, y_timestamp = self._build_timestamps(hist, timeframe, pred_len)

        predictor = self._load_predictor()
        forecast = predictor.predict(
            df=x_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_len,
            T=self.temperature,
            top_p=self.top_p,
            sample_count=self.sample_count,
        )

        last_close = float(hist["close"].iloc[-1])
        return self._to_prediction(
            forecast, last_close=last_close, symbol=symbol, timeframe=timeframe,
            lookback=len(x_df), pred_len=pred_len,
        )

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _build_timestamps(hist: Any, timeframe: str, pred_len: int):
        import pandas as pd

        if "timestamp" in getattr(hist, "columns", []):
            x_ts = pd.to_datetime(hist["timestamp"]).reset_index(drop=True)
        else:
            # Synthesize a regular index if the frame carries no timestamps.
            minutes = timeframe_minutes(timeframe)
            base = pd.Timestamp("2024-01-01")
            x_ts = pd.Series([base + timedelta(minutes=minutes * i)
                              for i in range(len(hist))])
        minutes = timeframe_minutes(timeframe)
        last = pd.Timestamp(x_ts.iloc[-1])
        y_ts = pd.Series([last + timedelta(minutes=minutes * k)
                          for k in range(1, pred_len + 1)])
        return x_ts, y_ts

    def _to_prediction(
        self, forecast: Any, *, last_close: float, symbol: str, timeframe: str,
        lookback: int, pred_len: int,
    ) -> KronosPrediction:
        """Reduce a Kronos forecast DataFrame to a :class:`KronosPrediction`."""
        closes = [float(x) for x in forecast["close"].tolist()]
        highs = [float(x) for x in forecast["high"].tolist()]
        lows = [float(x) for x in forecast["low"].tolist()]
        if not closes:
            raise KronosUnavailableError("Kronos returned an empty forecast")

        predicted_close = closes[-1]
        predicted_high = max(highs) if highs else predicted_close
        predicted_low = min(lows) if lows else predicted_close
        predicted_return = (predicted_close - last_close) / last_close if last_close else 0.0

        # Volatility: std of forecast close-to-close returns; fall back to the
        # single-bar high/low range when there's only one predicted bar.
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(1, len(closes)) if closes[i - 1] != 0]
        if rets:
            mean_r = sum(rets) / len(rets)
            predicted_vol = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
        elif predicted_close:
            predicted_vol = (predicted_high - predicted_low) / predicted_close
        else:
            predicted_vol = 0.0

        snr = abs(predicted_return) / (predicted_vol + 1e-12)
        confidence = snr / (1.0 + snr)

        # Keep the band consistent with the close (the model's high/low can drift).
        predicted_high = max(predicted_high, predicted_close)
        predicted_low = min(predicted_low, predicted_close)

        return KronosPrediction(
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            pred_len=pred_len,
            predicted_return=round(predicted_return, 8),
            predicted_high=round(predicted_high, 6),
            predicted_low=round(predicted_low, 6),
            predicted_close=round(predicted_close, 6),
            predicted_volatility=round(predicted_vol, 8),
            confidence_proxy=round(confidence, 6),
            model_name=self.model_name.lower(),
            is_mock=False,
        )


# --------------------------------------------------------------------------- #
# Disabled predictor + factory
# --------------------------------------------------------------------------- #
class DisabledKronos(KronosPredictor):
    """A predictor that refuses to predict — Kronos explicitly turned off."""

    def predict(self, candles: Any, **kwargs: Any) -> KronosPrediction:
        raise KronosDisabledError(
            "Kronos is disabled (mode='disabled'); enable mock or real mode"
        )


def load_kronos(
    mode: str = "auto",
    **kwargs: Any,
) -> KronosPredictor:
    """Return the right Kronos predictor for ``mode``.

    * ``auto`` — real if the package is importable, else the mock (the safe
      default for the whole system).
    * ``real`` — :class:`RealKronos`; raises :class:`KronosUnavailableError`
      now if the package is missing.
    * ``mock`` — :class:`MockKronos` (deterministic).
    * ``disabled`` — :class:`DisabledKronos` (predicting raises).
    """
    mode = (mode or "auto").lower()
    if mode == "mock":
        return MockKronos()
    if mode == "disabled":
        return DisabledKronos()
    if mode == "real":
        if not kronos_available():
            raise KronosUnavailableError(
                "mode='real' but the Kronos package is not installed; "
                "see docs/KRONOS_SETUP.md"
            )
        return RealKronos(**kwargs)
    if mode == "auto":
        if kronos_available():
            return RealKronos(**kwargs)
        return MockKronos()
    raise ValueError(f"unknown Kronos mode {mode!r}; "
                     "expected auto | real | mock | disabled")


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def save_prediction(
    prediction: KronosPrediction,
    *,
    base_dir: Optional[str | Path] = None,
) -> Path:
    """Append a prediction to ``data/predictions/<symbol>/<timeframe>.jsonl``."""
    base = Path(base_dir) if base_dir is not None else DEFAULT_PREDICTIONS_DIR
    path = base / prediction.symbol / f"{prediction.timeframe}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prediction.model_dump_json() + "\n")
    return path


def load_predictions(
    symbol: str,
    timeframe: str,
    *,
    base_dir: Optional[str | Path] = None,
) -> list[KronosPrediction]:
    """Read back stored predictions for a symbol/timeframe (empty if none)."""
    base = Path(base_dir) if base_dir is not None else DEFAULT_PREDICTIONS_DIR
    path = base / symbol / f"{timeframe}.jsonl"
    if not path.is_file():
        return []
    out: list[KronosPrediction] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(KronosPrediction.model_validate_json(line))
    return out


__all__ = [
    "RealKronos",
    "DisabledKronos",
    "load_kronos",
    "kronos_available",
    "save_prediction",
    "load_predictions",
    "KronosUnavailableError",
    "KronosDisabledError",
    "DEFAULT_PREDICTIONS_DIR",
    "DEFAULT_MODEL",
    "DEFAULT_TOKENIZER",
    "DEFAULT_MAX_CONTEXT",
]
