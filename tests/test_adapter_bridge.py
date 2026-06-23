"""Tests for the adapter -> backtester strategy bridge."""
from services.backtest_service.adapter_bridge import adapter_to_backtest_strategy
from services.backtest_service.simple_backtester import (
    Direction,
    SimpleBacktester,
)
from services.data_service.sample_data import generate_candles
from services.strategy_service.adapters.macd_oscillator import MACDOscillatorAdapter
from services.strategy_service.base import (
    AdapterMetadata,
    SignalSide,
    StrategyAdapter,
)


class _AlwaysBuy(StrategyAdapter):
    """Tiny adapter that always proposes a BUY with valid SL/TP."""

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(name="always_buy", version="1.0.0", min_candles=1)

    def _compute_signal(self, candles, features, kronos_prediction):
        price = float(candles["close"].iloc[-1])
        return self.make_signal(
            SignalSide.BUY, 0.9, "always",
            suggested_stop_loss=price * 0.99, suggested_take_profit=price * 1.01,
        )


def test_bridge_maps_buy_to_backtest_buy_with_levels():
    candles = generate_candles("EURUSD", "H1", n=60, seed=1)
    strat = adapter_to_backtest_strategy(_AlwaysBuy())
    sig = strat(candles)
    assert sig.direction is Direction.BUY
    assert sig.stop_loss is not None and sig.take_profit is not None
    assert sig.stop_loss < float(candles["close"].iloc[-1]) < sig.take_profit


def test_bridge_maps_none_to_backtest_none():
    # Too-short window → adapter abstains (insufficient inputs) → NONE.
    candles = generate_candles("EURUSD", "H1", n=3, seed=1)
    strat = adapter_to_backtest_strategy(MACDOscillatorAdapter())
    assert strat(candles).direction is Direction.NONE


def test_bridged_adapter_runs_through_the_backtester():
    candles = generate_candles("EURUSD", "H1", n=200, seed=3)
    strat = adapter_to_backtest_strategy(MACDOscillatorAdapter())
    report = SimpleBacktester().run(candles, strat)
    assert report.n_bars == 200
    # MACD crosses on this series → at least one round-trip trade.
    assert len(report.trades) >= 1
