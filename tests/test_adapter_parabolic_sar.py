"""Tests for the Parabolic SAR adapter (sample candles only)."""
from services.strategy_service.adapters.parabolic_sar import ParabolicSARAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "Parabolic SAR"
    assert sig.source_repo_url == REPO


def test_buy_on_flip_up(from_close):
    a = ParabolicSARAdapter()
    # Down-trend then a strong up bar → SAR flips below price.
    sig = a.generate_signal(from_close([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 99]))
    _assert_well_formed(sig, SignalSide.BUY)


def test_sell_on_flip_down(from_close):
    a = ParabolicSARAdapter()
    # Up-trend then a strong down bar → SAR flips above price.
    sig = a.generate_signal(from_close([91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 92]))
    _assert_well_formed(sig, SignalSide.SELL)


def test_none_when_trend_persists(from_close):
    a = ParabolicSARAdapter()
    sig = a.generate_signal(from_close(list(range(100, 112))))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = ParabolicSARAdapter()
    sig = a.generate_signal(from_close([100, 101, 102]))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = ParabolicSARAdapter()
    candles = from_close([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 99])
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
