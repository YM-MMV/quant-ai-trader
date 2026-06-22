"""Tests for paper-trade execution and the paper-trade log (M13).

Covers the Done-when criteria: approved trades are logged (status OPEN, full
schema), rejected trades are logged (status REJECTED with the risk reasons),
every record carries a RiskDecision, close computes PnL + result, and the log
round-trips through disk. No MT5, no network — fills come from a reference price.
"""
from datetime import datetime

import pytest

from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import PaperTrade, TradeLogStore
from services.execution_service.audit_log import AuditLog, AuditEventType
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

NOW = datetime(2024, 1, 2, 12, 0, 0)

# Required log fields (verbatim from the milestone spec).
REQUIRED_FIELDS = [
    "trade_id", "timestamp", "symbol", "broker_symbol", "timeframe",
    "strategy_name", "strategy_version", "side", "entry", "stop_loss",
    "take_profit", "lot_size", "risk_percent", "features_snapshot",
    "kronos_prediction", "risk_decision", "result", "pnl",
    "max_adverse_excursion", "max_favourable_excursion", "status",
]


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


def decide(intent=None, context=None) -> RiskDecision:
    intent = intent or make_intent()
    context = context or make_context()
    return RiskManager().evaluate(intent, context, now=NOW)


@pytest.fixture
def service(tmp_path):
    trade_log = TradeLogStore(tmp_path / "trades.jsonl")
    audit_log = AuditLog(tmp_path / "audit.jsonl")
    return PaperExecutionService(trade_log=trade_log, audit_log=audit_log)


def make_features() -> MarketFeatures:
    return MarketFeatures(symbol="EURUSD", timeframe="H1", timestamp=NOW,
                          features={"rsi": 55.0, "atr": 0.0012})


def make_prediction() -> KronosPrediction:
    return KronosPrediction(symbol="EURUSD", timeframe="H1", as_of=NOW, horizon=1,
                            direction=PredictionDirection.UP, predicted_close=1.105,
                            probability=0.62)


# --------------------------------------------------------------------------- #
# Approved trades are logged  (Done-when)
# --------------------------------------------------------------------------- #
def test_approved_trade_is_opened_and_logged(service):
    decision = decide()
    assert decision.approved is True

    trade = service.execute(
        decision, make_context(), timeframe="H1",
        strategy_name="macd-osc", strategy_version="1.2.0",
        features_snapshot=make_features(), kronos_prediction=make_prediction(),
        now=NOW,
    )

    assert trade.status is TradeStatus.OPEN
    assert trade.result == "open"
    assert trade.trade_id == "PT-000001"
    assert trade.symbol == "EURUSD"
    assert trade.broker_symbol == "EURUSD"
    assert trade.lot_size == 0.1
    assert trade.entry == 1.1000
    assert trade.pnl is None

    logged = service.trade_log.latest()
    assert len(logged) == 1
    assert logged[0].trade_id == trade.trade_id
    assert service.trade_log.approved() == logged


def test_logged_trade_has_all_required_fields(service):
    trade = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    dumped = trade.model_dump()
    for field in REQUIRED_FIELDS:
        assert field in dumped, f"missing required log field: {field}"


def test_risk_percent_is_recorded(service):
    # 0.1 lots * 50-pip stop * 100k = $50 = 0.5% of 10k.
    trade = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    assert trade.risk_percent == pytest.approx(0.5)


def test_broker_symbol_mapping_is_logged(service):
    intent = make_intent(symbol="XAUUSD", stop_loss=1990.0, take_profit=2050.0,
                         volume=0.01)
    ctx = make_context(reference_price=2000.0)
    trade = service.execute(decide(intent, ctx), ctx, timeframe="H1", now=NOW)
    assert trade.symbol == "XAUUSD"
    assert trade.broker_symbol == "GOLD"


# --------------------------------------------------------------------------- #
# Rejected trades are logged  (Done-when)
# --------------------------------------------------------------------------- #
def test_rejected_trade_is_logged(service):
    ctx = make_context(spread_points=999)  # spread blows the limit
    decision = decide(context=ctx)
    assert decision.approved is False

    trade = service.execute(decision, ctx, timeframe="H1", now=NOW)
    assert trade.status is TradeStatus.REJECTED
    assert trade.result == "rejected"
    assert trade.pnl is None

    rejected = service.trade_log.rejected()
    assert len(rejected) == 1
    assert rejected[0].trade_id == trade.trade_id
    assert service.trade_log.approved() == []


