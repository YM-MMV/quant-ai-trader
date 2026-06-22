"""Full-system end-to-end smoke test (M26).

Wires the whole pipeline together with **mocks and fake data only** and asserts
it runs start to finish:

    1.  Load fake candles            (services.data_service.sample_data)
    2.  Compute features             (services.data_service.features)
    3.  Mock Kronos prediction       (services.kronos_service.MockKronos)
    4.  Run all executable adapters  (services.strategy_service.adapters)
    5.  Generate signals             (StrategyAdapter.generate_signal)
    6.  Run a backtest               (services.backtest_service.SimpleBacktester)
    7.  Validate the strategy        (services.backtest_service.StrategyValidator)
    8.  Propose an OrderIntent       (services.models.OrderIntent)
    9.  Run the RiskManager          (services.risk_service.RiskManager)
   10.  Create a paper trade         (services.execution_service.PaperExecutionService)
   11.  Write the audit log          (services.execution_service.AuditLog)

Hard constraints (verified by construction — this test imports none of them):
no MT5, no real API calls, no external services, no secrets. Everything is
deterministic: the same run always produces the same trade log and audit trail.

This is a *smoke* test: it checks that the stages connect and produce the right
shapes, not that the (random-walk) strategy is profitable.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from services.backtest_service.simple_backtester import (
    BacktestSignal,
    Direction,
    SimpleBacktester,
)
from services.backtest_service.strategy_validator import (
    StrategyValidator,
    ValidationReport,
    build_validation_input,
)
from services.data_service import features as feats
from services.data_service.sample_data import generate_candles
from services.execution_service.audit_log import AuditLog, AuditEventType
from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import PaperTrade, TradeLogStore
from services.kronos_service import MockKronos
from services.kronos_service.base import KronosPrediction as KronosModelPrediction
from services.models import (
    KronosPrediction,
    MarketFeatures,
    OrderIntent,
    OrderType,
    PredictionDirection,
    RiskDecision,
    Side,
    TradeStatus,
    TradingMode,
)
from services.risk_service.risk_manager import RiskContext, RiskManager
from services.strategy_service.adapters import (
    TECHNICAL_INDICATOR_ADAPTERS,
    register_technical_indicator_adapters,
)
from services.strategy_service.base import AdapterSignal
from services.strategy_service.registry import StrategyRegistry

SYMBOL = "EURUSD"
TIMEFRAME = "M15"


# --------------------------------------------------------------------------- #
# Pipeline helpers
# --------------------------------------------------------------------------- #
def compute_feature_snapshot(candles: pd.DataFrame) -> MarketFeatures:
    """Stage 2: derive a few indicators at the last bar into a MarketFeatures."""
    close, high, low = candles["close"], candles["high"], candles["low"]
    raw = {
        "rsi": feats.rsi(close).iloc[-1],
        "atr": feats.atr(high, low, close).iloc[-1],
        "volatility": feats.rolling_volatility(close).iloc[-1],
    }
    # Keep only finite values — early bars produce NaNs by construction.
    snapshot = {k: float(v) for k, v in raw.items() if pd.notna(v)}
    return MarketFeatures(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        timestamp=candles["timestamp"].iloc[-1].to_pydatetime(),
        features=snapshot,
    )


def to_models_prediction(pred: KronosModelPrediction, as_of: datetime) -> KronosPrediction:
    """Map a Kronos-service prediction onto the snapshot model the trade log stores."""
    if pred.predicted_return > 0:
        direction = PredictionDirection.UP
    elif pred.predicted_return < 0:
        direction = PredictionDirection.DOWN
    else:
        direction = PredictionDirection.FLAT
    return KronosPrediction(
        symbol=pred.symbol,
        timeframe=pred.timeframe,
        as_of=as_of,
        horizon=pred.pred_len,
        direction=direction,
        predicted_close=pred.predicted_close,
        probability=pred.confidence_proxy,
        model_name=pred.model_name,
    )


def sma_cross_strategy(short: int = 5, long: int = 20):
    """A deterministic SMA-crossover strategy for the backtester (Stage 6).

    Causal: only ever reads the window it is handed. Emits a BUY when the short
    SMA crosses above the long SMA (SELL on the opposite cross), with a simple
    percentage stop/target so the backtester has a valid, bounded trade.
    """

    def strategy(window: pd.DataFrame):
        closes = window["close"]
        if len(closes) < long + 1:
            return BacktestSignal(Direction.NONE)
        short_now = closes.iloc[-short:].mean()
        long_now = closes.iloc[-long:].mean()
        short_prev = closes.iloc[-short - 1 : -1].mean()
        long_prev = closes.iloc[-long - 1 : -1].mean()
        price = float(closes.iloc[-1])
        if short_prev <= long_prev and short_now > long_now:
            return BacktestSignal(Direction.BUY, price * 0.995, price * 1.010)
        if short_prev >= long_prev and short_now < long_now:
            return BacktestSignal(Direction.SELL, price * 1.005, price * 0.990)
        return BacktestSignal(Direction.NONE)

    return strategy


# --------------------------------------------------------------------------- #
# The end-to-end smoke test
# --------------------------------------------------------------------------- #
def test_full_pipeline_smoke(tmp_path):
    now = datetime(2024, 1, 8, 0, 0, 0)

    # -- 1. Load fake candles ------------------------------------------------ #
    candles = generate_candles(symbol=SYMBOL, timeframe=TIMEFRAME, n=300, seed=7)
    assert len(candles) == 300
    assert {"open", "high", "low", "close", "timestamp"} <= set(candles.columns)

    # -- 2. Compute features ------------------------------------------------- #
    features = compute_feature_snapshot(candles)
    assert isinstance(features, MarketFeatures)
    assert features.features  # at least one finite indicator

    # -- 3. Mock Kronos prediction ------------------------------------------ #
    kronos_pred = MockKronos().predict(candles, symbol=SYMBOL, timeframe=TIMEFRAME)
    assert kronos_pred.is_mock is True
    assert kronos_pred.predicted_close > 0
    prediction = to_models_prediction(kronos_pred, as_of=now)

    # -- 4 + 5. Run all executable adapters and generate signals ------------ #
    registry = StrategyRegistry()
    register_technical_indicator_adapters(registry)
    assert len(registry) == len(TECHNICAL_INDICATOR_ADAPTERS) == 9

    signals: dict[str, AdapterSignal] = {}
    for adapter in registry.adapters():
        signal = adapter.generate_signal(candles, features, kronos_pred)
        # Every adapter must return a valid signal and never crash the pipeline.
        assert isinstance(signal, AdapterSignal)
        signals[adapter.name] = signal
    assert len(signals) == 9

    # -- 6. Run a backtest --------------------------------------------------- #
    strategy = sma_cross_strategy()
    report = SimpleBacktester().run(candles, strategy)
    assert report.trades is not None
    assert len(report.trades) >= 1  # the SMA cross trades on this fake series

    # -- 7. Validate the strategy ------------------------------------------- #
    validation_input = build_validation_input(candles, strategy)
    validation = StrategyValidator().validate(validation_input, strategy_id="sma_cross")
    assert isinstance(validation, ValidationReport)
    assert isinstance(validation.approved, bool)  # a verdict either way

    # -- 8. Propose an OrderIntent ------------------------------------------ #
    # Smoke test drives the approved path so the open-trade branch is exercised;
    # a clean, allowlisted intent with a valid stop/target below/above price.
    entry_price = float(candles["close"].iloc[-1])
    intent = OrderIntent(
        symbol=SYMBOL,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        volume=0.1,
        stop_loss=round(entry_price - 0.0050, 5),
        take_profit=round(entry_price + 0.0100, 5),
        strategy_id="sma_cross",
    )

    # -- 9. Run the RiskManager --------------------------------------------- #
    context = RiskContext(
        mode=TradingMode.PAPER,
        allow_live=False,
        allowlist=(SYMBOL, "XAUUSD"),
        account_balance=10_000.0,
        reference_price=entry_price,
        spread_points=10,
        volatility=None,
        realized_daily_loss=0.0,
        open_trades=(),
        trades_today=0,
        strategy_approved=True,
        strategy_applicability="direct",
    )
    decision = RiskManager().evaluate(intent, context, now=now)
    assert isinstance(decision, RiskDecision)
    assert decision.approved is True
    assert decision.reasons == []

    # -- 10 + 11. Create a paper trade and write the audit log -------------- #
    trade_log = TradeLogStore(tmp_path / "trades.jsonl")
    audit_log = AuditLog(tmp_path / "audit.jsonl")
    service = PaperExecutionService(trade_log=trade_log, audit_log=audit_log)
    trade = service.execute(
        decision,
        context,
        timeframe=TIMEFRAME,
        strategy_name="sma_cross",
        strategy_version="1.0.0",
        features_snapshot=features,
        kronos_prediction=prediction,
        reference_price=entry_price,
        now=now,
    )

    # Paper trade opened, fully populated, and persisted.
    assert isinstance(trade, PaperTrade)
    assert trade.status is TradeStatus.OPEN
    assert trade.side is Side.BUY
    assert trade.entry == entry_price
    assert trade.risk_decision is decision
    assert (tmp_path / "trades.jsonl").exists()
    assert len(trade_log.records()) == 1

    # Audit trail written: system mode, signal, risk decision, execution.
    recorded = {event.event_type for event in audit_log.events()}
    assert AuditEventType.SYSTEM_MODE in recorded
    assert AuditEventType.SIGNAL_PROPOSED in recorded
    assert AuditEventType.RISK_DECISION in recorded
    assert AuditEventType.EXECUTION_DECISION in recorded
    assert (tmp_path / "audit.jsonl").exists()


# --------------------------------------------------------------------------- #
# Constraint guard: the pipeline never touches MT5 / network / secrets
# --------------------------------------------------------------------------- #
def test_smoke_pipeline_imports_no_broker_or_network():
    """Sanity check that the smoke pipeline pulls in no broker module.

    The real MT5 package is never imported by any pipeline stage above — paper
    execution, the mock gateway, the mock Kronos, and the local backtester are
    all pure / mocked. (``requests``/``httpx`` may be present from unrelated test
    dependencies, so only the broker SDK is asserted absent.)
    """
    import sys

    assert "MetaTrader5" not in sys.modules, "smoke test must never import MT5"
