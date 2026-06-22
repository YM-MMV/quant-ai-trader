"""Tests for the strategy validation & approval gate (M11)."""
from datetime import datetime

import pytest

from services.backtest_service.metrics import BacktestMetrics
from services.backtest_service.simple_backtester import BacktestSignal, Direction
from services.backtest_service.strategy_validator import (
    RuleStatus,
    StrategyValidationConfig,
    StrategyValidator,
    ValidationInput,
    build_validation_input,
    load_validation_config,
)
from services.data_service.sample_data import generate_candles


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def metrics(*, total_trades=150, profit_factor=1.5, max_drawdown_pct=0.08,
            expectancy=2.0, largest=0.15, net_profit=300.0):
    return BacktestMetrics(
        total_trades=total_trades, profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct, expectancy=expectancy,
        largest_winner_contribution=largest, net_profit=net_profit,
    )


def good_input(**overrides):
    base = dict(
        in_sample=metrics(),
        out_of_sample=metrics(total_trades=60, net_profit=120.0, profit_factor=1.4),
        all_trades_have_stop_loss=True,
        all_trades_have_take_profit=True,
        rejected_no_stop_signals=0,
    )
    base.update(overrides)
    return ValidationInput(**base)


def _result(report, name):
    return next(r for r in report.results if r.name == name)


# --------------------------------------------------------------------------- #
# Approval of a strong strategy  (Done-when criterion)
# --------------------------------------------------------------------------- #
def test_strong_strategy_is_approved_with_record():
    report = StrategyValidator().validate(
        good_input(), strategy_id="macd-1", now=datetime(2024, 1, 1)
    )
    assert report.approved is True
    assert report.failed_rules == []
    record = report.approval_record()
    assert record.strategy_id == "macd-1"
    assert record.status == "approved"
    assert record.approved_at == datetime(2024, 1, 1)
    assert "minimum_trades" in record.metrics


def test_rejected_strategy_has_no_approval_record():
    report = StrategyValidator().validate(
        good_input(in_sample=metrics(total_trades=5)), strategy_id="weak"
    )
    assert report.approved is False
    with pytest.raises(ValueError):
        report.approval_record()


# --------------------------------------------------------------------------- #
# Each required gate rejects a weak strategy  (Done-when criterion)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "overrides, failing_rule",
    [
        (dict(in_sample=metrics(total_trades=10)), "minimum_trades"),
        (dict(in_sample=metrics(profit_factor=1.0)), "minimum_profit_factor"),
        (dict(in_sample=metrics(max_drawdown_pct=0.25)), "maximum_drawdown"),
        (dict(in_sample=metrics(largest=0.40)), "maximum_largest_trade_contribution"),
        (dict(in_sample=metrics(expectancy=-1.0)), "minimum_expectancy"),
        (dict(out_of_sample=metrics(net_profit=-50.0)), "out_of_sample_positive"),
        (dict(out_of_sample=None), "out_of_sample_positive"),
        (dict(all_trades_have_stop_loss=False), "no_missing_stop_loss"),
        (dict(rejected_no_stop_signals=3), "no_missing_stop_loss"),
        (dict(all_trades_have_take_profit=False), "no_missing_take_profit"),
        (dict(look_ahead_flags=("uses future close",)), "no_look_ahead"),
    ],
)
def test_weak_strategy_rejected_on_each_rule(overrides, failing_rule):
    report = StrategyValidator().validate(good_input(**overrides), strategy_id="s")
    assert report.approved is False
    assert failing_rule in report.failed_rules
    assert _result(report, failing_rule).status is RuleStatus.FAIL


# --------------------------------------------------------------------------- #
# Sensitivity gates
# --------------------------------------------------------------------------- #
def test_slippage_sensitivity_blocks_when_it_collapses():
    collapsed = metrics(net_profit=-20.0, profit_factor=0.8)
    report = StrategyValidator().validate(
        good_input(slippage_stress=collapsed), strategy_id="s"
    )
    assert report.approved is False
    assert "slippage_sensitivity" in report.failed_rules


def test_spread_sensitivity_passes_when_robust():
    robust = metrics(net_profit=180.0, profit_factor=1.3)
    report = StrategyValidator().validate(
        good_input(spread_stress=robust), strategy_id="s"
    )
    assert report.approved is True
    assert _result(report, "spread_sensitivity").status is RuleStatus.PASS


def test_sensitivity_not_evaluated_when_absent_and_non_blocking():
    report = StrategyValidator().validate(good_input(), strategy_id="s")
    for name in ("slippage_sensitivity", "spread_sensitivity"):
        r = _result(report, name)
        assert r.status is RuleStatus.NOT_EVALUATED
        assert r.blocking is False
    assert report.approved is True


# --------------------------------------------------------------------------- #
# Placeholders
# --------------------------------------------------------------------------- #
def test_placeholders_present_and_non_blocking():
    report = StrategyValidator().validate(good_input(), strategy_id="s")
    for name in ("parameter_sensitivity", "walk_forward_validation"):
        r = _result(report, name)
        assert r.status is RuleStatus.NOT_EVALUATED
        assert r.blocking is False


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_validation_is_deterministic():
    v = StrategyValidator()
    a = v.validate(good_input(), strategy_id="s", now=datetime(2024, 1, 1))
    b = v.validate(good_input(), strategy_id="s", now=datetime(2024, 1, 1))
    assert a == b


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_default_config_matches_documented_defaults():
    cfg = StrategyValidationConfig()
    assert cfg.minimum_trades == 100
    assert cfg.minimum_profit_factor == 1.2
    assert cfg.maximum_drawdown_pct == 15.0
    assert cfg.maximum_largest_trade_contribution_pct == 25.0
    assert cfg.require_stop_loss and cfg.require_take_profit
    assert cfg.require_out_of_sample_positive


def test_config_loads_from_yaml():
    cfg = load_validation_config()
    assert cfg.minimum_trades == 100
    assert cfg.minimum_profit_factor == 1.2
    assert cfg.require_out_of_sample_positive is True


def test_lenient_config_can_approve_small_sample():
    cfg = StrategyValidationConfig(minimum_trades=1, require_out_of_sample_positive=False)
    report = StrategyValidator(cfg).validate(
        good_input(in_sample=metrics(total_trades=3), out_of_sample=None),
        strategy_id="s",
    )
    assert report.approved is True


# --------------------------------------------------------------------------- #
# End-to-end with the M10 backtester
# --------------------------------------------------------------------------- #
def _always_long(window):
    if len(window) < 2:
        return BacktestSignal(Direction.NONE)
    last = float(window["close"].to_numpy()[-1])
    return BacktestSignal(Direction.BUY, stop_loss=last * 0.99, take_profit=last * 1.01)


def test_build_validation_input_runs_backtests_and_stress():
    candles_in = generate_candles("EURUSD", "M15", n=400, seed=1)
    candles_out = generate_candles("EURUSD", "M15", n=200, seed=2)
    vin = build_validation_input(candles_in, _always_long, candles_out=candles_out)
    assert vin.in_sample.total_trades >= 0
    assert vin.out_of_sample is not None
    # Stress runs were executed.
    assert vin.slippage_stress is not None and vin.spread_stress is not None
    # A report is produced deterministically.
    report = StrategyValidator(StrategyValidationConfig(minimum_trades=1)).validate(
        vin, strategy_id="e2e"
    )
    assert isinstance(report.summary, str)
