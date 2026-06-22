"""QuantDinger integration client (M18) — **optional external platform**.

`QuantDinger <https://github.com/brokermr810/QuantDinger>`_ is an external
backtesting / trading platform. This module gives the project a thin **client
layer** for it *without* making the rest of the system depend on it: the default
everywhere is the deterministic :class:`MockQuantDinger`, and the real HTTP
client is only used when a server URL is explicitly configured.

Design rules (from the milestone):

* **Optional.** QuantDinger is treated as an optional external service. Nothing
  in core code or tests requires it to be installed or running. The local
  :class:`~services.backtest_service.simple_backtester.SimpleBacktester` remains
  the always-available, deterministic backtester; QuantDinger is an *alternative*
  remote engine, not a replacement.
* **Mock first.** :class:`MockQuantDinger` is in-memory and deterministic — same
  inputs, same job ids and metrics — so tests never assume a live server.
* **One interface.** :class:`QuantDingerClient` is the contract the mock and the
  real client both implement, so callers never branch on which is in use.

Capabilities: submit a strategy, run a backtest, fetch a backtest result, and
two **placeholders** for paper trading (submit a paper strategy, fetch paper
logs) — clearly marked stubs until QuantDinger paper integration is built out.
"""
from __future__ import annotations

import abc
import hashlib
import json
import os
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TIMEOUT = 30.0
# Env var that points the real client (and ``auto`` mode) at a server.
ENV_URL = "QUANTDINGER_URL"
ENV_API_KEY = "QUANTDINGER_API_KEY"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class QuantDingerError(RuntimeError):
    """Base class for QuantDinger client failures."""


class QuantDingerNotAvailableError(QuantDingerError):
    """The real client cannot be used (no HTTP lib, or no server URL set)."""


class QuantDingerConnectionError(QuantDingerError):
    """A request to the QuantDinger server failed."""


class QuantDingerDisabledError(QuantDingerError):
    """QuantDinger is explicitly disabled but a call was made."""


# --------------------------------------------------------------------------- #
# Result models (shared schema; ``is_mock`` makes the source unmistakable)
# --------------------------------------------------------------------------- #
class StrategyRef(BaseModel):
    """A handle to a strategy registered with QuantDinger."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(..., min_length=1)
    name: str
    status: str = "submitted"
    is_mock: bool = True


class BacktestJob(BaseModel):
    """A backtest run handle. The mock completes immediately (``status`` done)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1)
    strategy_id: str = Field(..., min_length=1)
    symbol: str
    timeframe: str
    status: str = "queued"
    is_mock: bool = True


