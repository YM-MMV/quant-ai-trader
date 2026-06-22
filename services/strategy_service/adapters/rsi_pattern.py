"""RSI Pattern Recognition adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` RSI Pattern Recognition.
The original looks for chart patterns confirmed by RSI; we port the dependable
core: an **exit from an RSI extreme**.

* RSI was below the oversold threshold and crosses back up through it → selling
  pressure is exhausting → **BUY**;
* RSI was above the overbought threshold and crosses back down through it →
  **SELL**.

Crossing *out of* the zone (rather than merely being inside it) avoids fighting a
trend that keeps RSI pinned at an extreme.
"""
from __future__ import annotations

from typing import Any, Optional

from services.data_service.features import rsi
from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)


class RSIPatternAdapter(StrategyAdapter):
    """Reversal on RSI crossing back out of an overbought/oversold extreme."""

    VERSION = "1.0.0"

    def __init__(
        self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0
    ) -> None:
        self.period, self.oversold, self.overbought = period, oversold, overbought

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="rsi_pattern",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="RSI Pattern Recognition",
            category="technical_indicator",
            description="RSI reversal on exit from an overbought/oversold extreme.",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4", "D1"],
            min_candles=self.period + 6,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        close = candles["close"].astype(float)
        r = rsi(close, period=self.period)
        prev, now = r.iloc[-2], r.iloc[-1]
        if not c._is_num(prev, now):
            return self.none_signal("RSI not warmed up yet")

        entry = float(close.iloc[-1])
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)

        # Crossing up out of oversold → BUY.
        if prev < self.oversold <= now:
            conf = c.clamp_confidence(0.45 + 0.5 * min((self.oversold - prev) / 30.0, 1.0))
            sl, tp = c.sl_tp_from_atr(SignalSide.BUY, entry, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                f"RSI crossed back above {self.oversold:g} (oversold exhaustion)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        # Crossing down out of overbought → SELL.
        if prev > self.overbought >= now:
            conf = c.clamp_confidence(0.45 + 0.5 * min((prev - self.overbought) / 30.0, 1.0))
            sl, tp = c.sl_tp_from_atr(SignalSide.SELL, entry, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                f"RSI crossed back below {self.overbought:g} (overbought exhaustion)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("RSI not exiting an extreme at the latest bar")


__all__ = ["RSIPatternAdapter"]
