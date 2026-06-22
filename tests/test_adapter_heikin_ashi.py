"""Tests for the Heikin-Ashi adapter (sample candles only)."""
from services.strategy_service.adapters.heikin_ashi import HeikinAshiAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "Heikin-Ashi"
    assert sig.source_repo_url == REPO


def test_buy_on_bullish_flip(from_close):
    a = HeikinAshiAdapter()
    # Down-move then a strong up bar → HA flips bullish.
    sig = a.generate_signal(from_close([110, 108, 106, 104, 102, 100, 110]))
    _assert_well_formed(sig, SignalSide.BUY)


def test_sell_on_bearish_flip(from_close):
    a = HeikinAshiAdapter()
    # Up-move then a strong down bar → HA flips bearish.
    sig = a.generate_signal(from_close([100, 102, 104, 106, 108, 110, 100]))
    _assert_well_formed(sig, SignalSide.SELL)


def test_none_when_no_flip(from_close):
    a = HeikinAshiAdapter()
    sig = a.generate_signal(from_close([100, 101, 102, 103, 104, 105, 106]))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = HeikinAshiAdapter()
    sig = a.generate_signal(from_close([100, 101]))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = HeikinAshiAdapter()
    candles = from_close([110, 108, 106, 104, 102, 100, 110])
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
