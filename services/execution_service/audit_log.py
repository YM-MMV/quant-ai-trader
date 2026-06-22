"""Append-only audit log for the paper-trading pipeline (M13).

Every decision the system makes on the path from an AI-proposed signal to a
(paper) fill is recorded here as an immutable, timestamped JSON line. The audit
trail answers "why did this trade happen — or why was it rejected?" after the
fact, without re-running anything.

Recorded event types (see :class:`AuditEventType`):

* ``signal_proposed``   — the AI/strategy proposed a signal (the OrderIntent)
* ``strategy_output``   — a strategy adapter's raw output / rationale
* ``risk_decision``     — the RiskManager's verdict (approved + checks + reasons)
* ``execution_decision``— what execution did with an *approved* decision
* ``trade_rejected``    — a rejected trade and the reasons it was rejected
* ``config_snapshot``   — the risk/app config in force at decision time
* ``system_mode``       — the trading mode the system is running in

This module is pure I/O over a JSONL file — **no AI, no MT5, no network**. The
log is append-only: events are never mutated or deleted, so the same run always
produces the same trail, and reads simply replay what was written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from services.config_loader import PROJECT_ROOT
from services.models import RiskDecision, TradingMode

DEFAULT_AUDIT_DIR = PROJECT_ROOT / "data" / "paper_trades"
DEFAULT_AUDIT_FILE = "audit.jsonl"


class AuditEventType(str, Enum):
    """The kinds of events the audit log records (see module docstring)."""

    SIGNAL_PROPOSED = "signal_proposed"
    STRATEGY_OUTPUT = "strategy_output"
    RISK_DECISION = "risk_decision"
    EXECUTION_DECISION = "execution_decision"
    TRADE_REJECTED = "trade_rejected"
    CONFIG_SNAPSHOT = "config_snapshot"
    SYSTEM_MODE = "system_mode"


@dataclass(frozen=True)
class AuditEvent:
    """One immutable entry in the audit trail."""

    event_id: str
    timestamp: datetime
    event_type: AuditEventType
    mode: Optional[TradingMode]
    message: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "mode": self.mode.value if self.mode is not None else None,
            "message": self.message,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        mode = data.get("mode")
        return cls(
            event_id=data["event_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_type=AuditEventType(data["event_type"]),
            mode=TradingMode(mode) if mode is not None else None,
            message=data.get("message", ""),
            payload=data.get("payload", {}),
        )


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of arbitrary payload values to JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):  # pydantic v2 models
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


class AuditLog:
    """Append-only JSONL audit trail for the execution pipeline.

    A monotonic counter gives each event a deterministic ``event_id`` within a
    process (``AUD-000001`` …). Timestamps default to ``datetime.now(UTC)`` but
    can be injected via ``now=`` for reproducible runs and tests.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        prefix: str = "AUD",
    ) -> None:
        base = path if path is not None else DEFAULT_AUDIT_DIR / DEFAULT_AUDIT_FILE
        self.path = Path(base)
        self.prefix = prefix
        self._counter = 0

    # -- writing ----------------------------------------------------------- #
    def record(
        self,
        event_type: AuditEventType,
        *,
        payload: Optional[dict[str, Any]] = None,
        mode: Optional[TradingMode] = None,
        message: str = "",
        now: Optional[datetime] = None,
    ) -> AuditEvent:
        """Append one event to the trail and return it."""
        self._counter += 1
        event = AuditEvent(
            event_id=f"{self.prefix}-{self._counter:06d}",
            timestamp=now or datetime.now(timezone.utc),
            event_type=AuditEventType(event_type),
            mode=mode,
            message=message,
            payload={k: _jsonable(v) for k, v in (payload or {}).items()},
        )
        self._append(event)
        return event

    def _append(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    # -- convenience recorders -------------------------------------------- #
    def signal_proposed(self, intent, *, mode=None, message="", now=None) -> AuditEvent:
        return self.record(AuditEventType.SIGNAL_PROPOSED,
                           payload={"intent": intent}, mode=mode,
                           message=message, now=now)

    def strategy_output(self, signal, *, mode=None, message="", now=None) -> AuditEvent:
        return self.record(AuditEventType.STRATEGY_OUTPUT,
                           payload={"signal": signal}, mode=mode,
                           message=message, now=now)

    def risk_decision(self, decision: RiskDecision, *, message="", now=None) -> AuditEvent:
        return self.record(AuditEventType.RISK_DECISION,
                           payload={"decision": decision}, mode=decision.mode,
                           message=message, now=now)

    def execution_decision(self, trade, *, mode=None, message="", now=None) -> AuditEvent:
        return self.record(AuditEventType.EXECUTION_DECISION,
                           payload={"trade": trade}, mode=mode,
                           message=message, now=now)

    def trade_rejected(self, trade, *, reasons=None, mode=None, now=None) -> AuditEvent:
        return self.record(AuditEventType.TRADE_REJECTED,
                           payload={"trade": trade, "reasons": list(reasons or [])},
                           mode=mode,
                           message="trade rejected by risk manager", now=now)

    def config_snapshot(self, config, *, mode=None, now=None) -> AuditEvent:
        return self.record(AuditEventType.CONFIG_SNAPSHOT,
                           payload={"config": config}, mode=mode,
                           message="config snapshot at decision time", now=now)

    def system_mode(self, mode: TradingMode, *, now=None) -> AuditEvent:
        return self.record(AuditEventType.SYSTEM_MODE,
                           payload={"mode": mode}, mode=mode,
                           message=f"system running in {mode.value} mode", now=now)

    # -- reading ----------------------------------------------------------- #
    def events(self) -> list[AuditEvent]:
        """Replay the whole trail in write order (empty if nothing logged)."""
        if not self.path.is_file():
            return []
        events: list[AuditEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(AuditEvent.from_dict(json.loads(line)))
        return events

    def events_of(self, event_type: AuditEventType) -> list[AuditEvent]:
        """All events of one type, in write order."""
        wanted = AuditEventType(event_type)
        return [e for e in self.events() if e.event_type is wanted]


__all__ = ["AuditEventType", "AuditEvent", "AuditLog",
           "DEFAULT_AUDIT_DIR", "DEFAULT_AUDIT_FILE"]
