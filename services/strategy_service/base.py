"""Base interfaces for the strategy adapter framework.

A *strategy adapter* wraps the deterministic decision logic of one quant-trading
strategy behind a single, uniform interface so the rest of the system can treat
every strategy identically. Adapters are intentionally narrow and safe:

* **No direct trading.** An adapter only ever *proposes* an :class:`AdapterSignal`;
  it never places orders. Execution is owned by the RiskManager + execution
  service (later milestones).
* **No MT5 / broker calls.** Adapters compute on in-memory candles/features only.
* **No AI calls.** Strategy logic is deterministic — same inputs, same output.
* **No look-ahead.** Adapters receive candles/features up to and including the
  current bar and must use only those (features from M5 are already causal).
* **Fail safe.** If inputs are insufficient — or the adapter raises — the
  framework returns a ``NONE`` signal rather than guessing. This is enforced
  centrally in :meth:`StrategyAdapter.generate_signal`, so individual adapters
  cannot accidentally emit a signal on bad data.

Concrete adapters implement just two methods: :meth:`StrategyAdapter.get_metadata`
and :meth:`StrategyAdapter._compute_signal`. The public ``generate_signal``
template handles validation and the fail-safe wrapper for them.
"""
from __future__ import annotations

import abc
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.models import SignalAction
from services.models import StrategySignal as ModelStrategySignal

# Candle columns every adapter can rely on (subset of the storage schema).
REQUIRED_CANDLE_COLUMNS = ("open", "high", "low", "close")


class SignalSide(str, Enum):
    """Direction of an adapter's proposal."""

    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"  # abstain — no actionable view


