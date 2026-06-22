"""Parabolic SAR adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Parabolic SAR: Welles
Wilder's stop-and-reverse trend follower. The SAR dot trails price; when price
crosses it, the trend flips. We BUY on a fresh flip to an up-trend and SELL on a
fresh flip to a down-trend, and use the SAR value itself as the trailing stop.

Clean re-implementation notes:
* Standard recurrence with acceleration factor ``af`` stepping from
  ``af_step`` to ``af_max`` as new extremes print; SAR is clamped to not breach
  the prior two bars' range. Computed left-to-right, so it is causal.
* "Fresh flip" = the trend direction at the last bar differs from the prior bar.
* TP is ATR-scaled; SL is the current SAR (its native purpose).
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


class ParabolicSARAdapter(StrategyAdapter):
    """Signal on a Parabolic SAR stop-and-reverse flip."""

    VERSION = "1.0.0"

    def __init__(self, af_step: float = 0.02, af_max: float = 0.2) -> None:
        self.af_step, self.af_max = af_step, af_max

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="parabolic_sar",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="Parabolic SAR",
            category="technical_indicator",
            description="Wilder's Parabolic SAR stop-and-reverse trend follower.",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4", "D1"],
            min_candles=10,
            asset_classes=c.ASSET_CLASSES,
        )

    def _psar(self, high: np.ndarray, low: np.ndarray):
        """Return (sar, uptrend) arrays via Wilder's recurrence."""
        n = len(high)
        sar = np.empty(n)
        up = np.empty(n, dtype=bool)

        uptrend = high[1] >= high[0]  # seed direction from the first move
        af = self.af_step
        ep = high[0] if uptrend else low[0]
        sar[0] = low[0] if uptrend else high[0]
        up[0] = uptrend

        for i in range(1, n):
            prev_sar = sar[i - 1]
            cur = prev_sar + af * (ep - prev_sar)
            if uptrend:
                # SAR cannot exceed the prior two lows.
                cur = min(cur, low[i - 1], low[max(i - 2, 0)])
                if low[i] < cur:  # flip to downtrend
                    uptrend = False
                    cur = ep            # reset SAR to the extreme point
                    ep = low[i]
                    af = self.af_step
                elif high[i] > ep:      # extend uptrend
                    ep = high[i]
                    af = min(af + self.af_step, self.af_max)
            else:
                cur = max(cur, high[i - 1], high[max(i - 2, 0)])
                if high[i] > cur:  # flip to uptrend
                    uptrend = True
                    cur = ep
                    ep = high[i]
                    af = self.af_step
                elif low[i] < ep:       # extend downtrend
                    ep = low[i]
                    af = min(af + self.af_step, self.af_max)
            sar[i] = cur
            up[i] = uptrend
        return sar, up

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        high = candles["high"].astype(float).to_numpy()
        low = candles["low"].astype(float).to_numpy()
        sar, up = self._psar(high, low)

        entry = float(candles["close"].astype(float).iloc[-1])
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)
        conf = c.clamp_confidence(0.55)

        if up[-1] and not up[-2]:
            sl = c.positive(min(sar[-1], entry * 0.999), like=entry)
            _, tp = c.sl_tp_from_atr(SignalSide.BUY, entry, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                "Parabolic SAR flipped below price (up-trend begins)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["SL tracks the SAR dot"],
                symbol=c.last_symbol(candles) or None,
            )
        if not up[-1] and up[-2]:
            sl = c.positive(max(sar[-1], entry * 1.001), like=entry)
            _, tp = c.sl_tp_from_atr(SignalSide.SELL, entry, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                "Parabolic SAR flipped above price (down-trend begins)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["SL tracks the SAR dot"],
                symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no Parabolic SAR flip at the latest bar")


__all__ = ["ParabolicSARAdapter"]
