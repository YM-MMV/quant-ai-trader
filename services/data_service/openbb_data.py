"""OpenBB research-data access (M17) — **optional, research only**.

This is an *optional* bridge to the
`OpenBB Platform <https://github.com/OpenBB-finance/OpenBB>`_ used for
**supplementary research data** (historical prices for cross-checks, and the
macro / asset-context placeholders below). It is deliberately **not** an
execution-aligned source.

Design rules (from the milestone):

* **Optional.** The ``openbb`` package is heavy and is imported lazily/guardedly,
  exactly like the MT5 bridge. This module (and the pure transforms below) import
  cleanly anywhere; core tests never need OpenBB and mock it instead.
* **Research, not execution.** Nothing here sends orders or feeds the live
  trading path. For forex/gold the *execution-aligned* source stays MetaTrader 5
  (:mod:`services.data_service.mt5_data`); OpenBB is supplementary only. Frames
  produced here carry ``source="openbb"`` so their provenance is never confused
  with broker data.
* **Pure normalisation.** :func:`normalise_openbb_data` is a pure transform
  (no OpenBB needed) that maps an OpenBB result into the project's canonical
  candle schema (:data:`services.data_service.storage.REQUIRED_COLUMNS`).

The two ``*_placeholder`` functions define a stable interface for macro and
asset-context research data without requiring OpenBB yet — they return clearly
marked stubs so downstream code can be written against a fixed contract.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import pandas as pd

from services.data_service.storage import REQUIRED_COLUMNS

# Lazy, guarded import: the package is heavy and optional. Tests patch
# ``openbb_data.obb`` (or inject a client), so guard the import here and keep
# this module importable with OpenBB absent.
try:  # pragma: no cover - import side effect depends on the host
    from openbb import obb as _obb
except Exception:  # pragma: no cover
    _obb = None

obb = _obb

DEFAULT_SOURCE = "openbb"
DEFAULT_TIMEFRAME = "D1"

# Canonical asset class -> OpenBB endpoint namespace ("<ns>.price.historical").
_ASSET_NAMESPACES: dict[str, str] = {
    "currency": "currency",
    "forex": "currency",
    "fx": "currency",
    "equity": "equity",
    "stock": "equity",
    "crypto": "crypto",
    "index": "index",
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class OpenBBError(RuntimeError):
    """Base class for OpenBB research-data failures."""


class OpenBBNotAvailableError(OpenBBError):
    """The optional ``openbb`` package is not installed."""


class OpenBBDataError(OpenBBError):
    """OpenBB returned no usable data, or it could not be normalised."""


def openbb_available() -> bool:
    """True if the optional ``openbb`` package can be imported (loads nothing)."""
    try:  # pragma: no cover - depends on host having the optional package
        import importlib.util

        return importlib.util.find_spec("openbb") is not None
    except Exception:  # pragma: no cover
        return False


def _require_obb(client: Any = None) -> Any:
    """Return the OpenBB client (injected ``client`` wins), or raise clearly."""
    if client is not None:
        return client
    if obb is None:
        raise OpenBBNotAvailableError(
            "the optional 'openbb' package is not installed; OpenBB is a "
            "research-only data layer (see docs/OPENBB_SETUP.md). Use MT5 for "
            "execution-aligned forex/gold data, or pass a mock client."
        )
    return obb


def _historical_endpoint(client: Any, asset_class: str) -> Any:
    """Resolve ``client.<ns>.price.historical`` for a canonical asset class."""
    key = (asset_class or "").lower()
    ns = _ASSET_NAMESPACES.get(key)
    if ns is None:
        raise ValueError(
            f"unknown asset_class {asset_class!r}; "
            f"expected one of {sorted(_ASSET_NAMESPACES)}"
        )
    namespace = getattr(client, ns, None)
    price = getattr(namespace, "price", None)
    endpoint = getattr(price, "historical", None)
    if endpoint is None:
        raise OpenBBError(
            f"OpenBB client has no '{ns}.price.historical' endpoint"
        )
    return endpoint


# --------------------------------------------------------------------------- #
# Historical research data
# --------------------------------------------------------------------------- #
def get_historical_data(
    symbol: str,
    start: Optional[str | datetime] = None,
    end: Optional[str | datetime] = None,
    *,
    interval: str = "1d",
    asset_class: str = "currency",
    provider: Optional[str] = None,
    timeframe: str = DEFAULT_TIMEFRAME,
    source: str = DEFAULT_SOURCE,
    client: Any = None,
) -> pd.DataFrame:
    """Fetch supplementary historical candles for ``symbol`` from OpenBB.

    Returns a DataFrame in the canonical candle schema (with ``source="openbb"``
    so it is never mistaken for broker/execution data). Raises
    :class:`OpenBBNotAvailableError` if OpenBB is absent (and no ``client`` is
    injected), or :class:`OpenBBDataError` if nothing usable comes back.

    This is **research only** — for forex/gold execution alignment use
    :func:`services.data_service.mt5_data.get_rates` instead.
    """
    obb_client = _require_obb(client)
    endpoint = _historical_endpoint(obb_client, asset_class)

    kwargs: dict[str, Any] = {"symbol": symbol, "interval": interval}
    if start is not None:
        kwargs["start_date"] = start
    if end is not None:
        kwargs["end_date"] = end
    if provider is not None:
        kwargs["provider"] = provider

    raw = endpoint(**kwargs)
    return normalise_openbb_data(
        raw, symbol=symbol, timeframe=timeframe, source=source
    )


# --------------------------------------------------------------------------- #
# Macro / asset-context placeholders (no OpenBB required)
# --------------------------------------------------------------------------- #
def get_macro_data_placeholder(
    indicator: str = "CPI",
    *,
    country: str = "US",
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    """Stable placeholder for macroeconomic research data.

    Returns a clearly marked stub (``placeholder=True``, empty ``data``) so
    downstream code can target a fixed contract before the real OpenBB macro
    integration lands. Pure and offline — requires no OpenBB install.
    """
    return {
        "indicator": indicator,
        "country": country,
        "source": source,
        "placeholder": True,
        "data": [],
        "note": (
            "macro data integration is not implemented yet; this is a stable "
            "placeholder contract (see docs/OPENBB_SETUP.md)"
        ),
    }


def get_asset_context_placeholder(
    symbol: str,
    *,
    asset_class: str = "currency",
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    """Stable placeholder for per-asset research context (news/fundamentals).

    Returns a marked stub describing the future contract — related instruments,
    recent news, and fundamentals — without contacting OpenBB. Research only;
    nothing here influences execution.
    """
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "source": source,
        "placeholder": True,
        "related": [],
        "news": [],
        "fundamentals": {},
        "note": (
            "asset-context integration is not implemented yet; this is a stable "
            "placeholder contract (see docs/OPENBB_SETUP.md)"
        ),
    }


# --------------------------------------------------------------------------- #
# Normalisation (pure — no OpenBB needed)
# --------------------------------------------------------------------------- #
def _to_dataframe(raw: Any) -> pd.DataFrame:
    """Coerce an OpenBB result into a DataFrame.

    Accepts an ``OBBject`` (anything exposing ``to_dataframe()``), a DataFrame,
    or a list of record dicts.
    """
    if raw is None:
        raise OpenBBDataError("cannot normalise OpenBB data: got None")
    to_df = getattr(raw, "to_dataframe", None)
    if callable(to_df):
        return to_df()
    if isinstance(raw, pd.DataFrame):
        return raw
    if isinstance(raw, list):
        return pd.DataFrame(raw)
    raise OpenBBDataError(
        f"cannot normalise OpenBB data of type {type(raw).__name__}; expected "
        "an OBBject, DataFrame, or list of records"
    )


def normalise_openbb_data(
    raw: Any,
    *,
    symbol: str,
    timeframe: str = DEFAULT_TIMEFRAME,
    source: str = DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Normalise an OpenBB historical result into the canonical candle schema.

    ``raw`` may be an ``OBBject``, a DataFrame, or a list of records. The
    timestamp comes from a ``DatetimeIndex`` or a ``date``/``timestamp`` column.
    OpenBB has no broker microstructure, so ``tick_volume`` and ``spread`` are 0
    and ``real_volume`` carries OpenBB's ``volume`` (0 if absent). Output columns
    are exactly :data:`REQUIRED_COLUMNS` with ``source`` (default ``"openbb"``).
    """
    df = _to_dataframe(raw)
    if df is None or len(df) == 0:
        raise OpenBBDataError("OpenBB returned no rows")

    df = df.copy()
    # Lower-case columns for resilient matching against OpenBB's schema.
    df.columns = [str(c).lower() for c in df.columns]

    out = pd.DataFrame()

    # Timestamp: prefer an explicit column, else a datetime index.
    if "date" in df.columns:
        out["timestamp"] = pd.to_datetime(df["date"])
    elif "timestamp" in df.columns:
        out["timestamp"] = pd.to_datetime(df["timestamp"])
    elif isinstance(df.index, pd.DatetimeIndex):
        out["timestamp"] = pd.to_datetime(df.index)
    else:
        raise OpenBBDataError(
            "OpenBB data has no 'date'/'timestamp' column or datetime index; "
            f"got columns {list(df.columns)}"
        )
    out = out.reset_index(drop=True)

    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise OpenBBDataError(f"OpenBB data missing required field {col!r}")
        out[col] = df[col].astype(float).to_numpy()

    out["tick_volume"] = 0
    out["spread"] = 0
    if "volume" in df.columns:
        out["real_volume"] = (
            pd.to_numeric(df["volume"], errors="coerce")
            .fillna(0)
            .astype("int64")
            .to_numpy()
        )
    else:
        out["real_volume"] = 0
    out["symbol"] = symbol
    out["timeframe"] = timeframe
    out["source"] = source

    out = out[list(REQUIRED_COLUMNS)]
    return out.sort_values("timestamp").reset_index(drop=True)


__all__ = [
    "get_historical_data",
    "get_macro_data_placeholder",
    "get_asset_context_placeholder",
    "normalise_openbb_data",
    "openbb_available",
    "OpenBBError",
    "OpenBBNotAvailableError",
    "OpenBBDataError",
    "DEFAULT_SOURCE",
    "DEFAULT_TIMEFRAME",
]
