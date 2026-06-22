"""Tests for performance monitoring & degradation detection (M23).

Trades are mostly lightweight duck-typed records (a `SimpleNamespace` with the
same fields as a PaperTrade), with one test proving real PaperTrade objects work
too. Everything is deterministic — no AI, no MT5, no network.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from services.monitoring_service.performance_monitor import PerformanceMonitor
from services.monitoring_service.degradation import (
    DegradationThresholds,
    MonitorStatus,
    StrategyMonitor,
    StrategyPausedError,
    evaluate_degradation,
)

T0 = datetime(2024, 1, 1, 10, 0)  # 10:00 UTC → London-ish session


def trade(
    pnl,
    *,
    strategy="s1",
    symbol="EURUSD",
    side="buy",
    minutes=0,
    expected_price=None,
    entry=None,
    kdir=None,
    spread=None,
):
    intent = SimpleNamespace(price=expected_price, strategy_id=strategy)
    decision = SimpleNamespace(intent=intent, approved=True, checks={})
    kp = SimpleNamespace(direction=kdir) if kdir is not None else None
    return SimpleNamespace(
        pnl=pnl, strategy_name=strategy, symbol=symbol, side=side,
        timestamp=T0 + timedelta(minutes=minutes), entry=entry,
        risk_decision=decision, kronos_prediction=kp,
        features_snapshot=None, spread_at_entry=spread,
    )


def losers(n, *, strategy="s1", each=-50.0):
    return [trade(each, strategy=strategy, minutes=i) for i in range(n)]


# --------------------------------------------------------------------------- #
# Basic metrics
# --------------------------------------------------------------------------- #
def test_empty_trades():
    m = PerformanceMonitor().evaluate([])
    assert m.n_closed == 0
    assert m.win_rate == 0.0
    assert m.profit_factor is None


def test_open_and_rejected_trades_are_ignored():
    trades = [trade(None), trade(None, side="sell"), trade(100.0)]
    m = PerformanceMonitor().evaluate(trades)
    assert m.n_closed == 1
    assert m.wins == 1


def test_win_rate_and_profit_factor():
    trades = [trade(100), trade(-50), trade(100), trade(-50)]
    m = PerformanceMonitor().evaluate(trades)
    assert m.wins == 2 and m.losses == 2
    assert m.win_rate == 0.5
    assert m.gross_profit == 200.0
    assert m.gross_loss == 100.0
    assert m.profit_factor == 2.0
    assert m.net_pnl == 100.0


def test_profit_factor_none_without_losses():
    m = PerformanceMonitor().evaluate([trade(10), trade(20)])
    assert m.profit_factor is None


def test_signal_hit_rate_excludes_breakeven():
    trades = [trade(100), trade(-50), trade(0.0)]  # 1 win, 1 loss, 1 breakeven
    m = PerformanceMonitor().evaluate(trades)
    assert m.breakeven == 1
    assert m.signal_hit_rate == 0.5  # wins / (wins+losses)
    assert m.win_rate == round(1 / 3, 6)  # wins / all closed


def test_losing_streak():
    # win, then three losses in a row at the end.
    trades = [trade(100, minutes=0), trade(-10, minutes=1),
              trade(-10, minutes=2), trade(-10, minutes=3)]
    m = PerformanceMonitor().evaluate(trades)
    assert m.current_losing_streak == 3
    assert m.max_losing_streak == 3


def test_max_streak_resets_on_win():
    trades = [trade(-10, minutes=0), trade(-10, minutes=1), trade(50, minutes=2),
              trade(-10, minutes=3)]
    m = PerformanceMonitor().evaluate(trades)
    assert m.max_losing_streak == 2
    assert m.current_losing_streak == 1


def test_drawdown():
    mon = PerformanceMonitor(initial_equity=1000.0)
    m = mon.evaluate([trade(100, minutes=0), trade(-300, minutes=1)])
    # peak equity 1100, trough 800 → dd 300, pct 300/1100.
    assert m.max_drawdown == 300.0
    assert m.max_drawdown_pct == pytest.approx(300 / 1100, abs=1e-6)


# --------------------------------------------------------------------------- #
# Rolling window
# --------------------------------------------------------------------------- #
def test_rolling_window_limits_to_recent():
    trades = [trade(-100, minutes=0), trade(-100, minutes=1),
              trade(50, minutes=2), trade(50, minutes=3)]
    m = PerformanceMonitor(window=2).evaluate(trades)
    assert m.n_closed == 2
    assert m.wins == 2 and m.losses == 0  # only the last two count


def test_window_orders_by_timestamp():
    # Provide out of order; window should take the latest by timestamp.
    trades = [trade(50, minutes=5), trade(-100, minutes=0)]
    m = PerformanceMonitor(window=1).evaluate(trades)
    assert m.wins == 1  # minutes=5 is the most recent


# --------------------------------------------------------------------------- #
# Execution quality: slippage & spread
# --------------------------------------------------------------------------- #
def test_slippage_points_buy_adverse():
    # buy filled 1 pip worse than expected: 1.10010 vs 1.10000, EURUSD point 1e-5.
    t = trade(10, side="buy", expected_price=1.10000, entry=1.10010)
    m = PerformanceMonitor().evaluate([t])
    assert m.slippage_samples == 1
    assert m.avg_slippage_points == pytest.approx(10.0, abs=1e-6)


def test_slippage_sell_sign():
    # sell filled better than expected (higher) → favourable (negative slippage).
    t = trade(10, side="sell", expected_price=1.10000, entry=1.10010)
    m = PerformanceMonitor().evaluate([t])
    assert m.avg_slippage_points == pytest.approx(-10.0, abs=1e-6)


def test_no_slippage_when_no_expected_price():
    m = PerformanceMonitor().evaluate([trade(10, entry=1.10010)])
    assert m.slippage_samples == 0
    assert m.avg_slippage_points is None


def test_spread_at_entry_average():
    m = PerformanceMonitor().evaluate([trade(10, spread=12.0), trade(-5, spread=8.0)])
    assert m.spread_samples == 2
    assert m.avg_spread_at_entry == 10.0


def test_spread_from_feature_snapshot():
    t = trade(10)
    t.features_snapshot = SimpleNamespace(features={"spread": 15.0})
    m = PerformanceMonitor().evaluate([t])
    assert m.avg_spread_at_entry == 15.0


# --------------------------------------------------------------------------- #
# Kronos prediction accuracy
# --------------------------------------------------------------------------- #
def test_kronos_accuracy():
    trades = [
        trade(100, side="buy", kdir="up"),    # predicted up, went up → correct
        trade(-50, side="buy", kdir="up"),    # predicted up, went down → wrong
        trade(100, side="sell", kdir="down"),  # predicted down, went down → correct
        trade(0.0, side="buy", kdir="up"),     # breakeven → ignored
        trade(100, side="buy"),                # no prediction → ignored
    ]
    m = PerformanceMonitor().evaluate(trades)
    assert m.kronos_samples == 3
    assert m.kronos_accuracy == pytest.approx(2 / 3, abs=1e-6)


# --------------------------------------------------------------------------- #
# Breakdowns
# --------------------------------------------------------------------------- #
def test_pnl_breakdowns():
    trades = [
        trade(100, strategy="a", symbol="EURUSD"),
        trade(-30, strategy="b", symbol="GBPUSD"),
        trade(50, strategy="a", symbol="EURUSD"),
    ]
    mon = PerformanceMonitor()
    assert mon.pnl_by_strategy(trades) == {"a": 150.0, "b": -30.0}
    assert mon.pnl_by_symbol(trades) == {"EURUSD": 150.0, "GBPUSD": -30.0}
    sessions = mon.pnl_by_session(trades)
    assert sum(sessions.values()) == 120.0  # 10:00 UTC bucket(s)


def test_metrics_by_strategy():
    trades = [trade(100, strategy="a"), trade(-50, strategy="a"),
              trade(-20, strategy="b")]
    by = PerformanceMonitor().metrics_by_strategy(trades)
    assert set(by) == {"a", "b"}
    assert by["a"].n_closed == 2
    assert by["b"].losses == 1


# --------------------------------------------------------------------------- #
# Degradation rules
# --------------------------------------------------------------------------- #
def test_insufficient_trades_not_evaluated():
    m = PerformanceMonitor().evaluate(losers(3))  # all losers but too few
    report = evaluate_degradation(m, DegradationThresholds(min_trades=10))
    assert report.evaluated is False
    assert report.status is MonitorStatus.ACTIVE


def test_low_win_rate_pauses():
    th = DegradationThresholds(min_trades=5, min_win_rate=0.4, max_losing_streak=99)
    m = PerformanceMonitor().evaluate(losers(6))
    report = evaluate_degradation(m, th, strategy_id="s1")
    assert report.is_paused
    assert any("win_rate" in b for b in report.breaches)


def test_losing_streak_pauses():
    th = DegradationThresholds(min_trades=3, min_win_rate=0.0,
                               min_profit_factor=0.0, max_drawdown_pct=10.0,
                               max_losing_streak=4)
    m = PerformanceMonitor().evaluate(losers(5))
    report = evaluate_degradation(m, th)
    assert report.is_paused
    assert any("losing_streak" in b for b in report.breaches)


def test_drawdown_pauses():
    th = DegradationThresholds(min_trades=2, min_win_rate=0.0,
                               min_profit_factor=0.0, max_losing_streak=99,
                               max_drawdown_pct=0.25)
    m = PerformanceMonitor(initial_equity=1000.0).evaluate(
        [trade(0, minutes=0), trade(-300, minutes=1)])
    report = evaluate_degradation(m, th)
    assert report.is_paused
    assert any("drawdown" in b for b in report.breaches)


def test_healthy_strategy_active():
    th = DegradationThresholds(min_trades=4)
    trades = [trade(100, minutes=0), trade(80, minutes=1),
              trade(-20, minutes=2), trade(90, minutes=3)]
    m = PerformanceMonitor().evaluate(trades)
    report = evaluate_degradation(m, th)
    assert report.status is MonitorStatus.ACTIVE
    assert report.breaches == []


def test_optional_kronos_threshold():
    th = DegradationThresholds(min_trades=3, min_win_rate=0.0,
                               min_profit_factor=0.0, max_losing_streak=99,
                               max_drawdown_pct=10.0, min_kronos_accuracy=0.8)
    trades = [trade(100, side="buy", kdir="up", minutes=0),
              trade(-50, side="buy", kdir="up", minutes=1),
              trade(-50, side="buy", kdir="up", minutes=2)]  # 1/3 correct
    m = PerformanceMonitor().evaluate(trades)
    report = evaluate_degradation(m, th)
    assert report.is_paused
    assert any("kronos" in b for b in report.breaches)


# --------------------------------------------------------------------------- #
# StrategyMonitor: the paused-strategy gate
# --------------------------------------------------------------------------- #
def test_monitor_pauses_degrading_strategy():
    mon = StrategyMonitor(thresholds=DegradationThresholds(min_trades=5, min_win_rate=0.4))
    mon.update(losers(6, strategy="bad"))
    assert mon.is_paused("bad") is True
    assert mon.can_trade("bad") is False
    assert "bad" in mon.paused_strategies()


def test_paused_strategy_cannot_trade():
    mon = StrategyMonitor(thresholds=DegradationThresholds(min_trades=5))
    mon.update(losers(6, strategy="bad"))
    with pytest.raises(StrategyPausedError):
        mon.assert_can_trade("bad")


def test_active_strategy_can_trade():
    mon = StrategyMonitor(thresholds=DegradationThresholds(min_trades=4))
    trades = [trade(100, strategy="good", minutes=i) for i in range(5)]
    mon.update(trades)
    assert mon.can_trade("good") is True
    mon.assert_can_trade("good")  # does not raise


def test_unknown_strategy_defaults_active():
    mon = StrategyMonitor()
    assert mon.can_trade("never-seen") is True


def test_pause_is_sticky():
    mon = StrategyMonitor(thresholds=DegradationThresholds(min_trades=5, min_win_rate=0.4))
    mon.update(losers(6, strategy="bad"))
    assert mon.is_paused("bad")
    # A later batch of winners must NOT silently un-pause it.
    mon.update([trade(100, strategy="bad", minutes=100 + i) for i in range(10)])
    assert mon.is_paused("bad")


def test_manual_pause_and_resume():
    mon = StrategyMonitor()
    mon.pause("s1", reason="manual")
    assert mon.is_paused("s1")
    with pytest.raises(StrategyPausedError):
        mon.assert_can_trade("s1")
    mon.resume("s1")
    assert mon.can_trade("s1")


# --------------------------------------------------------------------------- #
# Works on real PaperTrade records
# --------------------------------------------------------------------------- #
def test_evaluates_real_paper_trades():
    from services.execution_service.trade_log import PaperTrade
    from services.models import (
        OrderIntent, RiskDecision, Side, TradeStatus, TradingMode,
    )

    def paper(pnl, tid):
        intent = OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.1,
                             stop_loss=1.095, take_profit=1.11)
        decision = RiskDecision(intent=intent, approved=True, mode=TradingMode.PAPER)
        return PaperTrade(
            trade_id=tid, timestamp=T0, symbol="EURUSD", broker_symbol="EURUSD",
            timeframe="H1", strategy_name="real", side=Side.BUY, entry=1.10,
            stop_loss=1.095, take_profit=1.11, lot_size=0.1, risk_decision=decision,
            result="win" if pnl > 0 else "loss", pnl=pnl, status=TradeStatus.CLOSED,
        )

    trades = [paper(100, "PT-1"), paper(-50, "PT-2")]
    m = PerformanceMonitor().evaluate(trades)
    assert m.n_closed == 2
    assert m.wins == 1 and m.losses == 1
    assert PerformanceMonitor().pnl_by_strategy(trades) == {"real": 50.0}
