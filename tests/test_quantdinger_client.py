"""Tests for the optional QuantDinger client (M18).

These run **without a QuantDinger server and without the ``requests`` package**:
the default mock is deterministic and in-memory, and the real client's HTTP path
is exercised by injecting a fake session. No test assumes QuantDinger is running.
"""
import pytest

from services.backtest_service import quantdinger_client as qd
from services.backtest_service.quantdinger_client import (
    BacktestJob,
    BacktestResult,
    DisabledQuantDinger,
    MockQuantDinger,
    PaperDeployment,
    PaperTradingLogs,
    QuantDingerClient,
    QuantDingerConnectionError,
    QuantDingerDisabledError,
    QuantDingerError,
    QuantDingerNotAvailableError,
    RealQuantDinger,
    StrategyRef,
    load_quantdinger,
    quantdinger_configured,
)

SAMPLE_STRATEGY = {"name": "ma_cross", "fast": 10, "slow": 30}


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
    """Records calls and returns queued responses for get/post."""

    def __init__(self, post_payloads=None, get_payloads=None):
        self.post_payloads = list(post_payloads or [])
        self.get_payloads = list(get_payloads or [])
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return FakeResponse(self.post_payloads.pop(0))

    def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return FakeResponse(self.get_payloads.pop(0))


# --------------------------------------------------------------------------- #
# Factory + availability (Done-when: optional, mock is the default)
# --------------------------------------------------------------------------- #
def test_auto_mode_defaults_to_mock_without_url(monkeypatch):
    monkeypatch.delenv(qd.ENV_URL, raising=False)
    client = load_quantdinger(mode="auto")
    assert isinstance(client, MockQuantDinger)
    assert client.is_mock is True


def test_mock_mode_returns_mock():
    assert isinstance(load_quantdinger(mode="mock"), MockQuantDinger)


def test_auto_mode_uses_real_when_url_configured():
    client = load_quantdinger(mode="auto", base_url="http://localhost:9999", session=FakeSession())
    assert isinstance(client, RealQuantDinger)
    assert client.is_mock is False


def test_real_mode_without_url_raises(monkeypatch):
    monkeypatch.delenv(qd.ENV_URL, raising=False)
    with pytest.raises(QuantDingerNotAvailableError):
        load_quantdinger(mode="real")


def test_disabled_mode_refuses_calls():
    client = load_quantdinger(mode="disabled")
    assert isinstance(client, DisabledQuantDinger)
    with pytest.raises(QuantDingerDisabledError):
        client.submit_strategy(SAMPLE_STRATEGY)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        load_quantdinger(mode="bogus")


def test_quantdinger_configured(monkeypatch):
    monkeypatch.delenv(qd.ENV_URL, raising=False)
    assert quantdinger_configured() is False
    assert quantdinger_configured("http://x") is True
    monkeypatch.setenv(qd.ENV_URL, "http://server")
    assert quantdinger_configured() is True


def test_mock_implements_interface():
    assert isinstance(MockQuantDinger(), QuantDingerClient)


# --------------------------------------------------------------------------- #
# Mock client: full deterministic round-trip
# --------------------------------------------------------------------------- #
def test_mock_submit_strategy():
    client = MockQuantDinger()
    ref = client.submit_strategy(SAMPLE_STRATEGY, name="ma_cross")
    assert isinstance(ref, StrategyRef)
    assert ref.is_mock is True
    assert ref.status == "submitted"
    assert ref.strategy_id


def test_mock_submit_strategy_is_deterministic():
    a = MockQuantDinger().submit_strategy(SAMPLE_STRATEGY, name="ma_cross")
    b = MockQuantDinger().submit_strategy(SAMPLE_STRATEGY, name="ma_cross")
    assert a.strategy_id == b.strategy_id


def test_mock_submit_strategy_rejects_empty():
    client = MockQuantDinger()
    with pytest.raises(ValueError):
        client.submit_strategy({})


def test_mock_run_backtest_and_fetch_result():
    client = MockQuantDinger()
    ref = client.submit_strategy(SAMPLE_STRATEGY)
    job = client.run_backtest(ref.strategy_id, symbol="EURUSD", timeframe="H1")
    assert isinstance(job, BacktestJob)
    assert job.status == "completed"

    result = client.fetch_backtest_result(job.job_id)
    assert isinstance(result, BacktestResult)
    assert result.job_id == job.job_id
    assert result.strategy_id == ref.strategy_id
    assert result.status == "completed"
    assert result.metrics["total_trades"] >= 1
    assert 0.0 <= result.metrics["win_rate"] <= 1.0


