"""Tests for the multi-strategy portfolio backtester."""
import math
from types import SimpleNamespace

import pytest

from services.backtest_service.portfolio_backtester import (
    LegInput,
    run_portfolio,
)


def equity_from_returns(returns, start=100.0):
    """Build an equity curve from a list of per-bar simple returns."""
    eq = [start]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    return eq


def leg(name, returns):
    return LegInput(name=name, equity_curve=equity_from_returns(returns))


# --------------------------------------------------------------------------- #
# Weighting
# --------------------------------------------------------------------------- #
def test_inverse_vol_gives_more_weight_to_the_calmer_leg():
    noisy = leg("noisy", [0.02, -0.02, 0.02, -0.02, 0.02, -0.02])
    calm = leg("calm", [0.005, -0.005, 0.005, -0.005, 0.005, -0.005])
    report = run_portfolio([noisy, calm], weighting="inverse_vol")

    w = report.weights
    assert math.isclose(w["noisy"] + w["calm"], 1.0, rel_tol=1e-9)
    assert w["calm"] > w["noisy"]            # lower vol → larger allocation


def test_equal_weighting_splits_evenly():
    a = leg("a", [0.01, -0.01, 0.02, -0.02])
    b = leg("b", [0.03, -0.01, 0.00, 0.01])
    report = run_portfolio([a, b], weighting="equal")
    assert report.weights == {"a": 0.5, "b": 0.5}


def test_unknown_weighting_scheme_rejected():
    with pytest.raises(ValueError):
        run_portfolio([leg("a", [0.01, -0.01])], weighting="magic")


# --------------------------------------------------------------------------- #
# Diversification (the whole point)
# --------------------------------------------------------------------------- #
def test_anti_correlated_legs_reduce_portfolio_volatility():
    up_down = leg("a", [0.01, -0.01, 0.01, -0.01, 0.01, -0.01])
    down_up = leg("b", [-0.01, 0.01, -0.01, 0.01, -0.01, 0.01])
    report = run_portfolio([up_down, down_up], weighting="equal")

    leg_vol = max(l.volatility for l in report.legs)
    assert leg_vol > 0
    # Perfectly anti-correlated, equal weight → the blend nets to ~flat.
    assert report.volatility < leg_vol
    assert report.volatility == pytest.approx(0.0, abs=1e-9)


def test_correlation_matrix_detects_anti_correlation():
    a = leg("a", [0.01, -0.01, 0.01, -0.01, 0.01])
    b = leg("b", [-0.01, 0.01, -0.01, 0.01, -0.01])
    report = run_portfolio([a, b])
    assert report.correlation[("a", "b")] == pytest.approx(-1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Sharpe annualisation
# --------------------------------------------------------------------------- #
def test_portfolio_sharpe_none_without_periods_then_set_with():
    a = leg("a", [0.01, 0.00, 0.02, -0.01, 0.015, 0.005])
    b = leg("b", [0.00, 0.01, -0.005, 0.02, 0.00, 0.01])
    no_ppy = run_portfolio([a, b])
    with_ppy = run_portfolio([a, b], periods_per_year=6200.0)
    assert no_ppy.sharpe_ratio is None
    assert with_ppy.sharpe_ratio is not None
    assert math.isfinite(with_ppy.sharpe_ratio)


# --------------------------------------------------------------------------- #
# Robustness / plumbing
# --------------------------------------------------------------------------- #
def test_flat_leg_gets_zero_inverse_vol_weight():
    active = leg("active", [0.01, -0.01, 0.02, -0.02])
    flat = leg("flat", [0.0, 0.0, 0.0, 0.0])
    report = run_portfolio([active, flat], weighting="inverse_vol")
    assert report.weights["flat"] == 0.0
    assert report.weights["active"] == pytest.approx(1.0)


def test_all_flat_legs_fall_back_to_equal_weight():
    report = run_portfolio(
        [leg("a", [0.0, 0.0, 0.0]), leg("b", [0.0, 0.0, 0.0])],
        weighting="inverse_vol",
    )
    assert report.weights == {"a": 0.5, "b": 0.5}


def test_from_report_adapts_a_backtest_report_like_object():
    fake = SimpleNamespace(
        equity_curve=[100, 101, 102, 101], trades=[object(), object()]
    )
    li = LegInput.from_report("x", fake)
    assert li.name == "x" and li.n_trades == 2
    assert li.equity_curve == [100, 101, 102, 101]


def test_legs_truncated_to_shortest_tail():
    long_leg = leg("long", [0.01, -0.01, 0.01, -0.01, 0.01, -0.01])
    short_leg = leg("short", [0.02, -0.02])
    report = run_portfolio([long_leg, short_leg], weighting="equal")
    assert report.n_bars == 2                # min(6, 2) per-bar returns


def test_empty_legs_rejected():
    with pytest.raises(ValueError):
        run_portfolio([])


def test_is_deterministic():
    legs = [leg("a", [0.01, -0.01, 0.02]), leg("b", [0.0, 0.01, -0.005])]
    r1 = run_portfolio(legs, periods_per_year=6200.0)
    r2 = run_portfolio(legs, periods_per_year=6200.0)
    assert r1 == r2
