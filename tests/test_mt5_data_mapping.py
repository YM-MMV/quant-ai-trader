"""Tests for MT5 market-data access (M14) — all MT5 calls are mocked.

The ``MetaTrader5`` package is Windows-only and talks to a live terminal, so
these tests patch ``services.data_service.mt5_data.mt5`` with a fake. They cover
symbol resolution (canonical -> broker alias), rate standardisation into the
canonical schema, the connect/symbols/tick/rates wrappers, Parquet round-trip,
and a guard that **no order-execution code exists** in this module.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from services.config_loader import load_symbols_config
from services.data_service import mt5_data
from services.data_service.storage import REQUIRED_COLUMNS, CandleStore

# Fields/dtype mirroring MetaTrader5.copy_rates_range output.
_RATE_DTYPE = [
    ("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
    ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
    ("real_volume", "<u8"),
]
# 1704067200 = 2024-01-01 00:00:00 UTC, 1704070800 = +1h
_RATES = np.array(
    [
        (1704067200, 1.1000, 1.1050, 1.0980, 1.1020, 120, 8, 0),
        (1704070800, 1.1020, 1.1075, 1.1010, 1.1060, 95, 7, 0),
    ],
    dtype=_RATE_DTYPE,
)


@pytest.fixture
def fake_mt5(monkeypatch):
    """Patch the module-level mt5 client with a MagicMock and return it."""
    client = MagicMock(name="MetaTrader5")
    # Realistic broker symbol list (uses dotted/GOLD aliases like a real broker).
    client.symbols_get.return_value = [
        SimpleNamespace(name="EURUSD."),
        SimpleNamespace(name="GBPUSD."),
        SimpleNamespace(name="GOLD"),
        SimpleNamespace(name="USDJPY"),
    ]
    client.initialize.return_value = True
    client.last_error.return_value = (0, "ok")
    client.copy_rates_range.return_value = _RATES
    client.symbol_info_tick.return_value = SimpleNamespace(
        time=1704070800, bid=1.1058, ask=1.1060, last=1.1059, volume=3,
    )
    monkeypatch.setattr(mt5_data, "mt5", client)
    return client


# --------------------------------------------------------------------------- #
# resolve_broker_symbol  (the core mapping)
# --------------------------------------------------------------------------- #
def test_resolve_picks_first_available_alias():
    # EURUSD aliases: [EURUSD, "EURUSD.", EURUSDm]; only "EURUSD." is offered.
    got = mt5_data.resolve_broker_symbol("EURUSD", available=["EURUSD.", "GOLD"])
    assert got == "EURUSD."


def test_resolve_maps_gold_alias():
    got = mt5_data.resolve_broker_symbol("XAUUSD", available=["GOLD", "EURUSD."])
    assert got == "GOLD"


def test_resolve_prefers_alias_order():
    # When several aliases are available, the config order wins (EURUSD first).
    got = mt5_data.resolve_broker_symbol(
        "EURUSD", available=["EURUSDm", "EURUSD.", "EURUSD"]
    )
    assert got == "EURUSD"


def test_resolve_unknown_symbol_raises():
    with pytest.raises(mt5_data.MT5SymbolError):
        mt5_data.resolve_broker_symbol("NOPE", available=["EURUSD"])


def test_resolve_no_available_alias_raises():
    with pytest.raises(mt5_data.MT5SymbolError):
        mt5_data.resolve_broker_symbol("EURUSD", available=["GOLD"])


def test_resolve_uses_live_symbols_when_available_omitted(fake_mt5):
    # available=None -> calls get_symbols() on the (mocked) terminal.
    assert mt5_data.resolve_broker_symbol("EURUSD") == "EURUSD."
    assert mt5_data.resolve_broker_symbol("XAUUSD") == "GOLD"
    fake_mt5.symbols_get.assert_called()


# --------------------------------------------------------------------------- #
# standardise_mt5_rates  (no MT5 needed — pure transform)
# --------------------------------------------------------------------------- #
def test_standardise_produces_canonical_schema():
    df = mt5_data.standardise_mt5_rates(_RATES, symbol="EURUSD", timeframe="H1")
    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert len(df) == 2


def test_standardise_converts_epoch_time():
    df = mt5_data.standardise_mt5_rates(_RATES, symbol="EURUSD", timeframe="H1")
    assert df["timestamp"].iloc[0] == datetime(2024, 1, 1, 0, 0, 0)
    assert df["timestamp"].iloc[1] == datetime(2024, 1, 1, 1, 0, 0)


def test_standardise_sets_provenance_columns():
    df = mt5_data.standardise_mt5_rates(
        _RATES, symbol="XAUUSD", timeframe="M15", source="mt5"
    )
    assert (df["symbol"] == "XAUUSD").all()
    assert (df["timeframe"] == "M15").all()
    assert (df["source"] == "mt5").all()


def test_standardise_preserves_ohlc_values():
    df = mt5_data.standardise_mt5_rates(_RATES, symbol="EURUSD", timeframe="H1")
    assert df["open"].iloc[0] == pytest.approx(1.1000)
    assert df["close"].iloc[1] == pytest.approx(1.1060)
    assert df["spread"].iloc[0] == 8


def test_standardise_accepts_list_of_dicts():
    rows = [{"time": 1704067200, "open": 1.0, "high": 1.1, "low": 0.9,
             "close": 1.05, "tick_volume": 10, "spread": 2, "real_volume": 0}]
    df = mt5_data.standardise_mt5_rates(rows, symbol="EURUSD", timeframe="H1")
    assert len(df) == 1
    assert list(df.columns) == list(REQUIRED_COLUMNS)


def test_standardise_empty_raises():
    empty = np.array([], dtype=_RATE_DTYPE)
    with pytest.raises(mt5_data.MT5DataError):
        mt5_data.standardise_mt5_rates(empty, symbol="EURUSD", timeframe="H1")


def test_standardise_none_raises():
    with pytest.raises(mt5_data.MT5DataError):
        mt5_data.standardise_mt5_rates(None, symbol="EURUSD", timeframe="H1")


# --------------------------------------------------------------------------- #
# Connection / symbols / rates / tick  (mocked client)
# --------------------------------------------------------------------------- #
def test_connect_success(fake_mt5):
    assert mt5_data.connect_to_mt5(settings=None) is True
    fake_mt5.initialize.assert_called_once()


def test_connect_failure_raises(fake_mt5):
    fake_mt5.initialize.return_value = False
    fake_mt5.last_error.return_value = (-6, "terminal not found")
    with pytest.raises(mt5_data.MT5ConnectionError):
        mt5_data.connect_to_mt5(settings=None)


def test_connect_passes_credentials(fake_mt5):
    mt5_data.connect_to_mt5(login="12345", password="pw", server="Demo",
                            terminal_path=None, settings=None)
    _, kwargs = fake_mt5.initialize.call_args
    assert kwargs["login"] == 12345
    assert kwargs["password"] == "pw"
    assert kwargs["server"] == "Demo"


def test_not_available_raises(monkeypatch):
    monkeypatch.setattr(mt5_data, "mt5", None)
    with pytest.raises(mt5_data.MT5NotAvailableError):
        mt5_data.connect_to_mt5(settings=None)
    with pytest.raises(mt5_data.MT5NotAvailableError):
        mt5_data.get_symbols()


def test_get_symbols_returns_names(fake_mt5):
    assert mt5_data.get_symbols() == ["EURUSD.", "GBPUSD.", "GOLD", "USDJPY"]


def test_get_rates_downloads_and_standardises(fake_mt5):
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 2)
    df = mt5_data.get_rates("EURUSD", "H1", start, end)
    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert len(df) == 2
    # broker symbol + the right timeframe constant were passed to MT5.
    args, _ = fake_mt5.copy_rates_range.call_args
    assert args[0] == "EURUSD."
    assert args[1] == fake_mt5.TIMEFRAME_H1


def test_get_rates_for_xauusd(fake_mt5):
    df = mt5_data.get_rates("XAUUSD", "H1", datetime(2024, 1, 1),
                            datetime(2024, 1, 2))
    assert (df["symbol"] == "XAUUSD").all()
    args, _ = fake_mt5.copy_rates_range.call_args
    assert args[0] == "GOLD"


def test_get_rates_no_data_raises(fake_mt5):
    fake_mt5.copy_rates_range.return_value = np.array([], dtype=_RATE_DTYPE)
    with pytest.raises(mt5_data.MT5DataError):
        mt5_data.get_rates("EURUSD", "H1", datetime(2024, 1, 1),
                           datetime(2024, 1, 2))


def test_get_rates_unknown_timeframe_raises(fake_mt5):
    with pytest.raises(ValueError):
        mt5_data.get_rates("EURUSD", "H7", datetime(2024, 1, 1),
                           datetime(2024, 1, 2))


def test_get_latest_tick(fake_mt5):
    tick = mt5_data.get_latest_tick("EURUSD")
    assert tick["symbol"] == "EURUSD"
    assert tick["broker_symbol"] == "EURUSD."
    assert tick["bid"] == pytest.approx(1.1058)
    assert tick["ask"] == pytest.approx(1.1060)
    assert tick["time"] == datetime(2024, 1, 1, 1, 0, 0)


def test_shutdown_is_safe_without_package(monkeypatch):
    monkeypatch.setattr(mt5_data, "mt5", None)
    mt5_data.shutdown_mt5()  # no-op, must not raise


def test_shutdown_calls_client(fake_mt5):
    mt5_data.shutdown_mt5()
    fake_mt5.shutdown.assert_called_once()


# --------------------------------------------------------------------------- #
# Persistence round-trip
# --------------------------------------------------------------------------- #
def test_save_rates_to_parquet_round_trip(tmp_path, fake_mt5):
    df = mt5_data.get_rates("EURUSD", "H1", datetime(2024, 1, 1),
                            datetime(2024, 1, 2))
    path = mt5_data.save_rates_to_parquet(
        df, base_dir=tmp_path, symbol="EURUSD", timeframe="H1"
    )
    assert path.is_file()

    reloaded = CandleStore(tmp_path).load("EURUSD", "H1")
    assert len(reloaded) == 2
    assert list(reloaded.columns)[:5] == list(REQUIRED_COLUMNS)[:5]
    assert reloaded["close"].iloc[1] == pytest.approx(1.1060)


def test_save_both_demo_symbols(tmp_path, fake_mt5):
    """Done-when: EURUSD and XAUUSD candles can be downloaded and saved."""
    available = mt5_data.get_symbols()
    for symbol in ("EURUSD", "XAUUSD"):
        df = mt5_data.get_rates("EURUSD" if symbol == "EURUSD" else "XAUUSD",
                                "H1", datetime(2024, 1, 1), datetime(2024, 1, 2),
                                available=available)
        path = mt5_data.save_rates_to_parquet(df, base_dir=tmp_path,
                                              symbol=symbol, timeframe="H1")
        assert path.is_file()
    assert (tmp_path / "EURUSD" / "H1.parquet").is_file()
    assert (tmp_path / "XAUUSD" / "H1.parquet").is_file()


# --------------------------------------------------------------------------- #
# Safety: no order-execution code exists in this module  (Done-when)
# --------------------------------------------------------------------------- #
def test_module_has_no_order_execution_api():
    for forbidden in ("order_send", "order_check", "positions_get",
                      "Buy", "Sell", "send_order"):
        assert not hasattr(mt5_data, forbidden), \
            f"data-only module must not expose {forbidden!r}"


def test_module_source_has_no_order_send():
    import inspect
    source = inspect.getsource(mt5_data)
    # Guard against an actual call/use (parens or attribute access), while still
    # allowing the docstring to *document* that order sending is absent.
    for pattern in ("order_send(", ".order_send", "OrderSend", "order_check("):
        assert pattern not in source, f"M14 is data-only: {pattern!r} not allowed"


def test_config_symbols_have_expected_aliases():
    # Sanity: the mapping the tests rely on matches config/symbols.yaml.
    cfg = load_symbols_config()
    assert "EURUSD." in cfg.symbols["EURUSD"].broker_aliases
    assert "GOLD" in cfg.symbols["XAUUSD"].broker_aliases
