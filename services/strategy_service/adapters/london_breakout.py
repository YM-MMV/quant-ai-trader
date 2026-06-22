"""London Breakout adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` London Breakout: the
quiet Asia session sets a range; when London opens, price often breaks out of
that range and runs. We BUY a fresh break above the Asia-session high and SELL a
fresh break below the Asia-session low, but only while the current bar is in the
London session.

Clean re-implementation notes:
* Sessions come from the project's causal
  :func:`services.data_service.sessions.label_sessions` (UTC-based, no
  look-ahead). The "Asia range" is taken from this UTC day's Asia bars only.
* "Fresh" break = previous close inside the range, current close outside it —
  so we don't re-fire every London bar.
* Cost-aware: the break must clear round-trip spread + slippage
  (:func:`_common.clears_cost`), otherwise we abstain.
* SL is the opposite side of the Asia range (a structural stop); TP scales with
  the range size.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from services.data_service.sessions import ASIA, LONDON, label_sessions
from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata,
    AdapterSignal,
    SignalSide,
    StrategyAdapter,
)


class LondonBreakoutAdapter(StrategyAdapter):
    """Trade the London-open breakout of the Asia-session range."""

    VERSION = "1.0.0"

    def __init__(self, tp_range_mult: float = 1.0) -> None:
        self.tp_range_mult = tp_range_mult

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="london_breakout",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="London Breakout",
            category="breakout",
            description="Breakout of the Asia-session range at the London open.",
            supported_symbols=None,
            # Intraday only — the session structure is meaningless on D1.
            supported_timeframes=["M5", "M15", "M30", "H1"],
            min_candles=20,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        if "timestamp" not in candles.columns:
            return self.none_signal("London Breakout needs a timestamp column")

        ts = pd.to_datetime(candles["timestamp"]).reset_index(drop=True)
        sessions = label_sessions(ts).reset_index(drop=True)
        close = candles["close"].astype(float).reset_index(drop=True)

        if sessions.iloc[-1] != LONDON:
            return self.none_signal("current bar is not in the London session")

        # Asia range for *this* UTC day (bars before the current London bar).
        today = ts.iloc[-1].normalize()
        asia_mask = (sessions == ASIA) & (ts.dt.normalize() == today)
        if not asia_mask.any():
            return self.none_signal("no Asia-session bars for the current day")

        asia_high = float(candles["high"].astype(float).reset_index(drop=True)[asia_mask].max())
        asia_low = float(candles["low"].astype(float).reset_index(drop=True)[asia_mask].min())
        rng = asia_high - asia_low
        if rng <= 0:
            return self.none_signal("degenerate Asia range")

        prev, now = float(close.iloc[-2]), float(close.iloc[-1])
        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)

        # Fresh break above the Asia high → BUY.
        if prev <= asia_high < now and c.clears_cost(now - asia_high, candles):
            conf = c.clamp_confidence(0.45 + 0.5 * min((now - asia_high) / rng, 1.0))
            sl = c.positive(asia_low, like=now)
            tp = c.positive(now + self.tp_range_mult * rng, like=now)
            return self.make_signal(
                SignalSide.BUY, conf,
                "London-open break above the Asia-session high",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["stop at opposite (low) side of Asia range"],
                symbol=c.last_symbol(candles) or None,
            )
        # Fresh break below the Asia low → SELL.
        if prev >= asia_low > now and c.clears_cost(asia_low - now, candles):
            conf = c.clamp_confidence(0.45 + 0.5 * min((asia_low - now) / rng, 1.0))
            sl = c.positive(asia_high, like=now)
            tp = c.positive(now - self.tp_range_mult * rng, like=now)
            return self.make_signal(
                SignalSide.SELL, conf,
                "London-open break below the Asia-session low",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["stop at opposite (high) side of Asia range"],
                symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no fresh London breakout of the Asia range")


__all__ = ["LondonBreakoutAdapter"]
