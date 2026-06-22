"""Trading-cost model for the simple backtester.

Frictionless backtests are forbidden in this project (see ARCHITECTURE.md), so
every fill goes through a :class:`CostModel`. It applies, deterministically:

* **spread** — half the quoted spread is crossed on entry and half on exit
  (price moves *against* you each side),
* **slippage** — a fixed adverse offset (in broker points) on every fill,
* **commission** — a per-trade placeholder charged once per round trip.

Spreads/slippage are quoted in broker *points*; this module converts them to
price using the symbol's pip convention (mirrors the sample-data generator):
JPY pairs and metals use a 0.01 pip, everything else 0.0001, and one point is
1/10 of a pip (3/5-digit feeds).
"""
from __future__ import annotations

from dataclasses import dataclass


def pip_size(symbol: str) -> float:
    """Price value of one pip for ``symbol`` (simplified convention)."""
    s = (symbol or "").upper()
    if s.endswith("JPY") or s.startswith("XAU") or s.startswith("XAG"):
        return 0.01
    return 0.0001


def point_size(symbol: str) -> float:
    """Price value of one broker point (1/10 pip)."""
    return pip_size(symbol) / 10.0


@dataclass(frozen=True)
class CostModel:
    """Deterministic spread + slippage + commission model.

    ``spread_fraction`` scales how much of the quoted spread is actually crossed
    over the round trip (1.0 = full spread, split half per side). ``commission``
    is a placeholder in account currency, charged once per completed trade.
    """

    slippage_points: float = 0.0
    commission: float = 0.0
    spread_fraction: float = 1.0

    def per_side_penalty(self, symbol: str, spread_points: float) -> float:
        """Adverse price offset applied to a single fill (entry *or* exit)."""
        pt = point_size(symbol)
        half_spread = 0.5 * self.spread_fraction * max(spread_points, 0.0) * pt
        slippage = max(self.slippage_points, 0.0) * pt
        return half_spread + slippage

    def fill_price(
        self,
        raw_price: float,
        *,
        symbol: str,
        spread_points: float,
        side: str,
        opening: bool,
    ) -> float:
        """Cost-adjusted fill price.

        Buying (opening a long or closing a short) fills *higher*; selling
        (opening a short or closing a long) fills *lower* — always against the
        trader by the per-side penalty.
        """
        penalty = self.per_side_penalty(symbol, spread_points)
        buying = (side == "BUY" and opening) or (side == "SELL" and not opening)
        adjusted = raw_price + penalty if buying else raw_price - penalty
        # Never let costs push a price non-positive.
        return max(adjusted, point_size(symbol))


# A zero-cost model is only for unit-testing mechanics; real backtests must use
# a CostModel with non-zero friction (enforced by the backtester's config docs).
ZERO_COST = CostModel(slippage_points=0.0, commission=0.0, spread_fraction=0.0)


__all__ = ["CostModel", "pip_size", "point_size", "ZERO_COST"]
