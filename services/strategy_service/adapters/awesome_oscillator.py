"""Awesome Oscillator adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Awesome Oscillator
(AO). Bill Williams' AO measures momentum as the gap between a fast and slow
simple moving average of the *median price* ``(high + low) / 2``:

    AO = SMA(median, 5) - SMA(median, 34)

We trade the **zero-line crossover** — AO turning positive is bullish momentum
(BUY), turning negative is bearish (SELL). Acting only on a fresh crossover at
the latest bar keeps it to one signal per momentum turn.
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


class AwesomeOscillatorAdapter(StrategyAdapter):
    """Signal on an Awesome Oscillator zero-line crossover."""

    VERSION = "1.0.0"

    def __init__(self, fast: int = 5, slow: int = 34) -> None:
        self.fast, self.slow = fast, slow

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="awesome_oscillator",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="Awesome Oscillator",
            category="technical_indicator",
            description="Bill Williams AO zero-line crossover (momentum).",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4", "D1"],
            min_candles=self.slow + 6,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        median = c.median_price(candles)
        ao = (
            median.rolling(self.fast, min_periods=self.fast).mean()
            - median.rolling(self.slow, min_periods=self.slow).mean()
        )
        prev, now = ao.iloc[-2], ao.iloc[-1]
        if not c._is_num(prev, now):
            return self.none_signal("Awesome Oscillator not warmed up yet")

        entry = float(candles["close"].astype(float).iloc[-1])
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)
        strength = abs(now) / atr if atr else abs(now) / (entry * 0.002)
        conf = c.clamp_confidence(0.45 + 0.5 * min(strength, 1.0))

        if prev <= 0 < now:
            sl, tp = c.sl_tp_from_atr(SignalSide.BUY, entry, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                "Awesome Oscillator crossed above zero (bullish momentum)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        if prev >= 0 > now:
            sl, tp = c.sl_tp_from_atr(SignalSide.SELL, entry, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                "Awesome Oscillator crossed below zero (bearish momentum)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no fresh AO zero-line crossover at the latest bar")


__all__ = ["AwesomeOscillatorAdapter"]
