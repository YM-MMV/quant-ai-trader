"""Trading-session labelling for candles.

Each candle is labelled with the FX session active at its timestamp. Labels
depend only on the bar's own timestamp (never future bars), so they introduce
no look-ahead bias.

Timestamps are interpreted as **UTC** (tz-aware inputs are converted to UTC;
naive inputs are assumed to already be UTC). Session boundaries are simplified,
documented approximations of the major FX centres — good enough for features
and signals, not a trading-hours calendar.

UTC hour layout on a weekday:

    00:00–08:00  Asia            (Tokyo/Sydney)
    08:00–09:00  overlap         (Asia ∩ London)
    09:00–13:00  London
    13:00–17:00  overlap         (London ∩ New York)
    17:00–22:00  New York
    22:00–24:00  closed          (post-NY, low-liquidity gap)

Weekend: the FX week runs from Sunday 22:00 UTC to Friday 22:00 UTC. Outside
that window everything is ``closed``.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

ASIA = "Asia"
LONDON = "London"
NEW_YORK = "New York"
OVERLAP = "overlap"
CLOSED = "closed"

SESSION_LABELS = (ASIA, LONDON, NEW_YORK, OVERLAP, CLOSED)


def _to_utc_naive(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _is_weekend_closed(weekday: int, hour: int) -> bool:
    """FX week: open Sun 22:00 UTC → close Fri 22:00 UTC (weekday: Mon=0..Sun=6)."""
    if weekday == 5:  # Saturday — always closed
        return True
    if weekday == 6 and hour < 22:  # Sunday before the weekly open
        return True
    if weekday == 4 and hour >= 22:  # Friday after the weekly close
        return True
    return False


def _label_from_parts(weekday: int, hour: int) -> str:
    if _is_weekend_closed(weekday, hour):
        return CLOSED
    if hour < 8:
        return ASIA
    if hour < 9:
        return OVERLAP  # Asia ∩ London
    if hour < 13:
        return LONDON
    if hour < 17:
        return OVERLAP  # London ∩ New York
    if hour < 22:
        return NEW_YORK
    return CLOSED  # 22:00–24:00 low-liquidity gap


def session_label(ts: datetime | pd.Timestamp) -> str:
    """Return the session label for a single timestamp (treated as UTC)."""
    t = _to_utc_naive(ts)
    return _label_from_parts(t.weekday(), t.hour)


def label_sessions(timestamps) -> pd.Series:
    """Vectorised session labels for a Series/sequence of timestamps (UTC).

    Returns a ``pd.Series`` of labels aligned to the input. Uses only each
    timestamp's own value — no rolling/lagging — so it is inherently causal.
    """
    ts = pd.to_datetime(pd.Series(timestamps).reset_index(drop=True))
    # Normalise tz: convert aware → UTC → naive; leave naive as-is.
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)

    hour = ts.dt.hour.to_numpy()
    wd = ts.dt.weekday.to_numpy()

    weekend = (
        (wd == 5)
        | ((wd == 6) & (hour < 22))
        | ((wd == 4) & (hour >= 22))
    )

    # Order matters: weekend overrides the intraday layout.
    labels = np.select(
        [
            weekend,
            hour < 8,
            hour < 9,
            hour < 13,
            hour < 17,
            hour < 22,
        ],
        [CLOSED, ASIA, OVERLAP, LONDON, OVERLAP, NEW_YORK],
        default=CLOSED,  # 22:00–24:00
    )
    result = pd.Series(labels, name="session")
    if isinstance(timestamps, pd.Series):
        result.index = timestamps.index
    return result


__all__ = [
    "ASIA",
    "LONDON",
    "NEW_YORK",
    "OVERLAP",
    "CLOSED",
    "SESSION_LABELS",
    "session_label",
    "label_sessions",
]
