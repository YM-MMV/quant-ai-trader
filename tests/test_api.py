"""Tests for the FastAPI backend (M20).

Exercised with Starlette's ``TestClient`` (no running server, no network). If
FastAPI/httpx are not installed the whole module is skipped, so the core suite
still runs without the optional API dependencies.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import app  # noqa: E402

client = TestClient(app)

CLEAN_INTENT = {
    "symbol": "EURUSD", "side": "buy", "volume": 0.01,
    "stop_loss": 1.095, "take_profit": 1.11,
}
APPROVING_CTX = {"reference_price": 1.10, "account_balance": 10_000}


# --------------------------------------------------------------------------- #
# Health & structural safety (Done-when: API starts, health works)
# --------------------------------------------------------------------------- #
def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["trading_mode"] == "paper"
    assert body["live_trading"] is False


def test_no_live_execution_endpoint():
    paths = set(app.openapi()["paths"])
    assert not any("live" in p.lower() for p in paths)
    assert not any("execute" in p.lower() for p in paths)


def test_expected_endpoints_exist():
    paths = set(app.openapi()["paths"])
    expected = {
        "/health", "/symbols", "/candles", "/features",
        "/strategies/inventory", "/strategies/adapters",
        "/backtests/run", "/risk/check", "/paper-trades",
    }
    assert expected <= paths


def test_health_exposes_no_secrets():
    body = client.get("/health").json()
    blob = " ".join(str(v).lower() for v in body.values())
    for leak in ("password", "secret", "token", "api_key", "login"):
        assert leak not in blob
    # Settings field names must not appear in the response keys.
    assert not (set(body) & {"mt5_password", "mt5_login", "mt5_server"})


# --------------------------------------------------------------------------- #
# Data routes
# --------------------------------------------------------------------------- #
def test_symbols():
    body = client.get("/symbols").json()
    assert body["count"] >= 1
    names = {s["symbol"] for s in body["symbols"]}
    assert "EURUSD" in names
    eur = next(s for s in body["symbols"] if s["symbol"] == "EURUSD")
    assert eur["allowlisted"] is True


def test_candles():
    resp = client.get("/candles", params={"symbol": "EURUSD", "timeframe": "H1", "n": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 10
    assert len(body["candles"]) == 10
    assert {"timestamp", "open", "high", "low", "close"} <= set(body["candles"][0])


def test_candles_validation_rejects_bad_n():
    # n above the cap fails Pydantic/Query validation → 422.
    resp = client.get("/candles", params={"symbol": "EURUSD", "n": 999999})
    assert resp.status_code == 422


def test_candles_requires_symbol():
    assert client.get("/candles").status_code == 422


def test_features():
    body = client.get("/features", params={"symbol": "EURUSD", "n": 150}).json()
    assert body["symbol"] == "EURUSD"
    assert body["features"]
    assert all(isinstance(v, float) for v in body["features"].values())


# --------------------------------------------------------------------------- #
# Strategy routes
# --------------------------------------------------------------------------- #
def test_strategies_inventory():
    body = client.get("/strategies/inventory").json()
    assert body["count"] > 0
    assert {"name", "category", "mt5_applicability"} <= set(body["strategies"][0])


def test_strategies_adapters():
    body = client.get("/strategies/adapters").json()
    assert body["count"] > 0
    assert "macd_oscillator" in {a["name"] for a in body["adapters"]}


# --------------------------------------------------------------------------- #
# Backtest route
# --------------------------------------------------------------------------- #
def test_backtest_run():
    resp = client.post("/backtests/run", json={
        "strategy": "macd_oscillator", "symbol": "EURUSD", "n": 300,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_bars"] == 300
    assert "total_trades" in body["metrics"]
    assert body["score"]["grade"] in {"A", "B", "C", "D", "F"}


def test_backtest_unknown_strategy_404():
    resp = client.post("/backtests/run", json={"strategy": "does_not_exist"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Risk route
# --------------------------------------------------------------------------- #
def test_risk_check_approves():
    resp = client.post("/risk/check", json={"intent": CLEAN_INTENT, "context": APPROVING_CTX})
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] is True
    assert body["mode"] == "paper"
    assert body["reasons"] == []


def test_risk_check_denies_non_allowlisted():
    ctx = {**APPROVING_CTX, "allowlist": []}
    body = client.post("/risk/check", json={"intent": CLEAN_INTENT, "context": ctx}).json()
    assert body["approved"] is False
    assert any("allowlisted" in r for r in body["reasons"])


def test_risk_check_rejects_intent_without_stop():
    bad = {"symbol": "EURUSD", "side": "buy", "volume": 0.01, "take_profit": 1.11}
    resp = client.post("/risk/check", json={"intent": bad, "context": APPROVING_CTX})
    assert resp.status_code == 422  # missing required stop_loss


# --------------------------------------------------------------------------- #
# Paper-trading route (the gate)
# --------------------------------------------------------------------------- #
def test_paper_trade_created_after_approval():
    resp = client.post("/paper-trades", json={
        "intent": CLEAN_INTENT, "context": APPROVING_CTX, "timeframe": "H1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["approved"] is True
    assert body["trade"]["status"] == "open"


def test_paper_trade_refused_when_risk_denies():
    ctx = {**APPROVING_CTX, "allowlist": []}
    body = client.post("/paper-trades", json={"intent": CLEAN_INTENT, "context": ctx}).json()
    assert body["created"] is False
    assert body["approved"] is False
    assert body["trade"] is None
    assert body["reasons"]
