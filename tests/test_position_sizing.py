"""Tests for deterministic position sizing (M12)."""
from services.risk_service.position_sizing import compute_lot_size
from services.risk_service.symbol_specs import (
    broker_symbol,
    canonical_symbol,
    get_symbol_spec,
)

EURUSD = get_symbol_spec("EURUSD")
XAUUSD = get_symbol_spec("XAUUSD")


# --------------------------------------------------------------------------- #
# Core sizing for EURUSD and XAUUSD  (Done-when criterion)
# --------------------------------------------------------------------------- #
def test_eurusd_lot_size():
    # 1% of 10,000 = $100 risk; 50-pip stop; 1 lot = 100,000 units.
    r = compute_lot_size(account_balance=10_000, risk_pct=1.0,
                         entry_price=1.1000, stop_loss=1.0950, spec=EURUSD)
    assert r.lots == 0.2
    assert r.money_at_risk == 100.0
    assert r.within_budget is True
    assert r.broker_symbol == "EURUSD"


def test_xauusd_lot_size():
    # 1% of 10,000 = $100 risk; $10 stop; 1 lot = 100 oz.
    r = compute_lot_size(account_balance=10_000, risk_pct=1.0,
                         entry_price=2000.0, stop_loss=1990.0, spec=XAUUSD)
    assert r.lots == 0.1
    assert r.money_at_risk == 100.0
    assert r.broker_symbol == "GOLD"  # broker mapping applied


# --------------------------------------------------------------------------- #
# Stepping / clamping
# --------------------------------------------------------------------------- #
def test_lots_are_floored_to_lot_step():
    # raw = 100 / (0.0037 * 100000) = 0.2702... -> floored to 0.27 (step 0.01).
    r = compute_lot_size(account_balance=10_000, risk_pct=1.0,
                         entry_price=1.1000, stop_loss=1.0963, spec=EURUSD)
    assert r.lots == 0.27
    assert r.money_at_risk <= r.risk_amount  # never rounds up into extra risk


def test_below_min_lot_returns_zero():
    r = compute_lot_size(account_balance=100, risk_pct=0.1,
                         entry_price=1.1000, stop_loss=1.0950, spec=EURUSD)
    assert r.lots == 0.0
    assert "minimum lot" in r.reason


def test_capped_at_max_lot():
    r = compute_lot_size(account_balance=10_000_000, risk_pct=1.0,
                         entry_price=1.1000, stop_loss=1.0990, spec=EURUSD)
    assert r.lots == EURUSD.max_lot
    assert "maximum lot" in r.reason


def test_zero_stop_distance_returns_zero():
    r = compute_lot_size(account_balance=10_000, risk_pct=1.0,
                         entry_price=1.1000, stop_loss=1.1000, spec=EURUSD)
    assert r.lots == 0.0
    assert "stop distance" in r.reason


def test_determinism():
    a = compute_lot_size(account_balance=10_000, risk_pct=1.0,
                         entry_price=1.1, stop_loss=1.095, spec=EURUSD)
    b = compute_lot_size(account_balance=10_000, risk_pct=1.0,
                         entry_price=1.1, stop_loss=1.095, spec=EURUSD)
    assert a == b


# --------------------------------------------------------------------------- #
# Symbol specs / broker mapping
# --------------------------------------------------------------------------- #
def test_broker_symbol_mapping_roundtrip():
    assert broker_symbol("XAUUSD") == "GOLD"
    assert canonical_symbol("GOLD") == "XAUUSD"
    assert broker_symbol("EURUSD") == "EURUSD"


def test_unknown_symbol_spec_is_none():
    assert get_symbol_spec("NOPE") is None
