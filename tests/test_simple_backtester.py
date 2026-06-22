"""Tests for the simple backtester (deterministic, fake candles only)."""
import pandas as pd

from services.backtest_service.costs import ZERO_COST, CostModel
from services.backtest_service.simple_backtester import (
    BacktestConfig,
    BacktestSignal,
    ClosePolicy,
    Direction,
    SimpleBacktester,
)
from services.data_service.sample_data import generate_candles


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_candles(rows, *, symbol="EURUSD", timeframe="M15", start="2024-01-01",
                 spread=10, freq="15min"):
    """Build a minimal candle frame from (open, high, low, close) rows."""
    n = len(rows)
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq=freq),
        "open": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "spread": [spread] * n,
        "symbol": [symbol] * n,
        "timeframe": [timeframe] * n,
    })


class OneShot:
    """Emit a single signal the first time it is called, then abstain."""

    def __init__(self, direction, stop, take=None, size=None):
        self.sig = BacktestSignal(direction, stop, take, size)

    def __call__(self, window):
        if len(window) == 1:
            return self.sig
        return BacktestSignal(Direction.NONE)


# --------------------------------------------------------------------------- #
# Entry timing / look-ahead
# --------------------------------------------------------------------------- #
def test_entry_is_next_bar_open_no_lookahead():
    rows = [
        (1.1000, 1.1005, 1.0995, 1.1000),
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.1050, 1.1150, 1.1040, 1.1100),
        (1.1100, 1.1110, 1.1090, 1.1100),
    ]
    bt = SimpleBacktester()
    rep = bt.run(make_candles(rows), OneShot(Direction.BUY, 1.0950, 1.1100))
    assert len(rep.trades) == 1
    # Signal computed at bar 0; entry must be at bar 1's open.
    assert rep.trades[0].entry_index == 1


# --------------------------------------------------------------------------- #
# Take profit / stop loss
# --------------------------------------------------------------------------- #
def test_long_take_profit_is_a_win():
    rows = [
        (1.1000, 1.1005, 1.0995, 1.1000),
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.1050, 1.1150, 1.1040, 1.1100),
        (1.1100, 1.1110, 1.1090, 1.1100),
    ]
    rep = SimpleBacktester().run(make_candles(rows), OneShot(Direction.BUY, 1.0950, 1.1100))
    t = rep.trades[0]
    assert t.exit_reason == "take_profit"
    assert t.pnl > 0 and t.r_multiple > 0


def test_long_stop_loss_is_a_loss():
    rows = [
        (1.1000, 1.1005, 1.0995, 1.1000),
        (1.1000, 1.1010, 1.0990, 1.1005),
        (1.0960, 1.0965, 1.0940, 1.0950),
        (1.0950, 1.0955, 1.0945, 1.0950),
    ]
    rep = SimpleBacktester().run(make_candles(rows), OneShot(Direction.BUY, 1.0950, 1.1100))
    t = rep.trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.pnl < 0 and t.r_multiple < 0


def test_short_take_profit_is_a_win():
    rows = [
        (1.1000, 1.1005, 1.0995, 1.1000),
        (1.1000, 1.1010, 1.0990, 1.1000),
        (1.0950, 1.0960, 1.0890, 1.0900),
        (1.0900, 1.0910, 1.0890, 1.0900),
    ]
    rep = SimpleBacktester().run(make_candles(rows), OneShot(Direction.SELL, 1.1050, 1.0900))
    t = rep.trades[0]
    assert t.direction is Direction.SELL
    assert t.exit_reason == "take_profit" and t.pnl > 0


# --------------------------------------------------------------------------- #
# Stop loss is mandatory  (Done-when criterion)
# --------------------------------------------------------------------------- #
def test_signal_without_stop_loss_is_rejected():
    rows = [(1.1000, 1.1010, 1.0990, 1.1000)] * 5
    bt = SimpleBacktester()
    rep = bt.run(make_candles(rows), OneShot(Direction.BUY, stop=None, take=1.1100))
    assert rep.trades == []
    assert rep.rejected_signals >= 1
    assert rep.rejected_no_stop >= 1


def test_signal_with_misplaced_stop_is_rejected():
    rows = [(1.1000, 1.1010, 1.0990, 1.1000)] * 5
    # BUY but stop is ABOVE the reference price -> ill-placed, rejected.
    rep = SimpleBacktester().run(make_candles(rows), OneShot(Direction.BUY, stop=1.2000))
    assert rep.trades == []
    assert rep.rejected_signals >= 1
    assert rep.rejected_no_stop == 0  # had a stop, just in the wrong place


