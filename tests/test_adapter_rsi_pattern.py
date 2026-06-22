"""Tests for the RSI Pattern Recognition adapter (sample candles only)."""
from services.strategy_service.adapters.rsi_pattern import RSIPatternAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "RSI Pattern Recognition"
    assert sig.source_repo_url == REPO


def test_buy_on_exit_from_oversold(from_close):
    a = RSIPatternAdapter()
    # Long decline (RSI deep oversold) then a large up bar crossing back above 30.
    closes = [100 - i for i in range(26)] + [100 - 25 + 8]
    sig = a.generate_signal(from_close(closes))
    _assert_well_formed(sig, SignalSide.BUY)


def test_sell_on_exit_from_overbought(from_close):
    a = RSIPatternAdapter()
    closes = [100 + i for i in range(26)] + [100 + 25 - 8]
    sig = a.generate_signal(from_close(closes))
    _assert_well_formed(sig, SignalSide.SELL)


def test_none_when_not_at_extreme(from_close):
    a = RSIPatternAdapter()
    sig = a.generate_signal(from_close([100.0] * 25))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = RSIPatternAdapter()
    sig = a.generate_signal(from_close([100.0] * 10))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = RSIPatternAdapter()
    closes = [100 - i for i in range(26)] + [100 - 25 + 8]
    candles = from_close(closes)
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
