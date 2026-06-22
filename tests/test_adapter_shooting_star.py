"""Tests for the Shooting Star adapter (sample candles only)."""
from services.strategy_service.adapters.shooting_star import ShootingStarAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "Shooting Star"
    assert sig.source_repo_url == REPO


def _set_last(candles, *, open_, high, low, close):
    idx = candles.index[-1]
    candles.loc[idx, ["open", "high", "low", "close"]] = [open_, high, low, close]
    return candles


def test_sell_on_shooting_star_after_uptrend(from_close):
    a = ShootingStarAdapter()
    candles = from_close([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 108.8])
    # Shooting star: small body near the low, long upper shadow.
    _set_last(candles, open_=109.0, high=112.0, low=108.7, close=108.8)
    sig = a.generate_signal(candles)
    _assert_well_formed(sig, SignalSide.SELL)
    assert sig.suggested_stop_loss > 109.0  # stop sits above the star's high


def test_buy_on_hammer_after_downtrend(from_close):
    a = ShootingStarAdapter()
    candles = from_close([110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 101.2])
    # Hammer: small body near the high, long lower shadow.
    _set_last(candles, open_=101.0, high=101.3, low=98.0, close=101.2)
    sig = a.generate_signal(candles)
    _assert_well_formed(sig, SignalSide.BUY)
    assert sig.suggested_stop_loss < 98.0  # stop sits below the hammer's low


def test_none_on_plain_candle(from_close):
    a = ShootingStarAdapter()
    sig = a.generate_signal(from_close([100.0] * 11))
    assert sig.side is SignalSide.NONE


def test_none_on_insufficient_data(from_close):
    a = ShootingStarAdapter()
    sig = a.generate_signal(from_close([100.0] * 5))
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = ShootingStarAdapter()
    candles = from_close([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 108.8])
    _set_last(candles, open_=109.0, high=112.0, low=108.7, close=108.8)
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
