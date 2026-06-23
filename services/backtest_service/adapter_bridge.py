"""Bridge a live :class:`StrategyAdapter` into a backtester ``Strategy`` callable.

Adapters speak :class:`~services.strategy_service.base.AdapterSignal` (BUY/SELL/
NONE plus ATR-scaled SL/TP hints); the :class:`SimpleBacktester` speaks
:class:`~services.backtest_service.simple_backtester.BacktestSignal`. This adapter
maps one to the other so the *same* adapter that proposes live signals is the one
being backtested and validated — no separate, drift-prone backtest reimplementation.

Causality is preserved: the backtester hands the strategy only the candle window
up to the current bar, and the adapter's own :meth:`generate_signal` template
fails safe to ``NONE`` on bad/insufficient data.
"""
from __future__ import annotations

from typing import Any, Optional

from services.backtest_service.simple_backtester import BacktestSignal, Direction
from services.strategy_service.base import SignalSide, StrategyAdapter

_SIDE_TO_DIRECTION = {SignalSide.BUY: Direction.BUY, SignalSide.SELL: Direction.SELL}


def adapter_to_backtest_strategy(
    adapter: StrategyAdapter,
    *,
    features: Any = None,
    kronos_prediction: Optional[Any] = None,
):
    """Return a ``Strategy`` closure that runs ``adapter`` over each candle window.

    ``features``/``kronos_prediction`` are passed through to the adapter on every
    call (most technical adapters ignore them and compute from candles alone).
    A ``NONE`` signal — or any signal missing SL — becomes a no-op the backtester
    will simply skip/reject, exactly as in live trading.
    """

    def strategy(window) -> BacktestSignal:
        signal = adapter.generate_signal(window, features, kronos_prediction)
        direction = _SIDE_TO_DIRECTION.get(signal.side)
        if direction is None:
            return BacktestSignal(Direction.NONE)
        return BacktestSignal(
            direction=direction,
            stop_loss=signal.suggested_stop_loss,
            take_profit=signal.suggested_take_profit,
            reason=signal.reason,
        )

    return strategy


__all__ = ["adapter_to_backtest_strategy"]
