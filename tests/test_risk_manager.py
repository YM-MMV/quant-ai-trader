"""Tests for the deterministic RiskManager (M12).

Covers every documented rejection rule plus the approval path. Uses real
``OrderIntent`` objects (and ``model_construct`` to simulate the impossible
missing-stop/TP cases that the model itself would normally forbid).
"""
from datetime import datetime

import pytest

from services.config_loader import RiskConfig
from services.models import OrderIntent, OrderType, Side, TradingMode
from services.risk_service.risk_manager import RiskContext, RiskManager


def make_config(**overrides) -> RiskConfig:
    base = dict(
        require_stop_loss=True, require_take_profit=True,
        max_daily_loss=100.0, max_open_trades=3, max_spread_points=30,
        max_risk_per_trade_pct=1.0, max_total_exposure_pct=5.0,
        kill_switch_enabled=True, max_trades_per_day=10, max_volatility=None,
        allowed_modes=[TradingMode.RESEARCH, TradingMode.BACKTEST,
                       TradingMode.PAPER, TradingMode.MT5_DEMO],
    )
    base.update(overrides)
    return RiskConfig(**base)


def make_intent(**overrides) -> OrderIntent:
    base = dict(
        symbol="EURUSD", side=Side.BUY, order_type=OrderType.MARKET,
        volume=0.1, stop_loss=1.0950, take_profit=1.1100, strategy_id="macd-1",
    )
    base.update(overrides)
    return OrderIntent(**base)


def make_context(**overrides) -> RiskContext:
    base = dict(
        mode=TradingMode.PAPER, allow_live=False,
        allowlist=("EURUSD", "XAUUSD"), account_balance=10_000.0,
        reference_price=1.1000, spread_points=10, volatility=None,
        realized_daily_loss=0.0, open_trades=(), trades_today=0,
        strategy_approved=True, strategy_applicability="direct",
    )
    base.update(overrides)
    return RiskContext(**base)


def evaluate(intent=None, context=None, config=None):
    rm = RiskManager(config or make_config())
    return rm.evaluate(intent or make_intent(), context or make_context(),
                       now=datetime(2024, 1, 1))


# --------------------------------------------------------------------------- #
# Approval path  (Done-when criterion)
# --------------------------------------------------------------------------- #
def test_clean_intent_is_approved():
    d = evaluate()
    assert d.approved is True
    assert d.reasons == []
    assert d.approved_volume == 0.1
    assert all(d.checks.values())


# --------------------------------------------------------------------------- #
# Each rejection rule  (Done-when criterion: all rejection cases tested)
# --------------------------------------------------------------------------- #
def test_reject_trading_mode_not_allowed():
    d = evaluate(context=make_context(mode=TradingMode.LIVE))
    assert d.approved is False
    assert d.checks["trading_mode_allowed"] is False


def test_reject_live_trading_disabled():
    cfg = make_config(allowed_modes=[TradingMode.PAPER, TradingMode.LIVE])
    d = evaluate(context=make_context(mode=TradingMode.LIVE, allow_live=False),
                 config=cfg)
    assert d.approved is False
    assert d.checks["live_trading_enabled"] is False
    assert d.checks["trading_mode_allowed"] is True  # isolated to the live lock


def test_reject_symbol_not_allowlisted():
    d = evaluate(context=make_context(allowlist=("XAUUSD",)))
    assert d.approved is False
    assert d.checks["symbol_allowlisted"] is False


def test_reject_missing_stop_loss():
    intent = OrderIntent.model_construct(
        symbol="EURUSD", side=Side.BUY, order_type=OrderType.MARKET,
        volume=0.1, stop_loss=None, take_profit=1.1100, price=None,
        strategy_id="x", comment="",
    )
    d = evaluate(intent=intent)
    assert d.approved is False
    assert d.checks["stop_loss_present"] is False


def test_reject_missing_take_profit():
    intent = OrderIntent.model_construct(
        symbol="EURUSD", side=Side.BUY, order_type=OrderType.MARKET,
        volume=0.1, stop_loss=1.0950, take_profit=None, price=None,
        strategy_id="x", comment="",
    )
    d = evaluate(intent=intent)
    assert d.approved is False
    assert d.checks["take_profit_present"] is False


def test_reject_risk_percent_exceeds_limit():
    # 0.5 lots * 50-pip stop * 100k = $250 = 2.5% of 10k > 1% limit.
    d = evaluate(intent=make_intent(volume=0.5))
    assert d.approved is False
    assert d.checks["risk_per_trade"] is False


def test_reject_daily_loss_limit_hit():
    d = evaluate(context=make_context(realized_daily_loss=150.0))
    assert d.approved is False
    assert d.checks["daily_loss_limit"] is False


