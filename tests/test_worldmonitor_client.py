"""Tests for the optional WorldMonitor client (M25).

These run **without a WorldMonitor server and without the ``requests`` package**:
the default is the disabled (neutral) client, the mock is deterministic and
in-memory, and the real client's HTTP path is exercised by injecting a fake
session. No test assumes WorldMonitor is installed, configured, or running.
"""
import pytest

from services.macro_service import worldmonitor_client as wm
from services.macro_service.worldmonitor_client import (
    DisabledWorldMonitor,
    MockWorldMonitor,
    RealWorldMonitor,
    RiskContext,
    WorldMonitorClient,
    WorldMonitorConnectionError,
    WorldMonitorNotAvailableError,
    load_worldmonitor,
    worldmonitor_configured,
)


# --------------------------------------------------------------------------- #
# Fake HTTP transport for the real client (no requests, no server)
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._payload


class FakeSession:
    """Records GET calls and returns a queued (or repeating) response."""

    def __init__(self, payload=None, status_ok=True):
        self.payload = payload if payload is not None else {}
        self.status_ok = status_ok
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.payload, status_ok=self.status_ok)


# --------------------------------------------------------------------------- #
# Factory + default (Done-when: optional, disabled by default)
# --------------------------------------------------------------------------- #
def test_default_mode_is_disabled(monkeypatch):
    monkeypatch.delenv(wm.ENV_URL, raising=False)
    client = load_worldmonitor()  # no mode → disabled
    assert isinstance(client, DisabledWorldMonitor)
    assert client.enabled is False


def test_auto_mode_is_disabled_without_url(monkeypatch):
    monkeypatch.delenv(wm.ENV_URL, raising=False)
    client = load_worldmonitor(mode="auto")
    assert isinstance(client, DisabledWorldMonitor)


def test_auto_mode_is_real_when_url_configured(monkeypatch):
    monkeypatch.setenv(wm.ENV_URL, "http://localhost:9000")
    client = load_worldmonitor(mode="auto", session=FakeSession())
    assert isinstance(client, RealWorldMonitor)


def test_mock_mode_returns_mock():
    assert isinstance(load_worldmonitor(mode="mock"), MockWorldMonitor)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        load_worldmonitor(mode="nonsense")


def test_worldmonitor_configured(monkeypatch):
    monkeypatch.delenv(wm.ENV_URL, raising=False)
    assert worldmonitor_configured() is False
    assert worldmonitor_configured("http://x") is True
    monkeypatch.setenv(wm.ENV_URL, "http://localhost:9000")
    assert worldmonitor_configured() is True


# --------------------------------------------------------------------------- #
# Disabled client = neutral context (trading must not depend on WorldMonitor)
# --------------------------------------------------------------------------- #
def test_disabled_yields_neutral_context():
    ctx = DisabledWorldMonitor().get_risk_context()
    assert isinstance(ctx, RiskContext)
    assert ctx.enabled is False
    assert ctx.source == "disabled"
    assert ctx.macro_risk_score is None
    assert ctx.event_risk is False
    assert ctx.news_lockout is False


def test_disabled_outputs_are_neutral_individually():
    client = DisabledWorldMonitor()
    assert client.macro_risk_score("EURUSD") is None
    assert client.event_risk("EURUSD") is False
    assert client.news_lockout("EURUSD") is False


# --------------------------------------------------------------------------- #
# Mock client = deterministic placeholders
# --------------------------------------------------------------------------- #
def test_mock_defaults_are_low_risk():
    ctx = MockWorldMonitor().get_risk_context("EURUSD")
    assert ctx.enabled is True
    assert ctx.source == "mock"
    assert ctx.macro_risk_score == 0.25
    assert ctx.event_risk is False
    assert ctx.news_lockout is False
    assert ctx.is_placeholder is True


def test_mock_overrides_simulate_a_risk_regime():
    client = MockWorldMonitor(
        macro_risk_score=0.9, event_risk=True, news_lockout=True
    )
    ctx = client.get_risk_context()
    assert ctx.macro_risk_score == 0.9
    assert ctx.event_risk is True
    assert ctx.news_lockout is True


def test_mock_is_deterministic():
    a = MockWorldMonitor().get_risk_context("EURUSD")
    b = MockWorldMonitor().get_risk_context("EURUSD")
    assert a.model_dump() == b.model_dump()


# --------------------------------------------------------------------------- #
# Real client over an injected fake session (no network, no requests)
# --------------------------------------------------------------------------- #
def test_real_requires_url(monkeypatch):
    monkeypatch.delenv(wm.ENV_URL, raising=False)
    with pytest.raises(WorldMonitorNotAvailableError):
        RealWorldMonitor()


def test_real_reads_risk_fields_from_payload():
    session = FakeSession(
        payload={
            "macro_risk_score": 0.7,
            "event_risk": True,
            "news_lockout": False,
            "details": {"region": "EU"},
        }
    )
    client = RealWorldMonitor(base_url="http://localhost:9000", session=session)
    ctx = client.get_risk_context("EURUSD")
    assert ctx.enabled is True
    assert ctx.source == "real"
    assert ctx.macro_risk_score == 0.7
    assert ctx.event_risk is True
    assert ctx.news_lockout is False
    assert ctx.details == {"region": "EU"}
    # symbol is passed through as a query param.
    assert session.calls[-1]["params"] == {"symbol": "EURUSD"}


def test_real_neutral_when_fields_absent():
    client = RealWorldMonitor(base_url="http://localhost:9000", session=FakeSession(payload={}))
    ctx = client.get_risk_context()
    assert ctx.macro_risk_score is None
    assert ctx.event_risk is False
    assert ctx.news_lockout is False


def test_real_sends_bearer_token_when_api_key_set():
    session = FakeSession(payload={})
    client = RealWorldMonitor(
        base_url="http://localhost:9000", api_key="secret", session=session
    )
    client.macro_risk_score()
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer secret"


def test_real_wraps_transport_errors():
    session = FakeSession(payload={}, status_ok=False)
    client = RealWorldMonitor(base_url="http://localhost:9000", session=session)
    with pytest.raises(WorldMonitorConnectionError):
        client.get_risk_context()


# --------------------------------------------------------------------------- #
# All clients honour the shared interface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "client",
    [
        DisabledWorldMonitor(),
        MockWorldMonitor(),
        RealWorldMonitor(base_url="http://localhost:9000", session=FakeSession(payload={})),
    ],
)
def test_clients_implement_interface(client):
    assert isinstance(client, WorldMonitorClient)
    assert isinstance(client.get_risk_context(), RiskContext)
