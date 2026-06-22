"""Tests for the London Breakout adapter (sample candles only).

Frames start Monday 2024-01-01 00:00 UTC on M15, so bars walk through the Asia
session (00:00–08:00), the overlap (08:00–09:00), and into London (from 09:00).
"""
from datetime import datetime

from services.strategy_service.adapters.london_breakout import LondonBreakoutAdapter
from services.strategy_service.base import SignalSide

REPO = "https://github.com/je-suis-tm/quant-trading"
MONDAY = datetime(2024, 1, 1)  # a Monday → FX week is open


def _assert_well_formed(sig, side):
    assert sig.side is side
    assert sig.is_actionable and 0.0 < sig.confidence <= 1.0 and sig.reason
    assert sig.suggested_stop_loss > 0 and sig.suggested_take_profit > 0
    assert sig.source_strategy == "London Breakout"
    assert sig.source_repo_url == REPO


def test_buy_on_break_above_asia_high(from_close):
    a = LondonBreakoutAdapter()
    # 37 M15 bars: flat through Asia/overlap, last (09:00 London) breaks up.
    closes = [1.1000] * 36 + [1.1050]
    candles = from_close(closes, timeframe="M15", start=MONDAY)
    sig = a.generate_signal(candles)
    _assert_well_formed(sig, SignalSide.BUY)


def test_sell_on_break_below_asia_low(from_close):
    a = LondonBreakoutAdapter()
    closes = [1.1000] * 36 + [1.0950]
    candles = from_close(closes, timeframe="M15", start=MONDAY)
    sig = a.generate_signal(candles)
    _assert_well_formed(sig, SignalSide.SELL)


def test_none_when_no_breakout(from_close):
    a = LondonBreakoutAdapter()
    candles = from_close([1.1000] * 37, timeframe="M15", start=MONDAY)
    sig = a.generate_signal(candles)
    assert sig.side is SignalSide.NONE


def test_none_when_not_in_london_session(from_close):
    a = LondonBreakoutAdapter()
    # 24 Asia-only bars (00:00–05:45) — never reaches London.
    candles = from_close([1.1000] * 24, timeframe="M15", start=MONDAY)
    sig = a.generate_signal(candles)
    assert sig.side is SignalSide.NONE
    assert "London session" in sig.reason


def test_none_on_insufficient_data(from_close):
    a = LondonBreakoutAdapter()
    candles = from_close([1.1000] * 10, timeframe="M15", start=MONDAY)
    sig = a.generate_signal(candles)
    assert sig.side is SignalSide.NONE
    assert "insufficient inputs" in sig.reason


def test_deterministic(from_close):
    a = LondonBreakoutAdapter()
    candles = from_close([1.1000] * 36 + [1.1050], timeframe="M15", start=MONDAY)
    assert a.generate_signal(candles) == a.generate_signal(candles.copy())
