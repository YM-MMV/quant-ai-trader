"""Tests for the typed domain models (services/models.py).

Each model gets a happy-path construction test plus at least one validation
test that proves the guard rails (especially the SAFETY.md-derived ones) fire.
"""
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from services.models import (
    AssetClass,
    BacktestResult,
    Candle,
    KronosPrediction,
    MarketFeatures,
    OrderIntent,
    OrderType,
    PredictionDirection,
    RiskDecision,
    Side,
    SignalAction,
    StrategyMetadata,
    StrategySignal,
    TradeLog,
    TradeStatus,
    TradingMode,
)

NOW = datetime(2026, 1, 1, 12, 0, 0)


# --------------------------------------------------------------------------- #
# Candle
# --------------------------------------------------------------------------- #
def test_candle_valid():
    c = Candle(
        symbol="EURUSD",
        timeframe="H1",
        timestamp=NOW,
        open=1.1000,
        high=1.1050,
        low=1.0980,
        close=1.1020,
        volume=1500,
    )
    assert c.high >= c.low
    assert c.symbol == "EURUSD"


def test_candle_high_below_low_rejected():
    with pytest.raises(ValidationError):
        Candle(
            symbol="EURUSD",
            timeframe="H1",
            timestamp=NOW,
            open=1.10,
            high=1.09,  # high < low
            low=1.10,
            close=1.10,
        )


def test_candle_negative_price_rejected():
    with pytest.raises(ValidationError):
        Candle(
            symbol="EURUSD",
            timeframe="H1",
            timestamp=NOW,
            open=-1.0,
            high=1.0,
            low=0.5,
            close=0.9,
        )


def test_candle_rejects_unknown_field():
    with pytest.raises(ValidationError):
        Candle(
            symbol="EURUSD",
            timeframe="H1",
            timestamp=NOW,
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
            bogus=123,
        )


# --------------------------------------------------------------------------- #
# MarketFeatures
# --------------------------------------------------------------------------- #
def test_market_features_valid():
    mf = MarketFeatures(
        symbol="XAUUSD",
        timeframe="M15",
        timestamp=NOW,
        features={"rsi_14": 55.2, "ema_20": 1995.4},
    )
    assert mf.features["rsi_14"] == 55.2


def test_market_features_rejects_nan():
    with pytest.raises(ValidationError):
        MarketFeatures(
            symbol="XAUUSD",
            timeframe="M15",
            timestamp=NOW,
            features={"rsi_14": float("nan")},
        )


# --------------------------------------------------------------------------- #
# KronosPrediction
# --------------------------------------------------------------------------- #
def test_kronos_prediction_valid():
    p = KronosPrediction(
        symbol="EURUSD",
        timeframe="H1",
        as_of=NOW,
        horizon=4,
        direction=PredictionDirection.UP,
        predicted_close=1.1100,
        probability=0.62,
    )
    assert p.direction is PredictionDirection.UP
    assert p.model_name == "kronos-mock"


def test_kronos_probability_out_of_range_rejected():
    with pytest.raises(ValidationError):
        KronosPrediction(
            symbol="EURUSD",
            timeframe="H1",
            as_of=NOW,
            horizon=4,
            direction=PredictionDirection.UP,
            predicted_close=1.11,
            probability=1.5,
        )


def test_kronos_nonpositive_horizon_rejected():
    with pytest.raises(ValidationError):
        KronosPrediction(
            symbol="EURUSD",
            timeframe="H1",
            as_of=NOW,
            horizon=0,
            direction=PredictionDirection.FLAT,
            predicted_close=1.11,
            probability=0.5,
        )


# --------------------------------------------------------------------------- #
# StrategyMetadata / StrategySignal
# --------------------------------------------------------------------------- #
def test_strategy_metadata_valid():
    m = StrategyMetadata(
        id="macd-cross-001",
        name="MACD Crossover",
        category="momentum",
        asset_classes=[AssetClass.FOREX, AssetClass.METAL],
        timeframes=["H1", "H4"],
    )
    assert m.applicable_to_mt5 is True
    assert AssetClass.FOREX in m.asset_classes


def test_strategy_signal_valid():
    s = StrategySignal(
        strategy_id="macd-cross-001",
        symbol="EURUSD",
        timeframe="H1",
        timestamp=NOW,
        action=SignalAction.BUY,
        confidence=0.7,
        suggested_stop_loss=1.0950,
        suggested_take_profit=1.1100,
    )
    assert s.action is SignalAction.BUY


