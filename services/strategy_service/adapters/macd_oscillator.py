"""MACD Oscillator adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` MACD Oscillator: trade
the crossover of the MACD line and its signal line. We act only on a *fresh*
crossover at the latest bar — the MACD histogram changing sign — so the adapter
emits one signal per cross rather than for every bar the lines stay separated.

Clean re-implementation notes:
* Uses the project's causal :func:`services.data_service.features.macd` (EMAs
  with ``adjust=False`` and NaN warm-up) — no look-ahead.
* SL/TP are ATR-scaled hints; confidence scales with histogram strength
  relative to ATR (a stronger cross is more convincing).
* Cost-aware: round-trip spread + slippage is surfaced as a risk note.
"""
from __future__ import annotations

from typing import Any, Optional

from services.data_service.features import macd
from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)


class MACDOscillatorAdapter(StrategyAdapter):
    """Signal on a fresh MACD/signal-line crossover (histogram sign change)."""

    VERSION = "1.0.0"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self.fast, self.slow, self.signal = fast, slow, signal

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="macd_oscillator",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="MACD Oscillator",
            category="technical_indicator",
            description="MACD line / signal line crossover (momentum).",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4", "D1"],
            # slow EMA (26) + signal EMA (9) warm-up, plus a couple of bars.
            min_candles=self.slow + self.signal + 5,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        close = candles["close"].astype(float)
        m = macd(close, fast=self.fast, slow=self.slow, signal=self.signal)
        hist_prev, hist_now = m["macd_hist"].iloc[-2], m["macd_hist"].iloc[-1]
        if not c._is_num(hist_prev, hist_now):
            return self.none_signal("MACD histogram not warmed up yet")

        entry = float(close.iloc[-1])
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)
        # Strength: histogram magnitude relative to ATR, gently scaled.
        strength = abs(hist_now) / atr if atr else abs(hist_now) / (entry * 0.002)
        conf = c.clamp_confidence(0.45 + 0.5 * min(strength, 1.0))

        if hist_prev <= 0 < hist_now:
            sl, tp = c.sl_tp_from_atr(SignalSide.BUY, entry, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                "MACD line crossed above signal line (bullish crossover)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        if hist_prev >= 0 > hist_now:
            sl, tp = c.sl_tp_from_atr(SignalSide.SELL, entry, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                "MACD line crossed below signal line (bearish crossover)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no fresh MACD crossover at the latest bar")


__all__ = ["MACDOscillatorAdapter"]