def test_mock_run_backtest_deterministic_metrics():
    c1 = MockQuantDinger()
    r1 = c1.submit_strategy(SAMPLE_STRATEGY)
    j1 = c1.run_backtest(r1.strategy_id, symbol="EURUSD", timeframe="H1")
    res1 = c1.fetch_backtest_result(j1.job_id)

    c2 = MockQuantDinger()
    r2 = c2.submit_strategy(SAMPLE_STRATEGY)
    j2 = c2.run_backtest(r2.strategy_id, symbol="EURUSD", timeframe="H1")
    res2 = c2.fetch_backtest_result(j2.job_id)

    assert j1.job_id == j2.job_id
    assert res1.metrics == res2.metrics


def test_mock_run_backtest_unknown_strategy_raises():
    client = MockQuantDinger()
    with pytest.raises(QuantDingerError):
        client.run_backtest("strat_nope", symbol="EURUSD", timeframe="H1")


def test_mock_fetch_unknown_job_raises():
    client = MockQuantDinger()
    with pytest.raises(QuantDingerError):
        client.fetch_backtest_result("job_nope")


def test_mock_paper_methods_are_placeholders():
    client = MockQuantDinger()
    ref = client.submit_strategy(SAMPLE_STRATEGY)
    dep = client.submit_paper_strategy(ref.strategy_id)
    assert isinstance(dep, PaperDeployment)
    assert dep.placeholder is True
    assert dep.status == "not_implemented"

    logs = client.fetch_paper_trading_logs(dep.deployment_id)
    assert isinstance(logs, PaperTradingLogs)
    assert logs.placeholder is True
    assert logs.logs == []


# --------------------------------------------------------------------------- #
# Real client: HTTP path via injected fake session (no server, no requests)
# --------------------------------------------------------------------------- #
def test_real_submit_strategy_calls_server():
    session = FakeSession(post_payloads=[{"strategy_id": "srv-1", "name": "ma_cross", "status": "submitted"}])
    client = RealQuantDinger(base_url="http://qd.local/", session=session)
    ref = client.submit_strategy(SAMPLE_STRATEGY, name="ma_cross")
    assert ref.strategy_id == "srv-1"
    assert ref.is_mock is False
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == "http://qd.local/api/strategies"


def test_real_run_backtest_and_fetch_result():
    session = FakeSession(
        post_payloads=[{"job_id": "job-9", "status": "queued"}],
        get_payloads=[{
            "strategy_id": "srv-1", "symbol": "EURUSD", "timeframe": "H1",
            "status": "completed", "metrics": {"net_profit": 12.5},
        }],
    )
    client = RealQuantDinger(base_url="http://qd.local", session=session)
    job = client.run_backtest("srv-1", symbol="EURUSD", timeframe="H1")
    assert job.job_id == "job-9"
    assert job.is_mock is False

    result = client.fetch_backtest_result("job-9")
    assert result.metrics == {"net_profit": 12.5}
    assert session.calls[1]["url"] == "http://qd.local/api/backtests/job-9"


def test_real_client_sends_api_key_header():
    session = FakeSession(post_payloads=[{"strategy_id": "s", "name": "n", "status": "submitted"}])
    client = RealQuantDinger(base_url="http://qd.local", api_key="secret", session=session)
    client.submit_strategy(SAMPLE_STRATEGY)
    headers = session.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer secret"


def test_real_client_wraps_transport_errors():
    class BoomSession:
        def post(self, url, **kwargs):
            raise ConnectionError("refused")

    client = RealQuantDinger(base_url="http://qd.local", session=BoomSession())
    with pytest.raises(QuantDingerConnectionError):
        client.submit_strategy(SAMPLE_STRATEGY)


def test_real_paper_methods_are_placeholders():
    client = RealQuantDinger(base_url="http://qd.local", session=FakeSession())
    dep = client.submit_paper_strategy("srv-1")
    assert dep.placeholder is True
    assert dep.is_mock is False
    logs = client.fetch_paper_trading_logs(dep.deployment_id)
    assert logs.placeholder is True


def test_real_client_uses_env_url(monkeypatch):
    monkeypatch.setenv(qd.ENV_URL, "http://from-env")
    client = RealQuantDinger(session=FakeSession())
    assert client.base_url == "http://from-env"


# --------------------------------------------------------------------------- #
# Interface compatibility: mock and real share the result schema
# --------------------------------------------------------------------------- #
def test_mock_and_real_share_result_schema():
    mock = MockQuantDinger()
    ref = mock.submit_strategy(SAMPLE_STRATEGY)
    job = mock.run_backtest(ref.strategy_id, symbol="EURUSD", timeframe="H1")
    mock_result = mock.fetch_backtest_result(job.job_id)

    session = FakeSession(get_payloads=[{
        "strategy_id": "s", "symbol": "EURUSD", "timeframe": "H1",
        "status": "completed", "metrics": {},
    }])
    real = RealQuantDinger(base_url="http://qd.local", session=session)
    real_result = real.fetch_backtest_result("job-1")

    assert set(mock_result.model_dump()) == set(real_result.model_dump())
    assert mock_result.is_mock is True
    assert real_result.is_mock is False
