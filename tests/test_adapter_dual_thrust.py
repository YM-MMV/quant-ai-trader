"""Tests for the Dual Thrust adapter (sample candles only)."""
from services.strategy_service.adapters.dual_thrust import DualThrustAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "Dual Thrust"
    assert sig.source_repo_url == REPO


def test_buy_on_upside_break(from_close):
    a = DualThrustAdapter()
    sig = a.generate_signal(from_close([99.8, 100.2] * 13 + [101.0]))
    _assert_well_formed(sig, SignalSide.BUY)
    assert sig.suggested_stop_loss < 101.0 < sig.suggested_take_profit


def test_sell_on_downside_break(from_close):
    a = DualThrustAdapter()
    sig = a.generate_signal(from_close([100.2, 99.8] * 13 + [99.0]))
    _assert_well_formed(sig, SignalSide.SELL)
    assert sig.suggested_take_profit < 99.0 < sig.suggested_stop_loss


def test_none_inside_band(from_close):
    a = DualThrustAdapter()
    sig = a.generate_signal(from_close([100.0] * 30))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = DualThrustAdapter()
    sig = a.generate_signal(from_close([100.0] * 10))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = DualThrustAdapter()
    candles = from_close([99.8, 100.2] * 13 + [101.0])
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
