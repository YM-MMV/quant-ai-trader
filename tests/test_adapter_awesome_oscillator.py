"""Tests for the Awesome Oscillator adapter (sample candles only)."""
from services.strategy_service.adapters.awesome_oscillator import AwesomeOscillatorAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "Awesome Oscillator"
    assert sig.source_repo_url == REPO


def test_buy_on_zero_cross_up(from_close):
    a = AwesomeOscillatorAdapter()
    sig = a.generate_signal(from_close([100.0] * 45 + [105.0]))
    _assert_well_formed(sig, SignalSide.BUY)
    assert sig.suggested_stop_loss < 105.0 < sig.suggested_take_profit


def test_sell_on_zero_cross_down(from_close):
    a = AwesomeOscillatorAdapter()
    sig = a.generate_signal(from_close([100.0] * 45 + [95.0]))
    _assert_well_formed(sig, SignalSide.SELL)
    assert sig.suggested_take_profit < 95.0 < sig.suggested_stop_loss


def test_none_without_fresh_cross(from_close):
    a = AwesomeOscillatorAdapter()
    sig = a.generate_signal(from_close([100.0] * 46))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = AwesomeOscillatorAdapter()
    sig = a.generate_signal(from_close([100.0] * 10))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = AwesomeOscillatorAdapter()
    candles = from_close([100.0] * 45 + [105.0])
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
