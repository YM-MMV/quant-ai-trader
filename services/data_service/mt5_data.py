"""MetaTrader 5 market-data access (M14) — **data only, never orders**.

This is the read-only bridge to a MetaTrader 5 terminal: connect, list symbols,
resolve a canonical symbol to the broker's actual name, pull historical rates
and the latest tick, normalise rates into the project's canonical candle schema,
and persist them to Parquet.

It deliberately contains **no order-sending or execution code** — no
``order_send``, no position management. Trading stays gated behind the
RiskManager + paper executor in other services; this module only fetches data.

Runtime notes:

* The ``MetaTrader5`` package is **Windows-only** and talks to a locally running
  terminal. It is imported lazily/guardedly so this module (and the pure
  transforms below) import cleanly anywhere — tests mock the package by patching
  :data:`mt5`.
* Credentials come from ``.env`` (see :class:`~services.config_loader.Settings`)
  and are only needed for an explicit demo-account login; ``mt5.initialize()``
  often attaches to an already-open terminal without them.

Canonical output schema matches :data:`services.data_service.storage.REQUIRED_COLUMNS`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from services.config_loader import PROJECT_ROOT, SymbolsConfig, load_symbols_config
from services.data_service.sample_data import TIMEFRAME_MINUTES
from services.data_service.storage import REQUIRED_COLUMNS, CandleStore

# Lazy, guarded import: the package only exists on Windows with a terminal.
# Tests patch ``mt5_data.mt5`` with a mock, so guard the import here.
try:  # pragma: no cover - import side effect depends on the host
    import MetaTrader5 as _MetaTrader5
except Exception:  # pragma: no cover
    _MetaTrader5 = None

mt5 = _MetaTrader5

DEFAULT_SOURCE = "mt5"
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class MT5Error(RuntimeError):
    """Base class for MT5 data-access failures."""


class MT5NotAvailableError(MT5Error):
    """The MetaTrader5 package is not importable (e.g. not on Windows)."""


class MT5ConnectionError(MT5Error):
    """``mt5.initialize`` failed to connect to a terminal."""


class MT5SymbolError(MT5Error):
    """A symbol could not be resolved to an available broker symbol."""


class MT5DataError(MT5Error):
    """The terminal returned no usable data for a request."""


def _require_mt5() -> Any:
    """Return the MetaTrader5 module, or raise a clear error if unavailable."""
    if mt5 is None:
        raise MT5NotAvailableError(
            "MetaTrader5 package is not available; MT5 data collection runs "
            "locally on Windows only (see milestone 14 constraints)"
        )
    return mt5


# --------------------------------------------------------------------------- #
# Connection lifecycle
# --------------------------------------------------------------------------- #
def connect_to_mt5(
    *,
    login: Optional[int | str] = None,
    password: Optional[str] = None,
    server: Optional[str] = None,
    terminal_path: Optional[str] = None,
    settings: Any = None,
) -> bool:
    """Initialise the connection to a (demo) MT5 terminal.

    Missing credentials fall back to ``.env`` via
    :class:`~services.config_loader.Settings`. With no credentials at all,
    ``mt5.initialize()`` attaches to an already-running terminal. Raises
    :class:`MT5ConnectionError` on failure.
    """
    client = _require_mt5()

    if settings is None:
        try:
            from services.config_loader import get_settings
            settings = get_settings()
        except Exception:  # pragma: no cover - .env optional
            settings = None

    if settings is not None:
        login = login if login is not None else settings.mt5_login
        password = password if password is not None else settings.mt5_password
        server = server if server is not None else settings.mt5_server
        terminal_path = terminal_path if terminal_path is not None else settings.mt5_terminal_path

    kwargs: dict[str, Any] = {}
    if terminal_path:
        kwargs["path"] = terminal_path
    if login:
        kwargs["login"] = int(login)
    if password:
        kwargs["password"] = password
    if server:
        kwargs["server"] = server

    ok = client.initialize(**kwargs)
    if not ok:
        raise MT5ConnectionError(f"mt5.initialize failed: {client.last_error()}")
    return True


def shutdown_mt5() -> None:
    """Close the MT5 connection if the package is available (no-op otherwise)."""
    if mt5 is not None:
        mt5.shutdown()


# --------------------------------------------------------------------------- #
# Symbols
# --------------------------------------------------------------------------- #
def get_symbols() -> list[str]:
    """Return the broker symbol names exposed by the connected terminal."""
    client = _require_mt5()
    symbols = client.symbols_get()
    if symbols is None:
        raise MT5DataError(f"mt5.symbols_get returned no symbols: {client.last_error()}")
    return [s.name for s in symbols]


def resolve_broker_symbol(
    symbol: str,
    *,
    available: Optional[list[str]] = None,
    symbols_config: Optional[SymbolsConfig] = None,
) -> str:
    """Map a canonical symbol to the broker's actual name.

    Tries the canonical symbol's ``broker_aliases`` (from ``config/symbols.yaml``)
    in order and returns the first one the broker actually offers. ``available``
    may be supplied to avoid querying the terminal (and for tests); otherwise the
    live symbol list is fetched. Raises :class:`MT5SymbolError` if the symbol is
    unknown or none of its aliases are available.
    """
    cfg = symbols_config or load_symbols_config()
    if symbol not in cfg.symbols:
        raise MT5SymbolError(
            f"unknown canonical symbol {symbol!r}; known: {sorted(cfg.symbols)}"
        )
    aliases = cfg.symbols[symbol].broker_aliases

    if available is None:
        available = get_symbols()
    available_set = set(available)

    for alias in aliases:
        if alias in available_set:
            return alias
    raise MT5SymbolError(
        f"none of {symbol!r} broker aliases {aliases} are available in MT5"
    )


# --------------------------------------------------------------------------- #
# Rates & ticks
# --------------------------------------------------------------------------- #
def _mt5_timeframe(client: Any, timeframe: str) -> Any:
    """Translate a canonical timeframe (``H1``) to the MT5 ``TIMEFRAME_*`` const."""
    tf = timeframe.upper()
    if tf not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; known: {sorted(TIMEFRAME_MINUTES)}"
        )
    const = getattr(client, f"TIMEFRAME_{tf}", None)
    if const is None:
        raise ValueError(f"MT5 has no timeframe constant TIMEFRAME_{tf}")
    return const


def get_rates(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    available: Optional[list[str]] = None,
    symbols_config: Optional[SymbolsConfig] = None,
    source: str = DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Download historical candles for ``symbol`` between ``start`` and ``end``.

    Returns a DataFrame in the canonical candle schema. Raises
    :class:`MT5DataError` if the terminal returns nothing.
    """
    if end < start:
        raise ValueError("end must be >= start")
    client = _require_mt5()
    broker = resolve_broker_symbol(
        symbol, available=available, symbols_config=symbols_config
    )
    tf = _mt5_timeframe(client, timeframe)
    rates = client.copy_rates_range(broker, tf, start, end)
    if rates is None or len(rates) == 0:
        raise MT5DataError(
            f"no rates for {symbol} ({broker}) {timeframe} {start}..{end}: "
            f"{client.last_error()}"
        )
    return standardise_mt5_rates(rates, symbol=symbol, timeframe=timeframe, source=source)