# --------------------------------------------------------------------------- #
# Balance-relative daily-loss limit (scales with the account)
# --------------------------------------------------------------------------- #
def test_daily_loss_percent_scales_with_balance():
    # Absolute cap off, 2% relative cap -> 2% of 1,000,000 = 20,000.
    cfg = make_config(max_daily_loss=0.0, max_daily_loss_pct=2.0)
    ok = evaluate(
        context=make_context(account_balance=1_000_000.0, realized_daily_loss=19_000.0),
        config=cfg,
    )
    assert ok.checks["daily_loss_limit"] is True

    hit = evaluate(
        context=make_context(account_balance=1_000_000.0, realized_daily_loss=21_000.0),
        config=cfg,
    )
    assert hit.checks["daily_loss_limit"] is False
    assert any("hit limit 20000.00" in r for r in hit.reasons)


def test_daily_loss_uses_tighter_of_absolute_and_percent():
    # Both caps on: the *smaller* one applies.
    cfg = make_config(max_daily_loss=100.0, max_daily_loss_pct=2.0)
    # Small balance: 2% = 20 < 100 -> percent is tighter, 50 breaches it.
    small = evaluate(
        context=make_context(account_balance=1_000.0, realized_daily_loss=50.0),
        config=cfg,
    )
    assert small.checks["daily_loss_limit"] is False
    # Large balance: 2% = 20,000 > 100 -> absolute is tighter, 50 is under it.
    large = evaluate(
        context=make_context(account_balance=1_000_000.0, realized_daily_loss=50.0),
        config=cfg,
    )
    assert large.checks["daily_loss_limit"] is True


def test_daily_loss_percent_disabled_keeps_absolute():
    # Back-compat: pct=0 -> behaves exactly like the absolute-only cap.
    cfg = make_config(max_daily_loss=100.0, max_daily_loss_pct=0.0)
    assert evaluate(
        context=make_context(realized_daily_loss=150.0), config=cfg
    ).checks["daily_loss_limit"] is False
    assert evaluate(
        context=make_context(realized_daily_loss=50.0), config=cfg
    ).checks["daily_loss_limit"] is True


def test_daily_loss_no_limit_when_both_disabled():
    cfg = make_config(max_daily_loss=0.0, max_daily_loss_pct=0.0)
    d = evaluate(
        context=make_context(realized_daily_loss=1_000_000.0), config=cfg
    )
    assert d.checks["daily_loss_limit"] is True


def test_reject_max_open_trades():
    open_trades = (("GBPUSD", "buy"), ("USDJPY", "buy"), ("AUDUSD", "buy"))
    d = evaluate(context=make_context(open_trades=open_trades))
    assert d.approved is False
    assert d.checks["max_open_trades"] is False


def test_reject_max_trades_per_day():
    d = evaluate(context=make_context(trades_today=10))
    assert d.approved is False
    assert d.checks["max_trades_per_day"] is False


def test_reject_spread_too_high():
    d = evaluate(context=make_context(spread_points=40))
    assert d.approved is False
    assert d.checks["spread_ok"] is False


def test_reject_volatility_too_high():
    cfg = make_config(max_volatility=0.01)
    d = evaluate(context=make_context(volatility=0.05), config=cfg)
    assert d.approved is False
    assert d.checks["volatility_ok"] is False


def test_reject_duplicate_trade():
    d = evaluate(context=make_context(open_trades=(("EURUSD", "buy"),)))
    assert d.approved is False
    assert d.checks["no_duplicate_trade"] is False


def test_reject_strategy_not_approved():
    d = evaluate(context=make_context(strategy_approved=False))
    assert d.approved is False
    assert d.checks["strategy_approved"] is False


def test_reject_research_only_strategy():
    d = evaluate(context=make_context(strategy_applicability="research_only"))
    assert d.approved is False
    assert d.checks["not_research_only"] is False


def test_reject_unknown_symbol_has_no_spec():
    d = evaluate(intent=make_intent(symbol="NOPE"),
                 context=make_context(allowlist=("NOPE",)))
    assert d.approved is False
    assert d.checks["risk_per_trade"] is False


# --------------------------------------------------------------------------- #
# Decision shape / determinism
# --------------------------------------------------------------------------- #
def test_rejected_decision_lists_all_reasons():
    # Two independent violations -> both reported.
    d = evaluate(context=make_context(spread_points=40, trades_today=10))
    assert d.approved is False
    assert d.checks["spread_ok"] is False
    assert d.checks["max_trades_per_day"] is False
    assert len(d.reasons) >= 2


def test_decision_is_deterministic():
    a = evaluate()
    b = evaluate()
    assert a == b
