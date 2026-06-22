"""Base interfaces for *research* adapters.

The M8 :class:`~services.strategy_service.base.StrategyAdapter` ports strategies
that can drive MT5 forex/gold/crypto execution. Many quant-trading projects,
however, are **not** directly tradable on our instruments — pair/stat-arb,
options, VIX, Monte-Carlo simulation, portfolio optimisation and the various
"quantamental" projects. Ignoring them would lose information, so this module
gives them a first-class home as *research adapters*.

A research adapter:

* **never executes** and is structurally prevented from doing so — its output
  carries an :class:`MT5Applicability` and an :class:`OutputType`, and only a
  ``DIRECT`` + ``SIGNAL`` output is ever :pymeth:`ResearchOutput.is_executable`.
  None of the ported research projects are ``DIRECT``, so every one of them is
  blocked from the execution path (enforced in
  :pymeth:`ResearchOutput.to_strategy_signal`, which raises
  :class:`NotExecutableError`).
* is **honest about its data needs** — it declares ``required_datasets`` and,
  when those inputs are absent, returns a *report* explaining what is needed
  rather than fabricating a tradeable signal.
* is deterministic and offline — no MT5, no network, no AI.
"""
from __future__ import annotations

import abc
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.models import SignalAction
from services.models import StrategySignal as ModelStrategySignal
from services.strategy_service.strategy_classifier import MT5Applicability

REPO_URL = "https://github.com/je-suis-tm/quant-trading"


class OutputType(str, Enum):
    """What a research adapter produces (never an order)."""

    SIGNAL = "signal"            # a directional view (only tradable if DIRECT)
    FEATURE = "feature"          # a number/series to feed other models
    REPORT = "report"            # a human-readable analysis
    RANKING = "ranking"          # an ordered list (allocations, consensus, …)
    RISK_CONTEXT = "risk_context"  # risk inputs (VaR, regime, …)


class NotExecutableError(RuntimeError):
    """Raised when something tries to route a non-tradable output to execution."""