def get_latest_tick(
    symbol: str,
    *,
    available: Optional[list[str]] = None,
    symbols_config: Optional[SymbolsConfig] = None,
) -> dict[str, Any]:
    """Return the latest tick for ``symbol`` as a normalised dict."""
    client = _require_mt5()
    broker = resolve_broker_symbol(
        symbol, available=available, symbols_config=symbols_config
    )
    tick = client.symbol_info_tick(broker)
    if tick is None:
        raise MT5DataError(
            f"no tick for {symbol} ({broker}): {client.last_error()}"
        )
    epoch = getattr(tick, "time", None)
    return {
        "symbol": symbol,
        "broker_symbol": broker,
        "time": (datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)
                 if epoch is not None else None),
        "bid": getattr(tick, "bid", None),
        "ask": getattr(tick, "ask", None),
        "last": getattr(tick, "last", None),
        "volume": getattr(tick, "volume", None),
    }


# --------------------------------------------------------------------------- #
# Normalisation & persistence (pure — no MT5 needed)
# --------------------------------------------------------------------------- #
def standardise_mt5_rates(
    rates: Any,
    *,
    symbol: str,
    timeframe: str,
    source: str = DEFAULT_SOURCE,
) -> pd.DataFrame:
    """Normalise MT5 rates into the canonical candle schema.

    ``rates`` is whatever ``copy_rates_range`` returns — a NumPy structured
    array (fields ``time, open, high, low, close, tick_volume, spread,
    real_volume``), a list of records, or a DataFrame. MT5's ``time`` is epoch
    seconds (UTC); it becomes a naive-UTC ``timestamp`` column to match the rest
    of the project. ``symbol``/``timeframe``/``source`` provenance columns are
    added. Output columns are exactly :data:`REQUIRED_COLUMNS`.
    """
    if rates is None:
        raise MT5DataError("cannot standardise rates: got None")
    df = pd.DataFrame(rates)
    if len(df) == 0:
        raise MT5DataError("cannot standardise rates: empty rates")

    if "time" not in df.columns:
        raise MT5DataError(f"rates missing 'time' field; got columns {list(df.columns)}")

    out = pd.DataFrame()
    out["timestamp"] = pd.to_datetime(df["time"], unit="s")
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise MT5DataError(f"rates missing required field {col!r}")
        out[col] = df[col].astype(float)
    out["tick_volume"] = df["tick_volume"].astype("int64") if "tick_volume" in df else 0
    out["spread"] = df["spread"].astype("int64") if "spread" in df else 0
    out["real_volume"] = df["real_volume"].astype("int64") if "real_volume" in df else 0
    out["symbol"] = symbol
    out["timeframe"] = timeframe
    out["source"] = source

    out = out[list(REQUIRED_COLUMNS)]
    return out.sort_values("timestamp").reset_index(drop=True)


def save_rates_to_parquet(
    df: pd.DataFrame,
    *,
    base_dir: Optional[str | Path] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> Path:
    """Persist standardised candles to Parquet via :class:`CandleStore`.

    Layout: ``<base_dir>/<symbol>/<timeframe>.parquet`` (default base
    ``data/raw``). ``symbol``/``timeframe`` are inferred from the frame when
    omitted. Returns the written path.
    """
    store = CandleStore(base_dir if base_dir is not None else DEFAULT_RAW_DIR)
    return store.save(df, symbol, timeframe)


__all__ = [
    "connect_to_mt5",
    "shutdown_mt5",
    "get_symbols",
    "resolve_broker_symbol",
    "get_rates",
    "get_latest_tick",
    "standardise_mt5_rates",
    "save_rates_to_parquet",
    "MT5Error",
    "MT5NotAvailableError",
    "MT5ConnectionError",
    "MT5SymbolError",
    "MT5DataError",
    "DEFAULT_RAW_DIR",
    "DEFAULT_SOURCE",
]