class AdapterMetadata(BaseModel):
    """Static description of an adapter and what it can run on.

    ``supported_symbols`` / ``supported_timeframes`` of ``None`` mean "any";
    a list restricts the adapter to those values. ``min_candles`` is the minimum
    history the adapter needs before it can produce an actionable signal.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    source_repo_url: str = ""
    source_strategy: str = ""  # canonical strategy name (defaults to ``name``)
    description: str = ""
    category: str = ""
    supported_symbols: Optional[list[str]] = None
    supported_timeframes: Optional[list[str]] = None
    min_candles: int = Field(1, ge=1)
    asset_classes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_source_strategy(self) -> "AdapterMetadata":
        if not self.source_strategy:
            object.__setattr__(self, "source_strategy", self.name)
        return self


class AdapterSignal(BaseModel):
    """The single output type of every adapter.

    Carries everything a downstream consumer (RiskManager, logging, UI) needs to
    understand and audit the proposal, including provenance (which strategy /
    repo / adapter version produced it). The suggested SL/TP are *hints* — the
    RiskManager re-validates and may override them; an adapter never bypasses it.

    Invariants (enforced below):
    * a ``NONE`` signal has confidence 0 and no SL/TP;
    * an actionable (``BUY``/``SELL``) signal must carry a non-empty ``reason``.
    """

    model_config = ConfigDict(extra="forbid")

    side: SignalSide
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""
    suggested_stop_loss: Optional[float] = Field(None, gt=0)
    suggested_take_profit: Optional[float] = Field(None, gt=0)
    risk_notes: list[str] = Field(default_factory=list)

    # Provenance
    source_strategy: str = Field(..., min_length=1)
    source_repo_url: str = ""
    adapter_version: str = Field(..., min_length=1)

    # Optional context
    symbol: Optional[str] = None
    timeframe: Optional[str] = None

    @property
    def is_actionable(self) -> bool:
        return self.side in (SignalSide.BUY, SignalSide.SELL)

    @model_validator(mode="after")
    def _enforce_invariants(self) -> "AdapterSignal":
        if self.side is SignalSide.NONE:
            # Coerce a NONE into a clean abstention.
            self.confidence = 0.0
            self.suggested_stop_loss = None
            self.suggested_take_profit = None
        elif not self.reason.strip():
            raise ValueError("an actionable signal must include a non-empty reason")
        return self

    def to_model_signal(
        self,
        *,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        strategy_id: str,
    ) -> ModelStrategySignal:
        """Map to the system-wide :class:`services.models.StrategySignal`.

        ``NONE`` maps to the model's ``HOLD`` action. This bridges the strategy
        layer to the shared model used by risk/logging downstream.
        """
        action = {
            SignalSide.BUY: SignalAction.BUY,
            SignalSide.SELL: SignalAction.SELL,
            SignalSide.NONE: SignalAction.HOLD,
        }[self.side]
        return ModelStrategySignal(
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            action=action,
            confidence=self.confidence,
            suggested_stop_loss=self.suggested_stop_loss,
            suggested_take_profit=self.suggested_take_profit,
            rationale=self.reason,
        )


class StrategyAdapter(abc.ABC):
    """Abstract base every concrete strategy adapter inherits from.

    Subclasses implement :meth:`get_metadata` and :meth:`_compute_signal`. The
    public :meth:`generate_signal` is a template that validates inputs and wraps
    the computation in a fail-safe (any problem → ``NONE``), so adapters can
    focus purely on deterministic strategy logic.
    """

    # -- subclass responsibilities ----------------------------------------- #
    @abc.abstractmethod
    def get_metadata(self) -> AdapterMetadata:
        """Return this adapter's static metadata."""

    @abc.abstractmethod
    def _compute_signal(
        self,
        candles: Any,
        features: Any,
        kronos_prediction: Optional[Any],
    ) -> AdapterSignal:
        """Deterministic strategy logic. Called only after inputs validate.

        Must return an :class:`AdapterSignal`. Use :meth:`make_signal` /
        :meth:`none_signal` so provenance fields are filled consistently.
        """

    # -- convenience accessors --------------------------------------------- #
    @property
    def name(self) -> str:
        return self.get_metadata().name

    @property
    def version(self) -> str:
        return self.get_metadata().version

    def supports_symbol(self, symbol: str) -> bool:
        symbols = self.get_metadata().supported_symbols
        return True if symbols is None else symbol in symbols

    def supports_timeframe(self, timeframe: str) -> bool:
        timeframes = self.get_metadata().supported_timeframes
        return True if timeframes is None else timeframe in timeframes

    # -- input validation -------------------------------------------------- #
    def validate_inputs(self, candles: Any, features: Any = None) -> Optional[str]:
        """Return ``None`` if inputs are usable, else a human-readable reason.

        Checks presence/shape/length of candles, required OHLC columns, and (when
        the candle frame carries them) symbol/timeframe support. Never raises.
        """
        meta = self.get_metadata()

        if candles is None:
            return "candles is None"
        try:
            n = len(candles)
        except TypeError:
            return "candles is not a sequence/DataFrame"
        if n == 0:
            return "candles is empty"

        columns = set(getattr(candles, "columns", []))
        if columns:  # DataFrame-like: verify required columns exist
            missing = [c for c in REQUIRED_CANDLE_COLUMNS if c not in columns]
            if missing:
                return f"missing candle columns: {missing}"

        if n < meta.min_candles:
            return f"insufficient candles: have {n}, need {meta.min_candles}"

        if "symbol" in columns:
            symbol = candles["symbol"].iloc[-1]
            if not self.supports_symbol(symbol):
                return f"symbol {symbol!r} not supported by {meta.name}"
        if "timeframe" in columns:
            timeframe = candles["timeframe"].iloc[-1]
            if not self.supports_timeframe(timeframe):
                return f"timeframe {timeframe!r} not supported by {meta.name}"

        if features is not None:
            try:
                fn = len(features)
            except TypeError:
                return "features is not a sequence/DataFrame"
            if fn != n:
                return f"features length {fn} != candles length {n}"

        return None

    # -- public template --------------------------------------------------- #
    def generate_signal(
        self,
        candles: Any,
        features: Any = None,
        kronos_prediction: Optional[Any] = None,
    ) -> AdapterSignal:
        """Validate inputs, run the strategy, and guarantee a safe result.

        Returns a ``NONE`` signal if inputs are insufficient or the adapter
        raises — an adapter can never crash the pipeline or emit a signal on bad
        data.
        """
        problem = self.validate_inputs(candles, features)
        if problem is not None:
            return self.none_signal(f"insufficient inputs: {problem}")

        try:
            signal = self._compute_signal(candles, features, kronos_prediction)
        except Exception as exc:  # noqa: BLE001 — fail safe by design
            return self.none_signal(f"adapter error: {type(exc).__name__}: {exc}")

        if not isinstance(signal, AdapterSignal):
            return self.none_signal("adapter returned a non-signal; abstaining")
        return signal

    # -- signal builders --------------------------------------------------- #
    def none_signal(
        self, reason: str = "", risk_notes: Optional[Sequence[str]] = None
    ) -> AdapterSignal:
        """Build a safe ``NONE`` (abstain) signal with provenance filled in."""
        meta = self.get_metadata()
        return AdapterSignal(
            side=SignalSide.NONE,
            reason=reason,
            risk_notes=list(risk_notes or []),
            source_strategy=meta.source_strategy,
            source_repo_url=meta.source_repo_url,
            adapter_version=meta.version,
        )

    def make_signal(
        self,
        side: SignalSide,
        confidence: float,
        reason: str,
        *,
        suggested_stop_loss: Optional[float] = None,
        suggested_take_profit: Optional[float] = None,
        risk_notes: Optional[Sequence[str]] = None,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> AdapterSignal:
        """Build a signal with provenance (strategy/repo/version) filled in."""
        meta = self.get_metadata()
        return AdapterSignal(
            side=side,
            confidence=confidence,
            reason=reason,
            suggested_stop_loss=suggested_stop_loss,
            suggested_take_profit=suggested_take_profit,
            risk_notes=list(risk_notes or []),
            symbol=symbol,
            timeframe=timeframe,
            source_strategy=meta.source_strategy,
            source_repo_url=meta.source_repo_url,
            adapter_version=meta.version,
        )


__all__ = [
    "SignalSide",
    "AdapterMetadata",
    "AdapterSignal",
    "StrategyAdapter",
    "REQUIRED_CANDLE_COLUMNS",
]
