"""Shooting Star adapter.

Ported idea (not code) from ``je-suis-tm/quant-trading`` Shooting Star: a
single-candle reversal pattern. A *shooting star* — a small body near the bar's
low with a long upper shadow and little lower shadow, printed after an up-move —
warns of a bearish reversal (**SELL**). Its mirror, a *hammer* (small body near
the high, long lower shadow) after a down-move, warns of a bullish reversal
(**BUY**).

A short trend filter (price direction over the prior few bars) ensures the
pattern appears at a meaningful turning point rather than mid-range noise.
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


class ShootingStarAdapter(StrategyAdapter):
    """Single-candle shooting-star (SELL) / hammer (BUY) reversal."""

    VERSION = "1.0.0"

    def __init__(
        self,
        trend_lookback: int = 5,
        max_body_ratio: float = 0.35,
        min_shadow_ratio: float = 0.5,
        shadow_to_body: float = 2.0,
    ) -> None:
        self.trend_lookback = trend_lookback
        self.max_body_ratio = max_body_ratio
        self.min_shadow_ratio = min_shadow_ratio
        self.shadow_to_body = shadow_to_body

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="shooting_star",
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="Shooting Star",
            category="candlestick_pattern",
            description="Shooting-star / hammer single-candle reversal.",
            supported_symbols=None,
            supported_timeframes=["M15", "M30", "H1", "H4", "D1"],
            min_candles=self.trend_lookback + 5,
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        o = float(candles["open"].astype(float).iloc[-1])
        h = float(candles["high"].astype(float).iloc[-1])
        low = float(candles["low"].astype(float).iloc[-1])
        cl = float(candles["close"].astype(float).iloc[-1])
        close = candles["close"].astype(float)

        rng = h - low
        if rng <= 0:
            return self.none_signal("zero-range bar; no shooting-star pattern")

        body = abs(cl - o)
        upper = h - max(o, cl)
        lower = min(o, cl) - low
        body_ratio = body / rng
        # Short prior trend (the bar before the candle in question).
        ref = float(close.iloc[-1 - self.trend_lookback])
        uptrend = cl > ref
        downtrend = cl < ref

        atr = c.atr_value(candles)
        notes = c.cost_notes(candles)
        buffer = (atr or rng) * 0.1

        is_star = (
            uptrend
            and body_ratio <= self.max_body_ratio
            and upper >= self.shadow_to_body * body
            and upper >= self.min_shadow_ratio * rng
            and lower <= 0.2 * rng
        )
        is_hammer = (
            downtrend
            and body_ratio <= self.max_body_ratio
            and lower >= self.shadow_to_body * body
            and lower >= self.min_shadow_ratio * rng
            and upper <= 0.2 * rng
        )

        if is_star:
            conf = c.clamp_confidence(0.4 + 0.5 * min(upper / rng, 1.0))
            sl = c.positive(h + buffer, like=cl)
            _, tp = c.sl_tp_from_atr(SignalSide.SELL, cl, atr)
            return self.make_signal(
                SignalSide.SELL, conf,
                "shooting star after an up-move (bearish reversal)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["SL above the star's high"],
                symbol=c.last_symbol(candles) or None,
            )
        if is_hammer:
            conf = c.clamp_confidence(0.4 + 0.5 * min(lower / rng, 1.0))
            sl = c.positive(low - buffer, like=cl)
            _, tp = c.sl_tp_from_atr(SignalSide.BUY, cl, atr)
            return self.make_signal(
                SignalSide.BUY, conf,
                "hammer after a down-move (bullish reversal)",
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=notes + ["SL below the hammer's low"],
                symbol=c.last_symbol(candles) or None,
            )
        return self.none_signal("no shooting-star / hammer pattern at the latest bar")


__all__ = ["ShootingStarAdapter"]