def test_strategy_signal_confidence_bounds():
    with pytest.raises(ValidationError):
        StrategySignal(
            strategy_id="x",
            symbol="EURUSD",
            timeframe="H1",
            timestamp=NOW,
            action=SignalAction.HOLD,
            confidence=2.0,
        )


# --------------------------------------------------------------------------- #
# OrderIntent — the SAFETY.md guard rails
# --------------------------------------------------------------------------- #
def test_order_intent_valid_market():
    o = OrderIntent(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.10,
        stop_loss=1.0950,
        take_profit=1.1100,
    )
    assert o.order_type is OrderType.MARKET


def test_order_intent_requires_stop_loss():
    with pytest.raises(ValidationError):
        OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.1, take_profit=1.11)


def test_order_intent_requires_take_profit():
    with pytest.raises(ValidationError):
        OrderIntent(symbol="EURUSD", side=Side.BUY, volume=0.1, stop_loss=1.09)


def test_order_intent_limit_requires_price():
    with pytest.raises(ValidationError):
        OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            volume=0.1,
            stop_loss=1.09,
            take_profit=1.11,
        )


def test_order_intent_buy_sl_must_be_below_entry():
    with pytest.raises(ValidationError):
        OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            volume=0.1,
            price=1.1000,
            stop_loss=1.1050,  # above entry — wrong for a buy
            take_profit=1.1100,
        )


def test_order_intent_sell_directionality_ok():
    o = OrderIntent(
        symbol="EURUSD",
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        volume=0.1,
        price=1.1000,
        stop_loss=1.1050,  # above entry — correct for a sell
        take_profit=1.0900,
    )
    assert o.side is Side.SELL


# --------------------------------------------------------------------------- #
# RiskDecision
# --------------------------------------------------------------------------- #
def _intent() -> OrderIntent:
    return OrderIntent(
        symbol="EURUSD", side=Side.BUY, volume=0.1, stop_loss=1.09, take_profit=1.11
    )


def test_risk_decision_approved():
    d = RiskDecision(
        intent=_intent(),
        approved=True,
        mode=TradingMode.PAPER,
        checks={"has_sl": True, "allowlisted": True},
        approved_volume=0.1,
    )
    assert d.approved is True


def test_risk_decision_denied_requires_reason():
    with pytest.raises(ValidationError):
        RiskDecision(intent=_intent(), approved=False)  # no reasons


def test_risk_decision_denied_with_reason_ok():
    d = RiskDecision(
        intent=_intent(),
        approved=False,
        reasons=["spread exceeds limit"],
    )
    assert d.reasons


# --------------------------------------------------------------------------- #
# TradeLog
# --------------------------------------------------------------------------- #
def test_trade_log_valid():
    t = TradeLog(
        id="trade-0001",
        timestamp=NOW,
        mode=TradingMode.PAPER,
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.1,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        status=TradeStatus.OPEN,
    )
    assert t.status is TradeStatus.OPEN


def test_trade_log_rejects_bad_volume():
    with pytest.raises(ValidationError):
        TradeLog(
            id="t",
            timestamp=NOW,
            mode=TradingMode.PAPER,
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.0,  # must be > 0
            entry_price=1.1,
            stop_loss=1.09,
            take_profit=1.11,
        )


# --------------------------------------------------------------------------- #
# BacktestResult
# --------------------------------------------------------------------------- #
def test_backtest_result_valid():
    r = BacktestResult(
        strategy_id="macd-cross-001",
        symbol="EURUSD",
        timeframe="H1",
        start=NOW,
        end=NOW + timedelta(days=30),
        num_trades=42,
        win_rate=0.55,
        net_profit=320.5,
        max_drawdown=120.0,
        profit_factor=1.4,
    )
    assert r.includes_friction is True


def test_backtest_result_frictionless_rejected():
    with pytest.raises(ValidationError):
        BacktestResult(
            strategy_id="x",
            symbol="EURUSD",
            timeframe="H1",
            start=NOW,
            end=NOW + timedelta(days=1),
            num_trades=1,
            win_rate=1.0,
            net_profit=10.0,
            includes_friction=False,
        )


def test_backtest_result_end_before_start_rejected():
    with pytest.raises(ValidationError):
        BacktestResult(
            strategy_id="x",
            symbol="EURUSD",
            timeframe="H1",
            start=NOW,
            end=NOW - timedelta(days=1),
            num_trades=0,
            win_rate=0.0,
            net_profit=0.0,
        )
