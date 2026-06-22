"""WorldMonitor integration client (M25) — **optional macro/news risk context**.

`WorldMonitor <https://github.com/koala73/worldmonitor>`_ is an external macro /
news / geopolitical monitoring project. This module gives the system a thin,
**strictly optional** client for it so it can be used as *risk context* —
never as a trading signal.

Design rules (from the milestone):

* **Optional only.** WorldMonitor is off by default. Nothing in core code or
  tests requires it to be installed, configured, or reachable. The default
  client is :class:`DisabledWorldMonitor`, which returns a neutral context.
* **Trading never depends on it.** The risk context is advisory. A disabled or
  unreachable WorldMonitor yields a *neutral* context (no extra risk, no
  lockout) so trading behaviour is unchanged by its absence. Callers must treat
  this as supplementary context, not as a source of entry/exit signals.
* **No base-install dependency.** The real client talks JSON over HTTP and
  imports ``requests`` lazily, so this module imports fine without it. Tests use
  the deterministic mock or an injected session and never touch the network.

The client exposes three deliberately **placeholder** risk outputs, matching the
milestone scope (the real mapping onto WorldMonitor's data is intentionally not
built out yet):

* ``macro_risk_score`` — placeholder macro risk score in ``[0.0, 1.0]`` (or
  ``None`` when unknown / disabled).
* ``event_risk`` — boolean event-risk flag (e.g. a high-impact event window).
* ``news_lockout`` — placeholder boolean: should new entries be paused on news?

:func:`load_worldmonitor` is the factory; its default mode is ``disabled``.
"""
from __future__ import annotations

import abc
import os
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TIMEOUT = 15.0
# Env vars that configure the real client (and ``auto`` mode).
ENV_URL = "WORLDMONITOR_URL"
ENV_API_KEY = "WORLDMONITOR_API_KEY"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class WorldMonitorError(RuntimeError):
    """Base class for WorldMonitor client failures."""


class WorldMonitorNotAvailableError(WorldMonitorError):
    """The real client cannot be used (no HTTP lib, or no server URL set)."""


