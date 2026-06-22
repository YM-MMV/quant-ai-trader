"""Deterministic position (lot) sizing.

Given an account balance, a risk budget (percent of balance), the stop distance,
and the symbol's contract spec, compute the lot size that risks *no more* than
the budget — then snap it to the broker's lot step and min/max constraints.

    money_risked(lots) = stop_distance_in_price * contract_size * lots
    raw_lots           = (balance * risk_pct / 100) / (stop_distance * contract_size)

The result is floored to ``lot_step`` (never rounds *up* into extra risk),
clamped to ``[min_lot, max_lot]``, and reports the money actually at risk. If the
budget cannot fund even one ``min_lot`` the size is ``0.0`` (do not trade) rather
than silently exceeding the risk limit.

**No AI. Pure arithmetic** — identical inputs always give identical lots.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from services.risk_service.symbol_specs import SymbolSpec


@dataclass(frozen=True)
class SizingResult:
    """Outcome of a lot-size computation."""

    symbol: str
    broker_symbol: str
    lots: float                 # final, step-snapped, clamped lot size (0 ⇒ no trade)
    raw_lots: float             # before stepping/clamping
    risk_amount: float          # budgeted risk (account currency)
    money_at_risk: float        # risk implied by the final lots
    stop_distance: float        # |entry - stop| in price terms
    within_budget: bool         # money_at_risk <= risk_amount (+ tolerance)
    reason: str = ""            # note when lots were capped/floored to 0


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    steps = math.floor(round(value / step, 9))
    return round(steps * step, 10)


def compute_lot_size(
    *,
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    spec: SymbolSpec,
) -> SizingResult:
    """Compute the risk-budgeted lot size for a trade (see module docstring)."""
    risk_amount = max(account_balance, 0.0) * max(risk_pct, 0.0) / 100.0
    stop_distance = abs(float(entry_price) - float(stop_loss))

    def result(lots, raw, reason=""):
        money = round(lots * stop_distance * spec.contract_size, 6)
        return SizingResult(
            symbol=spec.symbol, broker_symbol=spec.broker_alias,
            lots=lots, raw_lots=raw, risk_amount=round(risk_amount, 6),
            money_at_risk=money, stop_distance=stop_distance,
            within_budget=money <= risk_amount + 1e-9, reason=reason,
        )

    if stop_distance <= 0:
        return result(0.0, 0.0, "non-positive stop distance")
    if risk_amount <= 0:
        return result(0.0, 0.0, "non-positive risk budget")

    per_lot_risk = stop_distance * spec.contract_size
    raw_lots = risk_amount / per_lot_risk

    lots = _floor_to_step(raw_lots, spec.lot_step)
    reason = ""
    if lots > spec.max_lot:
        lots = _floor_to_step(spec.max_lot, spec.lot_step)
        reason = "capped at maximum lot"
    if lots < spec.min_lot:
        return result(0.0, raw_lots, "below minimum lot for the risk budget")

    return result(lots, raw_lots, reason)


__all__ = ["SizingResult", "compute_lot_size"]