# --------------------------------------------------------------------------- #
# Costs  (Done-when criterion)
# --------------------------------------------------------------------------- #
def _flat_roundtrip(cost_model):
    rows = [(1.1000, 1.1005, 1.0995, 1.1000)] * 4
    cfg = BacktestConfig(cost_model=cost_model, max_holding_bars=1)
    return SimpleBacktester(cfg).run(
        make_candles(rows), OneShot(Direction.BUY, 1.0950)
    ).trades[0]


def test_costs_make_a_flat_roundtrip_unprofitable():
    frictionless = _flat_roundtrip(ZERO_COST)
    with_costs = _flat_roundtrip(CostModel(slippage_points=1.0, commission=0.0,
                                           spread_fraction=1.0))
    assert frictionless.pnl == 0.0          # no friction → break-even
    assert with_costs.pnl < 0.0             # spread + slippage cost money
    assert with_costs.exit_reason == "max_holding"


def test_commission_reduces_pnl():
    no_comm = _flat_roundtrip(CostModel(slippage_points=0.0, spread_fraction=0.0))
    comm = _flat_roundtrip(CostModel(slippage_points=0.0, spread_fraction=0.0,
                                     commission=0.5))
    assert round(no_comm.pnl - comm.pnl, 6) == 0.5


# --------------------------------------------------------------------------- #
# Max holding period
# --------------------------------------------------------------------------- #
def test_max_holding_period_forces_exit():
    rows = [(1.1000, 1.1005, 1.0995, 1.1000)] * 6
    cfg = BacktestConfig(max_holding_bars=2)
    rep = SimpleBacktester(cfg).run(make_candles(rows), OneShot(Direction.BUY, 1.0950))
    t = rep.trades[0]
    assert t.exit_reason == "max_holding"
    assert t.bars_held == 2


# --------------------------------------------------------------------------- #
# End-of-day / session close
# --------------------------------------------------------------------------- #
def test_end_of_day_close():
    rows = [
        (1.1000, 1.1005, 1.0995, 1.1000),
        (1.1000, 1.1010, 1.0990, 1.1003),
        (1.1003, 1.1008, 1.0998, 1.1003),
    ]
    candles = make_candles(rows, start="2024-01-01 23:30")  # bar1 is last of Jan-1
    cfg = BacktestConfig(close_policy=ClosePolicy.END_OF_DAY)
    rep = SimpleBacktester(cfg).run(candles, OneShot(Direction.BUY, 1.0950))
    assert rep.trades[0].exit_reason == "end_of_day"


def test_session_close():
    rows = [
        (1.1000, 1.1005, 1.0995, 1.1000),
        (1.1000, 1.1010, 1.0990, 1.1003),
        (1.1003, 1.1008, 1.0998, 1.1003),
    ]
    # 07:30 Asia, 07:45 Asia (last Asia bar), 08:00 overlap -> boundary at bar 1.
    candles = make_candles(rows, start="2024-01-01 07:30")
    cfg = BacktestConfig(close_policy=ClosePolicy.SESSION)
    rep = SimpleBacktester(cfg).run(candles, OneShot(Direction.BUY, 1.0950))
    assert rep.trades[0].exit_reason == "session"


# --------------------------------------------------------------------------- #
# Equity curve, trade list, determinism, fake data
# --------------------------------------------------------------------------- #
def _momentum(window):
    if len(window) < 2:
        return BacktestSignal(Direction.NONE)
    closes = window["close"].to_numpy()
    if closes[-1] > closes[-2]:
        last = float(closes[-1])
        return BacktestSignal(Direction.BUY, stop_loss=last * 0.99,
                              take_profit=last * 1.01)
    return BacktestSignal(Direction.NONE)


def test_runs_on_fake_candles_with_equity_and_trades():
    candles = generate_candles("EURUSD", "M15", n=300, seed=5)
    rep = SimpleBacktester().run(candles, _momentum)
    assert len(rep.equity_curve) == rep.n_bars == 300
    assert isinstance(rep.trades, list)
    assert rep.metrics.total_trades == len(rep.trades)


def test_is_deterministic():
    candles = generate_candles("EURUSD", "M15", n=200, seed=9)
    bt = SimpleBacktester()
    a = bt.run(candles, _momentum)
    b = bt.run(candles.copy(), _momentum)
    assert a.equity_curve == b.equity_curve
    assert [t.pnl for t in a.trades] == [t.pnl for t in b.trades]


def test_empty_frame_raises():
    bt = SimpleBacktester()
    try:
        bt.run(make_candles([]).iloc[0:0], OneShot(Direction.BUY, 1.0))
        assert False, "expected ValueError"
    except ValueError:
        pass
