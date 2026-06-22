"""Append-only paper-trade log (M13).

A :class:`PaperTrade` is the full, auditable record of one paper trade's
lifecycle — from the moment it is opened (or rejected) through to close. Every
field required by the milestone is present, including the *provenance* of the
decision: the feature snapshot, the Kronos prediction, and the
:class:`~services.models.RiskDecision` that gated it. Per the hard project rule,
**every** paper trade carries a ``RiskDecision`` (approved *or* rejected) — the
model refuses to exist without one.

:class:`TradeLogStore` persists these records to a JSONL file under
``data/paper_trades/``. It is append-only: opening, updating and closing a trade
each write a new line keyed by ``trade_id``. Reading back, :meth:`records`
replays every line in order while :meth:`latest` collapses to the most recent
state of each trade (last write wins). This keeps the full history for auditing
without ever mutating an earlier line.

Pure I/O + data — **no AI, no MT5, no network**.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.config_loader import PROJECT_ROOT
from services.models import (
    KronosPrediction,
    MarketFeatures,
    RiskDecision,
    Side,
    TradeStatus,
)

DEFAULT_TRADE_DIR = PROJECT_ROOT / "data" / "paper_trades"
DEFAULT_TRADE_FILE = "trades.jsonl"


class PaperTrade(BaseModel):
    """Full lifecycle record of one paper trade (the M13 log schema).

    ``result`` is a human-readable outcome tag (``open`` / ``win`` / ``loss`` /
    ``breakeven`` / ``rejected``); ``status`` is the lifecycle state. The
    excursion fields are in account currency and only meaningful once the trade
    has been marked against price (they stay ``0.0`` otherwise).
    """

    # Reject unknown fields (typos fail loudly); keep enums as enums in memory.
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    trade_id: str = Field(..., min_length=1)
    timestamp: datetime
    symbol: str = Field(..., min_length=1)
    broker_symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    strategy_name: str = ""
    strategy_version: str = ""
    side: Side
    entry: float
    stop_loss: float
    take_profit: float
    lot_size: float = Field(..., ge=0)
    risk_percent: float = Field(0.0, ge=0)
    features_snapshot: Optional[MarketFeatures] = None
    kronos_prediction: Optional[KronosPrediction] = None
    # The gate every trade must carry — approved OR rejected.
    risk_decision: RiskDecision
    result: str = "open"
    pnl: Optional[float] = None
    max_adverse_excursion: float = 0.0
    max_favourable_excursion: float = 0.0
    status: TradeStatus = TradeStatus.PENDING

    def to_json_line(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json_line(cls, line: str) -> "PaperTrade":
        return cls.model_validate_json(line)


class TradeLogStore:
    """Append-only JSONL store of :class:`PaperTrade` records."""

    def __init__(self, path: Optional[Path] = None) -> None:
        base = path if path is not None else DEFAULT_TRADE_DIR / DEFAULT_TRADE_FILE
        self.path = Path(base)

    # -- writing ----------------------------------------------------------- #
    def append(self, trade: PaperTrade) -> None:
        """Append one trade record (a new line) to the log."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(trade.to_json_line() + "\n")

    # -- reading ----------------------------------------------------------- #
    def records(self) -> list[PaperTrade]:
        """Every record in write order (full history; empty if none)."""
        if not self.path.is_file():
            return []
        out: list[PaperTrade] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(PaperTrade.from_json_line(line))
        return out

    def latest(self) -> list[PaperTrade]:
        """Most recent state of each trade (last write wins), in first-seen order."""
        by_id: dict[str, PaperTrade] = {}
        order: list[str] = []
        for rec in self.records():
            if rec.trade_id not in by_id:
                order.append(rec.trade_id)
            by_id[rec.trade_id] = rec
        return [by_id[tid] for tid in order]

    def get(self, trade_id: str) -> Optional[PaperTrade]:
        """Latest state of a single trade by id, or ``None``."""
        found: Optional[PaperTrade] = None
        for rec in self.records():
            if rec.trade_id == trade_id:
                found = rec
        return found

    def approved(self) -> list[PaperTrade]:
        return [t for t in self.latest() if t.risk_decision.approved]

    def rejected(self) -> list[PaperTrade]:
        return [t for t in self.latest() if not t.risk_decision.approved]


__all__ = ["PaperTrade", "TradeLogStore", "DEFAULT_TRADE_DIR", "DEFAULT_TRADE_FILE"]