def test_rejected_trade_carries_the_risk_decision_and_reasons(service):
    ctx = make_context(spread_points=999)
    decision = decide(context=ctx)
    trade = service.execute(decision, ctx, timeframe="H1", now=NOW)
    assert trade.risk_decision.approved is False
    assert trade.risk_decision.reasons  # at least one reason recorded
    assert trade.risk_decision.checks["spread_ok"] is False


# --------------------------------------------------------------------------- #
# Every trade must include a RiskDecision  (hard constraint)
# --------------------------------------------------------------------------- #
def test_paper_trade_requires_a_risk_decision():
    with pytest.raises(Exception):
        PaperTrade(
            trade_id="PT-1", timestamp=NOW, symbol="EURUSD",
            broker_symbol="EURUSD", timeframe="H1", side=Side.BUY,
            entry=1.1, stop_loss=1.09, take_profit=1.11, lot_size=0.1,
            # risk_decision intentionally omitted
        )


def test_execute_rejects_non_decision(service):
    with pytest.raises(TypeError):
        service.execute("not-a-decision", make_context(), timeframe="H1")


# --------------------------------------------------------------------------- #
# Close: PnL, result, MAE/MFE
# --------------------------------------------------------------------------- #
def test_close_winning_trade_computes_pnl_and_result(service):
    trade = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    closed = service.close(trade, exit_price=1.1050, now=NOW)
    # (1.1050 - 1.1000) * 0.1 lots * 100_000 = 50.0
    assert closed.pnl == pytest.approx(50.0)
    assert closed.result == "win"
    assert closed.status is TradeStatus.CLOSED
    assert closed.max_favourable_excursion >= 50.0


def test_close_losing_trade(service):
    trade = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    closed = service.close(trade, exit_price=1.0980, now=NOW)
    # (1.0980 - 1.1000) * 0.1 * 100_000 = -20.0
    assert closed.pnl == pytest.approx(-20.0)
    assert closed.result == "loss"
    assert closed.max_adverse_excursion >= 20.0


def test_close_sell_trade_pnl_sign(service):
    intent = make_intent(side=Side.SELL, stop_loss=1.1050, take_profit=1.0900)
    ctx = make_context()
    trade = service.execute(decide(intent, ctx), ctx, timeframe="H1", now=NOW)
    closed = service.close(trade, exit_price=1.0950, now=NOW)
    # sell: (1.1000 - 1.0950) * 0.1 * 100_000 = 50.0
    assert closed.pnl == pytest.approx(50.0)
    assert closed.result == "win"


def test_cannot_close_a_rejected_trade(service):
    ctx = make_context(spread_points=999)
    trade = service.execute(decide(context=ctx), ctx, timeframe="H1", now=NOW)
    with pytest.raises(ValueError):
        service.close(trade, exit_price=1.10)


def test_mark_widens_excursions(service):
    trade = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    marked = service.mark(trade, high=1.1080, low=1.0990)
    # buy: MFE = (1.1080-1.1000)*0.1*100k = 80 ; MAE = (1.1000-1.0990)*... = 10
    assert marked.max_favourable_excursion == pytest.approx(80.0)
    assert marked.max_adverse_excursion == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# Persistence / determinism
# --------------------------------------------------------------------------- #
def test_log_round_trips_through_disk(tmp_path):
    store = TradeLogStore(tmp_path / "trades.jsonl")
    svc = PaperExecutionService(trade_log=store,
                               audit_log=AuditLog(tmp_path / "audit.jsonl"))
    opened = svc.execute(decide(), make_context(), timeframe="H1",
                        features_snapshot=make_features(),
                        kronos_prediction=make_prediction(), now=NOW)
    svc.close(opened, exit_price=1.1050, now=NOW)

    # Fresh store reads the same file: full history (open + close), latest = closed.
    reread = TradeLogStore(tmp_path / "trades.jsonl")
    assert len(reread.records()) == 2
    latest = reread.latest()
    assert len(latest) == 1
    assert latest[0].status is TradeStatus.CLOSED
    assert latest[0].pnl == pytest.approx(50.0)
    assert latest[0].features_snapshot.features["rsi"] == 55.0
    assert latest[0].kronos_prediction.direction is PredictionDirection.UP


def test_trade_ids_are_monotonic(service):
    a = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    b = service.execute(decide(), make_context(), timeframe="H1", now=NOW)
    assert a.trade_id == "PT-000001"
    assert b.trade_id == "PT-000002"
