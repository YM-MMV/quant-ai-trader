"""Tests for backtest metrics (pure functions, hand-built trades)."""
from types import SimpleNamespace

import math

from services.backtest_service.metrics import (
    BacktestMetrics,
    annualised_sharpe,
    compute_metrics,
    max_drawdown,
)


def _trade(pnl, r_multiple, bars_held=1):
    return SimpleNamespace(pnl=pnl, r_multiple=r_multiple, bars_held=bars_held)


# --------------------------------------------------------------------------- #
# max_drawdown
# --------------------------------------------------------------------------- #
def test_max_drawdown_absolute_and_pct():
    abs_dd, pct_dd = max_drawdown([100, 120, 90, 110, 80])
    assert abs_dd == 40.0           # peak 120 -> trough 80
    assert round(pct_dd, 4) == round(40 / 120, 4)


def test_max_drawdown_monotonic_increase_is_zero():
    abs_dd, pct_dd = max_drawdown([100, 101, 102, 103])
    assert abs_dd == 0.0 and pct_dd == 0.0


# --------------------------------------------------------------------------- #
# compute_metrics
# --------------------------------------------------------------------------- #
def test_metrics_basic_counts_and_ratios():
    trades = [
        _trade(10, 2.0, bars_held=4),
        _trade(30, 3.0, bars_held=2),
        _trade(-5, -1.0, bars_held=6),
        _trade(-5, -1.0, bars_held=3),
    ]
    equity = [100, 110, 140, 135, 130]
    m = compute_metrics(trades, equity)

    assert m.total_trades == 4
    assert m.wins == 2 and m.losses == 2
    assert m.win_rate == 0.5
    assert m.gross_profit == 40.0 and m.gross_loss == 10.0
    assert m.net_profit == 30.0
    assert m.profit_factor == 4.0          # 40 / 10
    assert m.expectancy == 7.5             # 30 / 4
    assert m.average_r == 0.75             # (2+3-1-1)/4
    assert m.largest_winner_contribution == 0.75   # 30 / 40
    assert m.average_holding_bars == 3.75  # (4+2+6+3)/4


def test_profit_factor_none_without_losses():
    trades = [_trade(10, 1.0), _trade(20, 2.0)]
    m = compute_metrics(trades, [100, 110, 130])
    assert m.profit_factor is None
    assert m.gross_loss == 0.0


def test_consecutive_losses_streak():
    trades = [
        _trade(-1, -1.0), _trade(-1, -1.0), _trade(5, 1.0),
        _trade(-1, -1.0), _trade(-1, -1.0), _trade(-1, -1.0), _trade(2, 1.0),
    ]
    m = compute_metrics(trades, [100])
    assert m.max_consecutive_losses == 3


def test_empty_trades_is_safe():
    m = compute_metrics([], [100, 90, 95])
    assert isinstance(m, BacktestMetrics)
    assert m.total_trades == 0
    assert m.win_rate == 0.0 and m.profit_factor is None
    assert m.max_drawdown == 10.0          # still computed from the equity curve


def test_sharpe_placeholder_none_for_flat_equity():
    m = compute_metrics([_trade(0, 0.0)], [100, 100, 100, 100])
    assert m.sharpe_placeholder is None


def test_sharpe_placeholder_present_for_varying_equity():
    m = compute_metrics([_trade(1, 1.0)], [100, 101, 103, 102, 105])
    assert m.sharpe_placeholder is not None


def test_metrics_to_dict_roundtrip():
    m = compute_metrics([_trade(10, 1.0)], [100, 110])
    d = m.to_dict()
    assert d["total_trades"] == 1 and d["net_profit"] == 10.0


# --------------------------------------------------------------------------- #
# annualised Sharpe
# --------------------------------------------------------------------------- #
def test_sharpe_ratio_none_without_periods_per_year():
    # Back-compat: callers that don't pass periods_per_year get no annualised value.
    m = compute_metrics([_trade(1, 1.0)], [100, 101, 103, 102, 105])
    assert m.sharpe_placeholder is not None
    assert m.sharpe_ratio is None


def test_sharpe_ratio_is_per_bar_times_sqrt_periods():
    equity = [100, 101, 103, 102, 105]
    ppy = 6200.0
    m = compute_metrics([_trade(1, 1.0)], equity, periods_per_year=ppy)
    assert m.sharpe_ratio is not None
    assert math.isclose(
        m.sharpe_ratio, m.sharpe_placeholder * math.sqrt(ppy), rel_tol=1e-9
    )


def test_annualised_sharpe_none_for_flat_or_unknown_period():
    assert annualised_sharpe([100, 100, 100, 100], 6200.0) is None   # flat → per-bar None
    assert annualised_sharpe([100, 101, 103, 105], None) is None      # no period info
    assert annualised_sharpe([100, 101, 103, 105], 0) is None         # invalid period
