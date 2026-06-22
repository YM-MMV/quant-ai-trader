"""Bollinger Bands Pattern Recognition adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Bollinger Bands Pattern
Recognition. The full original hunts multi-bar W-bottom / M-top shapes; we port
the robust core of that idea as a **band re-entry** pattern, which is the signal
those shapes ultimately confirm:

* price closes *below* the lower band, then the next close snaps back *inside*
  the band → exhaustion of the down-move → **BUY** (mean reversion up);
* price closes *above* the upper band, then snaps back inside → **SELL**.

Re-entry (rather than a naive "touch") avoids selling/buying into a band-riding
trend. TP is the band mid-line (the reversion target); SL is ATR-scaled beyond
the recent extreme.
"""
from __future__ import annotations

from typing import Any, Optional

from services.data_service.features import bollinger_bands
from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)


class BollingerBandsPatternAdapter(StrategyAdapter):
    """Mean-reversion on a Bollinger Band re-entry pattern."""

    VERSION = "1.0.0"

    def __init__(self, window: int = 20, num_std: float = 2.0) -> None:
        self.window, self.num_std = window, num_std

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="bollinger_bands_pattern",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="Bollinger Bands Pattern Recognition",
            category="technical_indicator",
            description="Bollinger Band re-entry mean-reversion pattern.",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4", "D1"],
            min_candles=self.window + 5,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        close = candles["close"].astype(float)
        bb = bollinger_bands(close, window=self.window, num_std=self.num_std)
        prev_c, now_c = float(close.iloc[-2]), float(close.iloc[-1])
        up_prev, up_now = bb["bb_upper"].iloc[-2], bb["bb_upper"].iloc[-1]
        lo_prev, lo_now = bb["bb_lower"].iloc[-2], bb["bb_lower"].iloc[-1]
        mid = bb["bb_mid"].iloc[-1]
        if not c._is_num(up_prev, up_now, lo_prev, lo_now, mid):
            return self.none_signal("Bollinger Bands not warmed up yet")

        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)
        band = float(up_now - lo_now) or (now_c * 0.002)

        # Re-entry from below the lower band → BUY (reversion up).
        if prev_c < lo_prev and now_c >= lo_now:
            conf = c.clamp_confidence(0.45 + 0.5 * min((lo_prev - prev_c) / band, 1.0))
            sl = c.positive(min(prev_c, now_c) - (atr or band * 0.5), like=now_c)
            tp = c.positive(float(mid), like=now_c)
            return self.make_signal(
                SignalSide.BUY, conf,
                "price re-entered above the lower Bollinger band (reversion up)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["TP at band mid-line"],
                symbol=c.last_symbol(candles) or None,
            )
        # Re-entry from above the upper band → SELL (reversion down).
        if prev_c > up_prev and now_c <= up_now:
            conf = c.clamp_confidence(0.45 + 0.5 * min((prev_c - up_prev) / band, 1.0))
            sl = c.positive(max(prev_c, now_c) + (atr or band * 0.5), like=now_c)
            tp = c.positive(float(mid), like=now_c)
            return self.make_signal(
                SignalSide.SELL, conf,
                "price re-entered below the upper Bollinger band (reversion down)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["TP at band mid-line"],
                symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no Bollinger band re-entry at the latest bar")


__all__ = ["BollingerBandsPatternAdapter"]
