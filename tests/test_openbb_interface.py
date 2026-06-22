"""Tests for the optional OpenBB research-data interface (M17).

These run **without the optional ``openbb`` package**: the fetch path is
exercised by injecting a fake OpenBB client, and the normalisation/placeholder
logic is pure. Core behaviour never requires OpenBB to be installed.
"""
from datetime import datetime

import pandas as pd
import pytest

from services.data_service.storage import REQUIRED_COLUMNS, validate_candles
from services.data_service import openbb_data
from services.data_service.openbb_data import (
    OpenBBDataError,
    OpenBBNotAvailableError,
    get_asset_context_placeholder,
    get_historical_data,
    get_macro_data_placeholder,
    normalise_openbb_data,
    openbb_available,
)


# --------------------------------------------------------------------------- #
# Fakes: stand in for an OpenBB client / OBBject (no network, no package)
# --------------------------------------------------------------------------- #
class FakeOBBject:
    """Minimal stand-in for an OpenBB result object (exposes to_dataframe)."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def to_dataframe(self) -> pd.DataFrame:
        return self._df


class _Endpoint:
    def __init__(self, result, calls):
        self._result = result
        self._calls = calls

    def __call__(self, **kwargs):
        self._calls.append(kwargs)
        return self._result


class _PriceNamespace:
    def __init__(self, result, calls):
        self.historical = _Endpoint(result, calls)


class _AssetNamespace:
    def __init__(self, result, calls):
        self.price = _PriceNamespace(result, calls)


class FakeOBBClient:
    """Fake ``obb`` exposing ``<asset>.price.historical`` for each asset class."""

    def __init__(self, result):
        self.calls: list[dict] = []
        ns = _AssetNamespace(result, self.calls)
        self.currency = ns
        self.equity = ns
        self.crypto = ns
        self.index = ns


def sample_openbb_frame(n: int = 5) -> pd.DataFrame:
    base = datetime(2024, 1, 1)
    return pd.DataFrame({
        "date": [base.replace(day=1 + i) for i in range(n)],
        "open": [1.10 + i * 0.001 for i in range(n)],
        "high": [1.11 + i * 0.001 for i in range(n)],
        "low": [1.09 + i * 0.001 for i in range(n)],
        "close": [1.105 + i * 0.001 for i in range(n)],
        "volume": [1000 + i for i in range(n)],
    })


# --------------------------------------------------------------------------- #
# Availability (Done-when: OpenBB is optional, core tests don't need it)
# --------------------------------------------------------------------------- #
def test_openbb_not_available_in_this_env():
    # The optional package is not installed in CI/test environments.
    assert openbb_available() is False


def test_module_import_does_not_require_openbb():
    # The guarded import leaves ``obb`` as None when the package is absent.
    assert openbb_data.obb is None


def test_get_historical_data_raises_without_package_or_client():
    with pytest.raises(OpenBBNotAvailableError):
        get_historical_data("EURUSD", asset_class="currency")


# --------------------------------------------------------------------------- #
# Fetch path with an injected fake client
# --------------------------------------------------------------------------- #
def test_get_historical_data_with_injected_client():
    client = FakeOBBClient(FakeOBBject(sample_openbb_frame()))
    df = get_historical_data(
        "EURUSD",
        start="2024-01-01",
        end="2024-01-05",
        asset_class="currency",
        client=client,
    )
    validate_candles(df)
    assert list(df.columns) == list(REQUIRED_COLUMNS)
    assert (df["symbol"] == "EURUSD").all()
    assert (df["source"] == "openbb").all()
    # Request params are forwarded to the endpoint.
    assert client.calls[0]["symbol"] == "EURUSD"
    assert client.calls[0]["start_date"] == "2024-01-01"
    assert client.calls[0]["end_date"] == "2024-01-05"


def test_get_historical_data_forwards_provider_and_interval():
    client = FakeOBBClient(FakeOBBject(sample_openbb_frame()))
    get_historical_data(
        "AAPL", asset_class="equity", interval="1h", provider="yfinance",
        client=client,
    )
    call = client.calls[0]
    assert call["interval"] == "1h"
    assert call["provider"] == "yfinance"


def test_get_historical_data_unknown_asset_class():
    client = FakeOBBClient(FakeOBBject(sample_openbb_frame()))
    with pytest.raises(ValueError):
        get_historical_data("EURUSD", asset_class="bogus", client=client)


# --------------------------------------------------------------------------- #
# normalise_openbb_data — pure transform, several input shapes
# --------------------------------------------------------------------------- #
def test_normalise_from_dataframe():
    out = normalise_openbb_data(sample_openbb_frame(), symbol="EURUSD")
    validate_candles(out)
    assert list(out.columns) == list(REQUIRED_COLUMNS)
    # OpenBB volume maps to real_volume; broker microstructure is zeroed.
    assert (out["tick_volume"] == 0).all()
    assert (out["spread"] == 0).all()
    assert out["real_volume"].iloc[0] == 1000
    assert out["timeframe"].iloc[0] == "D1"


def test_normalise_from_obbject():
    out = normalise_openbb_data(
        FakeOBBject(sample_openbb_frame()), symbol="XAUUSD", timeframe="H1"
    )
    assert (out["symbol"] == "XAUUSD").all()
    assert (out["timeframe"] == "H1").all()


def test_normalise_from_list_of_records():
    records = sample_openbb_frame(3).to_dict("records")
    out = normalise_openbb_data(records, symbol="EURUSD")
    assert len(out) == 3


def test_normalise_from_datetime_index():
    df = sample_openbb_frame().set_index("date")
    out = normalise_openbb_data(df, symbol="EURUSD")
    assert len(out) == 5
    assert out["timestamp"].is_monotonic_increasing


def test_normalise_uppercase_columns():
    df = sample_openbb_frame()
    df.columns = [c.upper() for c in df.columns]
    out = normalise_openbb_data(df, symbol="EURUSD")
    validate_candles(out)


def test_normalise_without_volume_defaults_to_zero():
    df = sample_openbb_frame().drop(columns=["volume"])
    out = normalise_openbb_data(df, symbol="EURUSD")
    assert (out["real_volume"] == 0).all()


def test_normalise_sorts_by_timestamp():
    df = sample_openbb_frame().iloc[::-1].reset_index(drop=True)
    out = normalise_openbb_data(df, symbol="EURUSD")
    assert out["timestamp"].is_monotonic_increasing


def test_normalise_empty_raises():
    with pytest.raises(OpenBBDataError):
        normalise_openbb_data(pd.DataFrame(), symbol="EURUSD")


def test_normalise_none_raises():
    with pytest.raises(OpenBBDataError):
        normalise_openbb_data(None, symbol="EURUSD")


def test_normalise_missing_ohlc_raises():
    df = sample_openbb_frame().drop(columns=["close"])
    with pytest.raises(OpenBBDataError):
        normalise_openbb_data(df, symbol="EURUSD")


def test_normalise_no_timestamp_raises():
    df = sample_openbb_frame().drop(columns=["date"]).reset_index(drop=True)
    with pytest.raises(OpenBBDataError):
        normalise_openbb_data(df, symbol="EURUSD")


def test_normalise_bad_type_raises():
    with pytest.raises(OpenBBDataError):
        normalise_openbb_data(42, symbol="EURUSD")


# --------------------------------------------------------------------------- #
# Placeholders — stable offline contracts, no OpenBB required
# --------------------------------------------------------------------------- #
def test_macro_placeholder_contract():
    macro = get_macro_data_placeholder("CPI", country="US")
    assert macro["placeholder"] is True
    assert macro["indicator"] == "CPI"
    assert macro["country"] == "US"
    assert macro["source"] == "openbb"
    assert macro["data"] == []


def test_asset_context_placeholder_contract():
    ctx = get_asset_context_placeholder("XAUUSD", asset_class="commodity")
    assert ctx["placeholder"] is True
    assert ctx["symbol"] == "XAUUSD"
    assert ctx["asset_class"] == "commodity"
    assert ctx["related"] == []
    assert ctx["news"] == []
    assert ctx["fundamentals"] == {}