class BacktestResult(BaseModel):
    """The outcome of a backtest job: a metrics dict plus provenance."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1)
    strategy_id: str = Field(..., min_length=1)
    symbol: str
    timeframe: str
    status: str = "completed"
    metrics: dict[str, Any] = Field(default_factory=dict)
    is_mock: bool = True


class PaperDeployment(BaseModel):
    """Placeholder handle for a paper-trading deployment (not implemented yet)."""

    model_config = ConfigDict(extra="forbid")

    deployment_id: str = Field(..., min_length=1)
    strategy_id: str = Field(..., min_length=1)
    status: str = "not_implemented"
    placeholder: bool = True
    is_mock: bool = True
    note: str = (
        "QuantDinger paper-trading is a placeholder; the project's own paper "
        "executor is the supported path (see docs/QUANTDINGER_SETUP.md)"
    )


class PaperTradingLogs(BaseModel):
    """Placeholder paper-trading logs (not implemented yet)."""

    model_config = ConfigDict(extra="forbid")

    deployment_id: str = Field(..., min_length=1)
    status: str = "not_implemented"
    placeholder: bool = True
    is_mock: bool = True
    logs: list[dict[str, Any]] = Field(default_factory=list)
    note: str = (
        "QuantDinger paper-trading logs are a placeholder; see "
        "docs/QUANTDINGER_SETUP.md"
    )


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class QuantDingerClient(abc.ABC):
    """Abstract QuantDinger client. The mock and real client both implement this."""

    is_mock: bool = True

    @abc.abstractmethod
    def submit_strategy(self, strategy: dict[str, Any], *, name: Optional[str] = None) -> StrategyRef:
        """Register a strategy definition and return a handle."""

    @abc.abstractmethod
    def run_backtest(
        self,
        strategy_id: str,
        *,
        symbol: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> BacktestJob:
        """Start a backtest for a previously submitted strategy."""

    @abc.abstractmethod
    def fetch_backtest_result(self, job_id: str) -> BacktestResult:
        """Fetch the result of a backtest job."""

    @abc.abstractmethod
    def submit_paper_strategy(
        self, strategy_id: str, *, params: Optional[dict[str, Any]] = None
    ) -> PaperDeployment:
        """Placeholder: deploy a strategy to QuantDinger paper trading."""

    @abc.abstractmethod
    def fetch_paper_trading_logs(
        self, deployment_id: str, *, limit: int = 100
    ) -> PaperTradingLogs:
        """Placeholder: fetch paper-trading logs for a deployment."""


# --------------------------------------------------------------------------- #
# Deterministic helpers (shared by the mock)
# --------------------------------------------------------------------------- #
def _stable_id(prefix: str, *parts: Any) -> str:
    """A short, deterministic id from the given parts."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _seeded_metrics(seed: str) -> dict[str, Any]:
    """Derive plausible, fully deterministic backtest metrics from a seed."""
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    total_trades = 20 + (h % 40)
    win_rate = round(0.35 + ((h >> 8) % 40) / 100.0, 4)   # 0.35..0.74
    wins = round(total_trades * win_rate)
    losses = total_trades - wins
    net_profit = round(((h >> 16) % 5000) / 10.0 - 100.0, 2)
    profit_factor = round(0.8 + ((h >> 24) % 200) / 100.0, 4)
    max_dd_pct = round(((h >> 32) % 30) / 100.0, 4)
    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_profit": net_profit,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd_pct,
    }


