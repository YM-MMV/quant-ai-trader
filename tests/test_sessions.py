"""Tests for session labelling (services/data_service/sessions.py).

Sessions depend only on each bar's own UTC timestamp, so they are causal by
construction. These tests pin the boundary behaviour and weekend handling.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from services.data_service.sessions import (
    ASIA,
    CLOSED,
    LONDON,
    NEW_YORK,
    OVERLAP,
    SESSION_LABELS,
    label_sessions,
    session_label,
)

# A weekday: 2024-01-03 is a Wednesday.
WED = "2024-01-03"


@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, ASIA),
        (7, ASIA),
        (8, OVERLAP),     # Asia ∩ London
        (9, LONDON),
        (12, LONDON),
        (13, OVERLAP),    # London ∩ New York
        (16, OVERLAP),
        (17, NEW_YORK),
        (21, NEW_YORK),
        (22, CLOSED),     # post-NY low-liquidity gap
        (23, CLOSED),
    ],
)
def test_weekday_hour_boundaries(hour, expected):
    ts = datetime(2024, 1, 3, hour, 30)
    assert session_label(ts) == expected


def test_saturday_is_closed_all_day():
    for hour in (0, 6, 12, 18, 23):
        assert session_label(datetime(2024, 1, 6, hour)) == CLOSED  # Saturday


def test_sunday_before_open_is_closed():
    # Sunday 2024-01-07: closed before 22:00 UTC, open (low-liq) after.
    assert session_label(datetime(2024, 1, 7, 21)) == CLOSED
    # 22:00 falls into the 22–24 gap label, also CLOSED here.
    assert session_label(datetime(2024, 1, 7, 22)) == CLOSED


def test_friday_after_close_is_closed():
    # Friday 2024-01-05 after 22:00 UTC -> weekly close.
    assert session_label(datetime(2024, 1, 5, 22)) == CLOSED
    assert session_label(datetime(2024, 1, 5, 21)) == NEW_YORK  # still open before


def test_tz_aware_converted_to_utc():
    # 04:00 US/Eastern (UTC-5) == 09:00 UTC -> London.
    ts = pd.Timestamp("2024-01-03 04:00", tz="US/Eastern")
    assert session_label(ts) == LONDON


def test_label_sessions_vectorised_matches_scalar():
    idx = pd.date_range("2024-01-03 00:00", periods=48, freq="30min")
    s = pd.Series(idx)
    vec = label_sessions(s)
    assert len(vec) == len(s)
    for ts, lab in zip(s, vec):
        assert lab == session_label(ts)
    assert set(vec.unique()).issubset(set(SESSION_LABELS))


def test_label_sessions_preserves_index():
    s = pd.Series(
        pd.to_datetime(["2024-01-03 09:00", "2024-01-03 14:00"]),
        index=[10, 20],
    )
    out = label_sessions(s)
    assert list(out.index) == [10, 20]
    assert out.loc[10] == LONDON
    assert out.loc[20] == OVERLAP


def test_every_label_reachable_across_a_week():
    idx = pd.date_range("2024-01-01 00:00", periods=24 * 7, freq="1h")
    labels = set(label_sessions(pd.Series(idx)).unique())
    assert labels == set(SESSION_LABELS)