class ResearchAdapterMetadata(BaseModel):
    """Static description of a research adapter."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    source_repo_url: str = REPO_URL
    source_strategy: str = Field(..., min_length=1)
    description: str = ""
    category: str = ""
    output_type: OutputType
    applicability: MT5Applicability
    required_datasets: list[str] = Field(default_factory=list)
    supported_asset_classes: list[str] = Field(default_factory=list)
    # Why the project is (or is not) tradable on our MT5 instruments.
    reason: str = Field(..., min_length=1)

    @property
    def tradable_on_mt5(self) -> bool:
        """Only DIRECT projects could ever drive an MT5 order."""
        return self.applicability is MT5Applicability.DIRECT


class ResearchOutput(BaseModel):
    """The single result type a research adapter returns.

    Carries the analysis payload plus the provenance and the applicability /
    output-type metadata that gates execution. ``data`` is an open map so each
    project can return its own shape (metrics, weights, breakevens, …) without a
    bespoke model per project.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    output_type: OutputType
    applicability: MT5Applicability
    summary: str = Field(..., min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    required_datasets: list[str] = Field(default_factory=list)
    reason: str = Field(..., min_length=1)
    risk_notes: list[str] = Field(default_factory=list)
    data_available: bool = True  # False ⇒ this is a "requirements" report

    # Provenance
    source_strategy: str = Field(..., min_length=1)
    source_repo_url: str = REPO_URL
    adapter_version: str = Field(..., min_length=1)

    @property
    def tradable_on_mt5(self) -> bool:
        return self.applicability is MT5Applicability.DIRECT

    def is_executable(self) -> bool:
        """A research output is executable only if it is a DIRECT signal.

        None of the ported research projects are ``DIRECT``, so this is always
        ``False`` for them — the structural guarantee that research never trades.
        """
        return self.tradable_on_mt5 and self.output_type is OutputType.SIGNAL

    def to_strategy_signal(
        self,
        *,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        strategy_id: str,
    ) -> ModelStrategySignal:
        """Bridge to an executable signal — **refused** unless truly tradable.

        Raises :class:`NotExecutableError` for any non-``DIRECT`` / non-signal
        output, i.e. for every research-only project. This is the single gate
        between research and the execution layer.
        """
        if not self.is_executable():
            raise NotExecutableError(
                f"{self.name!r} is {self.applicability.value}/{self.output_type.value};"
                " research outputs cannot be sent to execution"
            )
        side = str(self.data.get("side", "")).lower()
        action = {"buy": SignalAction.BUY, "sell": SignalAction.SELL}.get(
            side, SignalAction.HOLD
        )
        return ModelStrategySignal(
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            action=action,
            confidence=float(self.data.get("confidence", 0.0)),
            rationale=self.summary,
        )


def ensure_not_executed(output: ResearchOutput) -> ResearchOutput:
    """Execution-gate helper: pass through tradable outputs, reject the rest.

    Mirrors what a future execution layer must call. Research-only outputs raise
    :class:`NotExecutableError`, so they can never reach order placement.
    """
    if not output.is_executable():
        raise NotExecutableError(
            f"refusing to execute {output.name!r}: "
            f"{output.applicability.value}/{output.output_type.value}"
        )
    return output


class ResearchAdapter(abc.ABC):
    """Abstract base for every research adapter.

    Subclasses implement :meth:`get_metadata` and :meth:`_analyze`. The public
    :meth:`run` wraps the analysis so an adapter can never crash the caller — on
    error it returns a safe *report* output instead.
    """

    @abc.abstractmethod
    def get_metadata(self) -> ResearchAdapterMetadata:
        """Return this adapter's static metadata."""

    @abc.abstractmethod
    def _analyze(self, **inputs: Any) -> ResearchOutput:
        """Deterministic research computation. Use :meth:`make_output`."""

    @property
    def name(self) -> str:
        return self.get_metadata().name

    def run(self, **inputs: Any) -> ResearchOutput:
        """Run the analysis with a fail-safe wrapper (never raises)."""
        try:
            out = self._analyze(**inputs)
        except Exception as exc:  # noqa: BLE001 — fail safe by design
            return self._failed_report(f"adapter error: {type(exc).__name__}: {exc}")
        if not isinstance(out, ResearchOutput):
            return self._failed_report("adapter returned a non-output; abstaining")
        return out

    # -- output builders --------------------------------------------------- #
    def make_output(
        self,
        *,
        summary: str,
        data: Optional[dict[str, Any]] = None,
        risk_notes: Optional[list[str]] = None,
        data_available: bool = True,
        output_type: Optional[OutputType] = None,
    ) -> ResearchOutput:
        """Build a :class:`ResearchOutput`, inheriting metadata + provenance."""
        meta = self.get_metadata()
        return ResearchOutput(
            name=meta.name,
            output_type=output_type or meta.output_type,
            applicability=meta.applicability,
            summary=summary,
            data=data or {},
            required_datasets=list(meta.required_datasets),
            reason=meta.reason,
            risk_notes=list(risk_notes or []),
            data_available=data_available,
            source_strategy=meta.source_strategy,
            source_repo_url=meta.source_repo_url,
            adapter_version=meta.version,
        )

    def requirements_report(self, missing: str) -> ResearchOutput:
        """A REPORT explaining what data is required when inputs are absent."""
        meta = self.get_metadata()
        needed = ", ".join(meta.required_datasets) or "the documented datasets"
        return self.make_output(
            summary=f"{meta.name}: insufficient inputs ({missing}). Requires: {needed}.",
            data={"missing": missing},
            data_available=False,
            output_type=OutputType.REPORT,
        )

    def _failed_report(self, reason: str) -> ResearchOutput:
        return self.make_output(
            summary=f"{self.name}: {reason}",
            data={"error": reason},
            data_available=False,
            output_type=OutputType.REPORT,
        )


__all__ = [
    "OutputType",
    "MT5Applicability",
    "NotExecutableError",
    "ResearchAdapterMetadata",
    "ResearchOutput",
    "ResearchAdapter",
    "ensure_not_executed",
    "REPO_URL",
]