# --------------------------------------------------------------------------- #
# Mock client (deterministic, in-memory — the default)
# --------------------------------------------------------------------------- #
class MockQuantDinger(QuantDingerClient):
    """A deterministic, in-memory stand-in for QuantDinger.

    No network, no external platform. Submitting a strategy and running a
    backtest produce stable ids; the backtest "completes" immediately with
    metrics derived deterministically from the inputs. Paper-trading methods
    return clearly marked placeholders.
    """

    is_mock = True

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyRef] = {}
        self._results: dict[str, BacktestResult] = {}

    def submit_strategy(self, strategy: dict[str, Any], *, name: Optional[str] = None) -> StrategyRef:
        if not isinstance(strategy, dict) or not strategy:
            raise ValueError("strategy must be a non-empty dict")
        name = name or str(strategy.get("name") or "strategy")
        strategy_id = _stable_id("strat", name, strategy)
        ref = StrategyRef(strategy_id=strategy_id, name=name, status="submitted", is_mock=True)
        self._strategies[strategy_id] = ref
        return ref

    def run_backtest(
        self,
        strategy_id: str,
        *,
        symbol: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> BacktestJob:
        if strategy_id not in self._strategies:
            raise QuantDingerError(f"unknown strategy_id {strategy_id!r}; submit it first")
        job_id = _stable_id("job", strategy_id, symbol, timeframe, start, end, params)
        # The mock completes synchronously; store the result for fetch.
        self._results[job_id] = BacktestResult(
            job_id=job_id,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            status="completed",
            metrics=_seeded_metrics(job_id),
            is_mock=True,
        )
        return BacktestJob(
            job_id=job_id,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            status="completed",
            is_mock=True,
        )

    def fetch_backtest_result(self, job_id: str) -> BacktestResult:
        result = self._results.get(job_id)
        if result is None:
            raise QuantDingerError(f"unknown job_id {job_id!r}; run a backtest first")
        return result

    def submit_paper_strategy(
        self, strategy_id: str, *, params: Optional[dict[str, Any]] = None
    ) -> PaperDeployment:
        return PaperDeployment(
            deployment_id=_stable_id("paper", strategy_id, params),
            strategy_id=strategy_id,
            is_mock=True,
        )

    def fetch_paper_trading_logs(
        self, deployment_id: str, *, limit: int = 100
    ) -> PaperTradingLogs:
        return PaperTradingLogs(deployment_id=deployment_id, is_mock=True)


# --------------------------------------------------------------------------- #
# Real HTTP client (optional; only used with an explicit server URL)
# --------------------------------------------------------------------------- #
class RealQuantDinger(QuantDingerClient):
    """Thin HTTP client for a running QuantDinger server.

    Talks JSON over HTTP. The transport is pluggable: pass a ``session`` with
    ``get``/``post`` methods (e.g. a ``requests.Session`` or a test double). When
    none is given, ``requests`` is imported lazily — so this class still imports
    fine without it, and tests inject a fake session instead.

    Paper-trading methods are placeholders (no server round-trip yet), matching
    the mock, so the interface stays stable until that integration lands.
    """

    is_mock = False

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        api_key: Optional[str] = None,
        session: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        base_url = base_url or os.environ.get(ENV_URL)
        if not base_url:
            raise QuantDingerNotAvailableError(
                f"no QuantDinger server URL; set {ENV_URL} or pass base_url "
                "(see docs/QUANTDINGER_SETUP.md)"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get(ENV_API_KEY)
        self.timeout = timeout
        self._session = session  # injected or lazily created

    # -- transport --------------------------------------------------------- #
    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:  # pragma: no cover - exercised only where requests is installed
            import requests
        except Exception as exc:  # noqa: BLE001
            raise QuantDingerNotAvailableError(
                "the 'requests' package is needed for the real QuantDinger "
                "client; install it or inject a session (see docs/QUANTDINGER_SETUP.md)"
            ) from exc
        self._session = requests.Session()
        return self._session

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, *, json_body: Any = None) -> dict[str, Any]:
        session = self._get_session()
        url = f"{self.base_url}{path}"
        try:
            fn = session.get if method == "GET" else session.post
            kwargs: dict[str, Any] = {"headers": self._headers(), "timeout": self.timeout}
            if json_body is not None:
                kwargs["json"] = json_body
            response = fn(url, **kwargs)
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            return response.json()
        except QuantDingerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QuantDingerConnectionError(
                f"QuantDinger {method} {path} failed: {exc}"
            ) from exc

    # -- API --------------------------------------------------------------- #
    def submit_strategy(self, strategy: dict[str, Any], *, name: Optional[str] = None) -> StrategyRef:
        if not isinstance(strategy, dict) or not strategy:
            raise ValueError("strategy must be a non-empty dict")
        name = name or str(strategy.get("name") or "strategy")
        data = self._request("POST", "/api/strategies", json_body={"name": name, "definition": strategy})
        return StrategyRef(
            strategy_id=str(data["strategy_id"]),
            name=str(data.get("name", name)),
            status=str(data.get("status", "submitted")),
            is_mock=False,
        )

    def run_backtest(
        self,
        strategy_id: str,
        *,
        symbol: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> BacktestJob:
        body = {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "params": params or {},
        }
        data = self._request("POST", "/api/backtests", json_body=body)
        return BacktestJob(
            job_id=str(data["job_id"]),
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            status=str(data.get("status", "queued")),
            is_mock=False,
        )

    def fetch_backtest_result(self, job_id: str) -> BacktestResult:
        data = self._request("GET", f"/api/backtests/{job_id}")
        return BacktestResult(
            job_id=job_id,
            strategy_id=str(data.get("strategy_id", "")),
            symbol=str(data.get("symbol", "")),
            timeframe=str(data.get("timeframe", "")),
            status=str(data.get("status", "completed")),
            metrics=dict(data.get("metrics", {})),
            is_mock=False,
        )

    def submit_paper_strategy(
        self, strategy_id: str, *, params: Optional[dict[str, Any]] = None
    ) -> PaperDeployment:
        # Placeholder: QuantDinger paper trading is not wired to the server yet.
        return PaperDeployment(
            deployment_id=_stable_id("paper", strategy_id, params),
            strategy_id=strategy_id,
            is_mock=False,
        )

    def fetch_paper_trading_logs(
        self, deployment_id: str, *, limit: int = 100
    ) -> PaperTradingLogs:
        # Placeholder: paper-trading logs are not exposed yet.
        return PaperTradingLogs(deployment_id=deployment_id, is_mock=False)


# --------------------------------------------------------------------------- #
# Disabled client + factory
# --------------------------------------------------------------------------- #
class DisabledQuantDinger(QuantDingerClient):
    """A client that refuses every call — QuantDinger explicitly turned off."""

    is_mock = False

    def _refuse(self) -> Any:
        raise QuantDingerDisabledError(
            "QuantDinger is disabled (mode='disabled'); use mock or real mode"
        )

    def submit_strategy(self, strategy: dict[str, Any], *, name: Optional[str] = None) -> StrategyRef:
        return self._refuse()

    def run_backtest(self, strategy_id: str, **kwargs: Any) -> BacktestJob:
        return self._refuse()

    def fetch_backtest_result(self, job_id: str) -> BacktestResult:
        return self._refuse()

    def submit_paper_strategy(self, strategy_id: str, **kwargs: Any) -> PaperDeployment:
        return self._refuse()

    def fetch_paper_trading_logs(self, deployment_id: str, **kwargs: Any) -> PaperTradingLogs:
        return self._refuse()


def quantdinger_configured(base_url: Optional[str] = None) -> bool:
    """True if a QuantDinger server URL is configured (arg or env)."""
    return bool(base_url or os.environ.get(ENV_URL))


def load_quantdinger(mode: str = "auto", **kwargs: Any) -> QuantDingerClient:
    """Return the right QuantDinger client for ``mode``.

    * ``auto`` — real if a server URL is configured (``base_url`` arg or
      ``QUANTDINGER_URL`` env), else the deterministic mock. This is the safe
      default: with no server set up, nothing tries to reach the network.
    * ``mock`` — :class:`MockQuantDinger` (deterministic; used by tests).
    * ``real`` — :class:`RealQuantDinger`; raises
      :class:`QuantDingerNotAvailableError` if no URL is configured.
    * ``disabled`` — :class:`DisabledQuantDinger` (every call raises).
    """
    mode = (mode or "auto").lower()
    if mode == "mock":
        return MockQuantDinger()
    if mode == "disabled":
        return DisabledQuantDinger()
    if mode == "real":
        return RealQuantDinger(**kwargs)
    if mode == "auto":
        if quantdinger_configured(kwargs.get("base_url")):
            return RealQuantDinger(**kwargs)
        return MockQuantDinger()
    raise ValueError(
        f"unknown QuantDinger mode {mode!r}; expected auto | real | mock | disabled"
    )


__all__ = [
    "QuantDingerClient",
    "MockQuantDinger",
    "RealQuantDinger",
    "DisabledQuantDinger",
    "load_quantdinger",
    "quantdinger_configured",
    "StrategyRef",
    "BacktestJob",
    "BacktestResult",
    "PaperDeployment",
    "PaperTradingLogs",
    "QuantDingerError",
    "QuantDingerNotAvailableError",
    "QuantDingerConnectionError",
    "QuantDingerDisabledError",
    "ENV_URL",
    "ENV_API_KEY",
]