class WorldMonitorConnectionError(WorldMonitorError):
    """A request to the WorldMonitor server failed."""


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
class RiskContext(BaseModel):
    """Advisory macro/news risk context. **Never a trading signal.**

    ``enabled`` records whether a live WorldMonitor produced this; when
    ``False`` (the default, disabled path) the fields are neutral and trading
    should proceed exactly as if WorldMonitor did not exist.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    source: str = "disabled"  # "disabled" | "mock" | "real"
    macro_risk_score: Optional[float] = Field(
        default=None,
        description="Placeholder macro risk score in [0,1]; None when unknown.",
    )
    event_risk: bool = Field(
        default=False, description="Placeholder high-impact event-risk flag."
    )
    news_lockout: bool = Field(
        default=False,
        description="Placeholder: pause new entries due to news? Advisory only.",
    )
    is_placeholder: bool = True
    details: dict[str, Any] = Field(default_factory=dict)
    note: str = (
        "Advisory risk context only; trading does not depend on WorldMonitor. "
        "See docs/WORLDMONITOR_SETUP.md"
    )


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class WorldMonitorClient(abc.ABC):
    """Abstract WorldMonitor client.

    Subclasses implement the three placeholder risk outputs; the combined
    :meth:`get_risk_context` is provided here so every client assembles a
    :class:`RiskContext` the same way.
    """

    enabled: bool = False
    source: str = "disabled"

    @abc.abstractmethod
    def macro_risk_score(self, symbol: Optional[str] = None) -> Optional[float]:
        """Placeholder macro risk score in ``[0, 1]`` (``None`` when unknown)."""

    @abc.abstractmethod
    def event_risk(self, symbol: Optional[str] = None) -> bool:
        """Placeholder boolean event-risk flag."""

    @abc.abstractmethod
    def news_lockout(self, symbol: Optional[str] = None) -> bool:
        """Placeholder boolean news-lockout flag."""

    def get_risk_context(self, symbol: Optional[str] = None) -> RiskContext:
        """Assemble the three placeholder outputs into a :class:`RiskContext`."""
        return RiskContext(
            enabled=self.enabled,
            source=self.source,
            macro_risk_score=self.macro_risk_score(symbol),
            event_risk=self.event_risk(symbol),
            news_lockout=self.news_lockout(symbol),
        )


# --------------------------------------------------------------------------- #
# Disabled client (the default — neutral context, never blocks trading)
# --------------------------------------------------------------------------- #
class DisabledWorldMonitor(WorldMonitorClient):
    """The default. WorldMonitor is off; every output is neutral.

    Returning a neutral context (rather than raising) is deliberate: trading
    must not depend on WorldMonitor, so its absence simply means "no extra risk
    information" — not an error and not a lockout.
    """

    enabled = False
    source = "disabled"

    def macro_risk_score(self, symbol: Optional[str] = None) -> Optional[float]:
        return None

    def event_risk(self, symbol: Optional[str] = None) -> bool:
        return False

    def news_lockout(self, symbol: Optional[str] = None) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Mock client (deterministic placeholders; explicit opt-in, used by tests)
# --------------------------------------------------------------------------- #
class MockWorldMonitor(WorldMonitorClient):
    """A deterministic, in-memory stand-in with placeholder risk outputs.

    No network. Outputs are fixed placeholders by default; pass overrides to
    simulate a given macro regime in tests. Same inputs → same context.
    """

    enabled = True
    source = "mock"

    def __init__(
        self,
        *,
        macro_risk_score: Optional[float] = 0.25,
        event_risk: bool = False,
        news_lockout: bool = False,
    ) -> None:
        self._macro_risk_score = macro_risk_score
        self._event_risk = bool(event_risk)
        self._news_lockout = bool(news_lockout)

    def macro_risk_score(self, symbol: Optional[str] = None) -> Optional[float]:
        return self._macro_risk_score

    def event_risk(self, symbol: Optional[str] = None) -> bool:
        return self._event_risk

    def news_lockout(self, symbol: Optional[str] = None) -> bool:
        return self._news_lockout


# --------------------------------------------------------------------------- #
# Real HTTP client (optional; only used with an explicit server URL)
# --------------------------------------------------------------------------- #
class RealWorldMonitor(WorldMonitorClient):
    """Thin HTTP client for a running WorldMonitor instance.

    Talks JSON over HTTP. The transport is pluggable: pass a ``session`` with a
    ``get`` method (e.g. a ``requests.Session`` or a test double). When none is
    given, ``requests`` is imported lazily — so this class imports fine without
    it, and tests inject a fake session instead.

    The three risk outputs remain **placeholders**: this client fetches a status
    payload and reads risk fields from it *if present*, falling back to neutral
    values. The full mapping onto WorldMonitor's data is intentionally not built
    out — WorldMonitor is risk *context*, not a signal source.
    """

    enabled = True
    source = "real"

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
            raise WorldMonitorNotAvailableError(
                f"no WorldMonitor server URL; set {ENV_URL} or pass base_url "
                "(see docs/WORLDMONITOR_SETUP.md)"
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
            raise WorldMonitorNotAvailableError(
                "the 'requests' package is needed for the real WorldMonitor "
                "client; install it or inject a session "
                "(see docs/WORLDMONITOR_SETUP.md)"
            ) from exc
        self._session = requests.Session()
        return self._session

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _fetch(self, symbol: Optional[str]) -> dict[str, Any]:
        session = self._get_session()
        url = f"{self.base_url}/api/risk"
        params = {"symbol": symbol} if symbol else None
        try:
            response = session.get(
                url, headers=self._headers(), params=params, timeout=self.timeout
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except WorldMonitorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WorldMonitorConnectionError(
                f"WorldMonitor GET /api/risk failed: {exc}"
            ) from exc

    # -- placeholder risk outputs ----------------------------------------- #
    def macro_risk_score(self, symbol: Optional[str] = None) -> Optional[float]:
        value = self._fetch(symbol).get("macro_risk_score")
        return float(value) if value is not None else None

    def event_risk(self, symbol: Optional[str] = None) -> bool:
        return bool(self._fetch(symbol).get("event_risk", False))

    def news_lockout(self, symbol: Optional[str] = None) -> bool:
        return bool(self._fetch(symbol).get("news_lockout", False))

    def get_risk_context(self, symbol: Optional[str] = None) -> RiskContext:
        # Single round-trip: fetch once, then read all three fields from it.
        data = self._fetch(symbol)
        score = data.get("macro_risk_score")
        return RiskContext(
            enabled=True,
            source="real",
            macro_risk_score=float(score) if score is not None else None,
            event_risk=bool(data.get("event_risk", False)),
            news_lockout=bool(data.get("news_lockout", False)),
            details=dict(data.get("details", {})),
        )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def worldmonitor_configured(base_url: Optional[str] = None) -> bool:
    """True if a WorldMonitor server URL is configured (arg or env)."""
    return bool(base_url or os.environ.get(ENV_URL))


def load_worldmonitor(mode: str = "disabled", **kwargs: Any) -> WorldMonitorClient:
    """Return the right WorldMonitor client for ``mode``. **Default: disabled.**

    * ``disabled`` — :class:`DisabledWorldMonitor` (the default): neutral
      context, no network, trading unaffected.
    * ``mock`` — :class:`MockWorldMonitor` (deterministic placeholders; tests).
    * ``real`` — :class:`RealWorldMonitor`; raises
      :class:`WorldMonitorNotAvailableError` if no URL is configured.
    * ``auto`` — real *iff* a server URL is configured, else disabled. So with
      no server set up, ``auto`` stays fully offline and neutral.
    """
    mode = (mode or "disabled").lower()
    if mode == "disabled":
        return DisabledWorldMonitor()
    if mode == "mock":
        return MockWorldMonitor(**kwargs)
    if mode == "real":
        return RealWorldMonitor(**kwargs)
    if mode == "auto":
        if worldmonitor_configured(kwargs.get("base_url")):
            return RealWorldMonitor(**kwargs)
        return DisabledWorldMonitor()
    raise ValueError(
        f"unknown WorldMonitor mode {mode!r}; expected disabled | mock | real | auto"
    )


__all__ = [
    "RiskContext",
    "WorldMonitorClient",
    "DisabledWorldMonitor",
    "MockWorldMonitor",
    "RealWorldMonitor",
    "load_worldmonitor",
    "worldmonitor_configured",
    "WorldMonitorError",
    "WorldMonitorNotAvailableError",
    "WorldMonitorConnectionError",
    "ENV_URL",
    "ENV_API_KEY",
]
