"""Heikin-Ashi adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Heikin-Ashi: smooth the
OHLC into Heikin-Ashi candles and trade the *colour flip* — a bearish HA candle
followed by a bullish one signals a possible up-turn (BUY) and vice versa.

Heikin-Ashi recurrence (causal — bar ``t`` depends only on bars ``<= t``):

    HA_close[t] = (open + high + low + close) / 4
    HA_open[0]  = (open[0] + close[0]) / 2
    HA_open[t]  = (HA_open[t-1] + HA_close[t-1]) / 2

A candle is "bullish" when ``HA_close > HA_open``. We act only on a fresh flip
at the latest bar so the adapter emits one signal per turn.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)


class HeikinAshiAdapter(StrategyAdapter):
    """Signal on a Heikin-Ashi candle colour flip."""

    VERSION = "1.0.0"

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="heikin_ashi",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="Heikin-Ashi",
            category="candlestick_pattern",
            description="Heikin-Ashi smoothed candle colour flip (trend turn).",
            supported_symbols=None,
            supported_timeframes=["M30", "H1", "H4", "D1"],
            min_candles=5,
            asset_classes=c.ASSET_CLASSES,
        )

    @staticmethod
    def _ha_open_close(o, h, l, cl) -> tuple[np.ndarray, np.ndarray]:
        ha_close = (o + h + l + cl) / 4.0
        ha_open = np.empty_like(ha_close)
        ha_open[0] = (o[0] + cl[0]) / 2.0
        for i in range(1, len(ha_close)):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
        return ha_open, ha_close

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        o = candles["open"].astype(float).to_numpy()
        h = candles["high"].astype(float).to_numpy()
        low = candles["low"].astype(float).to_numpy()
        cl = candles["close"].astype(float).to_numpy()

        ha_open, ha_close = self._ha_open_close(o, h, low, cl)
        prev_bull = ha_close[-2] > ha_open[-2]
        now_bull = ha_close[-1] > ha_open[-1]

        entry = float(cl[-1])
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)
        # Confidence from the latest HA body relative to ATR.
        body = abs(ha_close[-1] - ha_open[-1])
        strength = body / atr if atr else body / (entry * 0.002)
        conf = c.clamp_confidence(0.4 + 0.5 * min(strength, 1.0))

        if now_bull and not prev_bull:
            sl, tp = c.sl_tp_from_atr(SignalSide.BUY, entry, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                "Heikin-Ashi flipped bullish (bearish→bullish candle)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        if prev_bull and not now_bull:
            sl, tp = c.sl_tp_from_atr(SignalSide.SELL, entry, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                "Heikin-Ashi flipped bearish (bullish→bearish candle)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes, symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no Heikin-Ashi colour flip at the latest bar")


__all__ = ["HeikinAshiAdapter"]
