"""Dual Thrust adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Dual Thrust: build a
volatility range from recent bars and place breakout trigger lines around the
current bar's open.

    Range = max(HH - LC, HC - LL)   over the prior ``lookback`` bars
    buy_line  = open + k1 * Range
    sell_line = open - k2 * Range

where HH/LL are the highest high / lowest low and HC/LC the highest / lowest
close of the lookback window. A BUY fires when the bar opens at/below the buy
line and closes above it (an intrabar upside break); a SELL is the mirror.

Clean re-implementation notes:
* The lookback window excludes the current bar (no look-ahead).
* The open ≤ trigger ≤ close construction makes each signal a *fresh* break, so
  no extra state is needed.
* Cost-aware: the break must clear round-trip spread + slippage.
"""
from __future__ import annotations

from typing import Any, Optional

from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)


class DualThrustAdapter(StrategyAdapter):
    """Range-based intraday breakout around the current bar's open."""

    VERSION = "1.0.0"

    def __init__(self, lookback: int = 20, k1: float = 0.5, k2: float = 0.5) -> None:
        self.lookback, self.k1, self.k2 = lookback, k1, k2

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="dual_thrust",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="Dual Thrust",
            category="breakout",
            description="Dual Thrust volatility-range breakout (k1/k2 triggers).",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4"],
            min_candles=self.lookback + 5,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        high = candles["high"].astype(float).reset_index(drop=True)
        low = candles["low"].astype(float).reset_index(drop=True)
        close = candles["close"].astype(float).reset_index(drop=True)
        open_ = candles["open"].astype(float).reset_index(drop=True)

        # Prior `lookback` bars, excluding the current one.
        window_hi = high.iloc[-self.lookback - 1:-1]
        window_lo = low.iloc[-self.lookback - 1:-1]
        window_cl = close.iloc[-self.lookback - 1:-1]
        rng = max(
            window_hi.max() - window_cl.min(),
            window_cl.max() - window_lo.min(),
        )
        if not c._is_num(rng) or rng <= 0:
            return self.none_signal("degenerate Dual Thrust range")

        o, cl = float(open_.iloc[-1]), float(close.iloc[-1])
        buy_line = o + self.k1 * rng
        sell_line = o - self.k2 * rng
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)

        if o <= buy_line < cl and c.clears_cost(cl - buy_line, candles):
            conf = c.clamp_confidence(0.45 + 0.5 * min((cl - buy_line) / rng, 1.0))
            sl, tp = c.sl_tp_from_atr(SignalSide.BUY, cl, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                "price broke above the Dual Thrust buy line",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        if o >= sell_line > cl and c.clears_cost(sell_line - cl, candles):
            conf = c.clamp_confidence(0.45 + 0.5 * min((sell_line - cl) / rng, 1.0))
            sl, tp = c.sl_tp_from_atr(SignalSide.SELL, cl, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                "price broke below the Dual Thrust sell line",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("price inside the Dual Thrust trigger band")


__all__ = ["DualThrustAdapter"]
