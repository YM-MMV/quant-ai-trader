"""Paper-trade execution (M13).

The :class:`PaperExecutionService` is the *only* place an :class:`OrderIntent`
turns into a (simulated) fill — and it never touches MT5. Given the
:class:`~services.models.RiskDecision` that gated an intent, it either:

* **opens** an approved trade as a paper position (a :class:`PaperTrade` with
  status ``OPEN``), or
* **logs** a rejected trade (status ``REJECTED``) with the risk manager's
  reasons — rejected trades are recorded just like approved ones.

Both paths append a full record to the :class:`~services.execution_service.trade_log.TradeLogStore`
and write the corresponding entries to the
:class:`~services.execution_service.audit_log.AuditLog` (risk decision,
execution decision / rejection, config + system mode on the first call).

Hard rules enforced here:

* **No real MT5 orders.** Paper only — fills come from the supplied reference
  price, nothing is sent anywhere.
* **Every trade carries a RiskDecision.** :meth:`execute` requires one; a
  ``PaperTrade`` cannot be built without it.

Deterministic: ``trade_id`` comes from a monotonic counter (``PT-000001`` …) and
timestamps can be injected via ``now=``, so the same inputs reproduce the same
log byte-for-byte.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.config_loader import RiskConfig
from services.execution_service.audit_log import AuditLog
from services.execution_service.trade_log import PaperTrade, TradeLogStore
from services.models import (
    KronosPrediction,
    MarketFeatures,
    RiskDecision,
    Side,
    TradeStatus,
)
from services.risk_service.risk_manager import RiskContext
from services.risk_service.symbol_specs import broker_symbol, get_symbol_spec


def _result_from_pnl(pnl: Optional[float]) -> str:
    if pnl is None:
        return "open"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


class PaperExecutionService:
    """Open / close paper trades and write the trade + audit logs."""

    def __init__(
        self,
        *,
        trade_log: Optional[TradeLogStore] = None,
        audit_log: Optional[AuditLog] = None,
        config: Optional[RiskConfig] = None,
        id_prefix: str = "PT",
    ) -> None:
        self.trade_log = trade_log or TradeLogStore()
        self.audit_log = audit_log or AuditLog()
        self.config = config
        self.id_prefix = id_prefix
        self._counter = 0
        self._mode_logged = False

    # ------------------------------------------------------------------ #
    def execute(
        self,
        decision: RiskDecision,
        context: RiskContext,
        *,
        timeframe: str,
        strategy_name: str = "",
        strategy_version: str = "",
        features_snapshot: Optional[MarketFeatures] = None,
        kronos_prediction: Optional[KronosPrediction] = None,
        reference_price: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> PaperTrade:
        """Turn a gated :class:`RiskDecision` into a logged paper trade.

        Returns the :class:`PaperTrade` (status ``OPEN`` if approved, ``REJECTED``
        otherwise). Both the trade log and the audit log are written before the
        record is returned.
        """
        if not isinstance(decision, RiskDecision):
            raise TypeError("execute requires a RiskDecision (every trade must be gated)")

        now = now or datetime.now(timezone.utc)
        intent = decision.intent

        # First call records the config + system mode for the audit trail.
        if not self._mode_logged:
            self.audit_log.system_mode(decision.mode, now=now)
            if self.config is not None:
                self.audit_log.config_snapshot(self.config, mode=decision.mode, now=now)
            self._mode_logged = True

        # Always audit the proposed intent and the risk verdict.
        self.audit_log.signal_proposed(intent, mode=decision.mode, now=now)
        self.audit_log.risk_decision(decision, now=now)

        self._counter += 1
        trade_id = f"{self.id_prefix}-{self._counter:06d}"

        entry = reference_price
        if entry is None:
            entry = intent.price if intent.price is not None else context.reference_price
        entry = float(entry) if entry is not None else 0.0

        lot_size = decision.approved_volume if decision.approved_volume else intent.volume
        lot_size = float(lot_size or 0.0)

        trade = PaperTrade(
            trade_id=trade_id,
            timestamp=now,
            symbol=intent.symbol,
            broker_symbol=broker_symbol(intent.symbol) or intent.symbol,
            timeframe=timeframe,
            strategy_name=strategy_name or (intent.strategy_id or ""),
            strategy_version=strategy_version,
            side=intent.side,
            entry=entry,
            stop_loss=float(intent.stop_loss or 0.0),
            take_profit=float(intent.take_profit or 0.0),
            lot_size=lot_size,
            risk_percent=self._risk_percent(intent, context, entry, lot_size),
            features_snapshot=features_snapshot,
            kronos_prediction=kronos_prediction,
            risk_decision=decision,
            result="open" if decision.approved else "rejected",
            pnl=None,
            status=TradeStatus.OPEN if decision.approved else TradeStatus.REJECTED,
        )

        self.trade_log.append(trade)
        if decision.approved:
            self.audit_log.execution_decision(
                trade, mode=decision.mode,
                message=f"opened paper trade {trade_id}", now=now)
        else:
            self.audit_log.trade_rejected(
                trade, reasons=decision.reasons, mode=decision.mode, now=now)
        return trade

    # ------------------------------------------------------------------ #
    def mark(self, trade: PaperTrade, *, high: float, low: float) -> PaperTrade:
        """Update MAE/MFE (account currency) from a bar's high/low.

        Returns a new :class:`PaperTrade` with the running excursions widened;
        does **not** write to the log (excursions are persisted on close).
        """
        contract = self._contract_size(trade.symbol)
        if trade.side is Side.BUY:
            favourable = (float(high) - trade.entry) * trade.lot_size * contract
            adverse = (trade.entry - float(low)) * trade.lot_size * contract
        else:  # SELL
            favourable = (trade.entry - float(low)) * trade.lot_size * contract
            adverse = (float(high) - trade.entry) * trade.lot_size * contract
        mfe = max(trade.max_favourable_excursion, favourable, 0.0)
        mae = max(trade.max_adverse_excursion, adverse, 0.0)
        return trade.model_copy(update={
            "max_favourable_excursion": round(mfe, 6),
            "max_adverse_excursion": round(mae, 6),
        })

    def close(
        self,
        trade: PaperTrade,
        *,
        exit_price: float,
        now: Optional[datetime] = None,
    ) -> PaperTrade:
        """Close an open paper trade at ``exit_price`` and log the closed record."""
        if trade.status is not TradeStatus.OPEN:
            raise ValueError(f"cannot close trade {trade.trade_id!r} in status "
                             f"{trade.status.value!r}")
        now = now or datetime.now(timezone.utc)
        contract = self._contract_size(trade.symbol)
        if trade.side is Side.BUY:
            pnl = (float(exit_price) - trade.entry) * trade.lot_size * contract
        else:
            pnl = (trade.entry - float(exit_price)) * trade.lot_size * contract
        pnl = round(pnl, 6)

        # Ensure the realized move is reflected in the excursions.
        mfe = max(trade.max_favourable_excursion, pnl, 0.0)
        mae = max(trade.max_adverse_excursion, -pnl, 0.0)

        closed = trade.model_copy(update={
            "pnl": pnl,
            "result": _result_from_pnl(pnl),
            "status": TradeStatus.CLOSED,
            "timestamp": now,
            "max_favourable_excursion": round(mfe, 6),
            "max_adverse_excursion": round(mae, 6),
        })
        self.trade_log.append(closed)
        self.audit_log.execution_decision(
            closed, mode=closed.risk_decision.mode,
            message=f"closed paper trade {closed.trade_id} pnl={pnl}", now=now)
        return closed

    # ------------------------------------------------------------------ #
    @staticmethod
    def _contract_size(symbol: str) -> float:
        spec = get_symbol_spec(symbol)
        return spec.contract_size if spec else 1.0

    def _risk_percent(self, intent, context: RiskContext, entry: float, lots: float) -> float:
        spec = get_symbol_spec(intent.symbol)
        sl = intent.stop_loss
        if (spec is None or sl is None or sl <= 0 or lots <= 0
                or entry <= 0 or context.account_balance <= 0):
            return 0.0
        money_at_risk = abs(entry - float(sl)) * lots * spec.contract_size
        return round(money_at_risk / context.account_balance * 100.0, 6)


__all__ = ["PaperExecutionService"]
