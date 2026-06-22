"""Tests for the Bollinger Bands Pattern Recognition adapter (sample candles)."""
from services.strategy_service.adapters.bollinger_bands_pattern import (
    BollingerBandsPatternAdapter,
)
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "Bollinger Bands Pattern Recognition"
    assert sig.source_repo_url == REPO


def test_buy_on_lower_band_reentry(from_close):
    a = BollingerBandsPatternAdapter()
    # Tight noise, a sharp dip below the lower band, then re-entry inside.
    closes = [99.9, 100.1] * 14 + [96.0, 100.0]
    sig = a.generate_signal(from_close(closes))
    _assert_well_formed(sig, SignalSide.BUY)


def test_sell_on_upper_band_reentry(from_close):
    a = BollingerBandsPatternAdapter()
    closes = [99.9, 100.1] * 14 + [104.0, 100.0]
    sig = a.generate_signal(from_close(closes))
    _assert_well_formed(sig, SignalSide.SELL)


def test_none_inside_bands(from_close):
    a = BollingerBandsPatternAdapter()
    sig = a.generate_signal(from_close([99.9, 100.1] * 15))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = BollingerBandsPatternAdapter()
    sig = a.generate_signal(from_close([100.0] * 10))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = BollingerBandsPatternAdapter()
    candles = from_close([99.9, 100.1] * 14 + [96.0, 100.0])
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
