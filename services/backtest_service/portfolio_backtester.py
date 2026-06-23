"""Combine several single-strategy backtests into one portfolio.

The single-symbol :class:`~services.backtest_service.simple_backtester.SimpleBacktester`
answers "is this *one* strategy any good?". This module answers the question that
actually drives risk-adjusted return: "what happens when I run *several* of them
together?". Diversifying across weakly-correlated strategies/symbols is the single
biggest lever on portfolio Sharpe — far bigger than tuning any one strategy.

What it does, deterministically and with no network/broker/AI:

* Takes a set of *legs* (each a backtest equity curve, e.g. a ``BacktestReport``).
* Derives each leg's per-bar returns and weights them — **inverse-volatility** by
  default (a calmer leg gets more capital), or ``equal``.
* Blends the weighted returns into one portfolio equity curve and reports
  portfolio-level metrics, including the annualised :func:`annualised_sharpe`.
* Surfaces the pairwise return **correlation** so you can see *why* the blend
  helps (or doesn't).

Honest limitations (documented, not hidden):

* Legs are combined on **per-bar returns aligned to the shortest leg's tail**.
  Feed legs from the same timeframe/length (the common case) for a clean blend;
  mixed lengths are truncated to the shortest, most-recent overlap.
* This is a *return-stream* blend (like allocating capital across sleeves), not a
  margin-aware multi-symbol order simulator. It tells you about risk-adjusted
  return shape, not broker margin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from services.backtest_service.metrics import annualised_sharpe, max_drawdown

WeightingScheme = str  # "inverse_vol" | "equal"


# --------------------------------------------------------------------------- #
# Inputs / outputs
# --------------------------------------------------------------------------- #
@dataclass
class LegInput:
    """One portfolio sleeve: a name plus its per-bar equity curve.

    ``trades`` is optional and only used for an aggregate trade count. Any object
    exposing ``equity_curve`` (and optionally ``trades``) — e.g. a
    ``BacktestReport`` — can be adapted via :meth:`from_report`.
    """

    name: str
    equity_curve: Sequence[float]
    n_trades: int = 0

    @classmethod
    def from_report(cls, name: str, report) -> "LegInput":
        return cls(
            name=name,
            equity_curve=list(report.equity_curve),
            n_trades=len(getattr(report, "trades", []) or []),
        )


@dataclass
class LegResult:
    name: str
    weight: float
    sharpe_ratio: Optional[float]
    volatility: float            # std of the leg's per-bar returns
    total_return: float          # leg's own end/start - 1
    n_trades: int


@dataclass
class PortfolioReport:
    legs: list[LegResult]
    equity_curve: list[float]
    sharpe_ratio: Optional[float]
    max_drawdown: float
    max_drawdown_pct: float
    total_return: float
    volatility: float
    n_bars: int
    weighting: WeightingScheme
    correlation: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def weights(self) -> dict[str, float]:
        return {leg.name: leg.weight for leg in self.legs}


# --------------------------------------------------------------------------- #
# Helpers (pure)
# --------------------------------------------------------------------------- #
def _returns(equity: Sequence[float]) -> list[float]:
    """Per-bar simple returns; bars off a zero/None base contribute 0.0."""
    out: list[float] = []
    for prev, cur in zip(equity, equity[1:]):
        out.append((cur / prev - 1.0) if prev else 0.0)
    return out


def _std(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def _correlation(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def _weights(
    return_series: list[list[float]], scheme: WeightingScheme
) -> list[float]:
    """Return normalised weights for each leg under ``scheme``.

    ``inverse_vol``: w_i ∝ 1/σ_i (flat legs, σ=0, get zero weight). Falls back to
    equal weights when every leg is flat. ``equal``: 1/N each.
    """
    n = len(return_series)
    if n == 0:
        return []
    if scheme == "equal":
        return [1.0 / n] * n

    if scheme != "inverse_vol":
        raise ValueError(f"unknown weighting scheme: {scheme!r}")

    inv = [(1.0 / s if s > 0 else 0.0) for s in (_std(r) for r in return_series)]
    total = sum(inv)
    if total <= 0:                       # all legs flat → equal weight
        return [1.0 / n] * n
    return [w / total for w in inv]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def run_portfolio(
    legs: Sequence[LegInput],
    *,
    weighting: WeightingScheme = "inverse_vol",
    initial_equity: float = 10_000.0,
    periods_per_year: Optional[float] = None,
) -> PortfolioReport:
    """Blend leg equity curves into one portfolio and report its metrics.

    ``periods_per_year`` (e.g. ~6_200 for H1 forex) annualises the portfolio
    Sharpe; omit it and ``sharpe_ratio`` stays ``None`` rather than mislead.
    """
    if not legs:
        raise ValueError("at least one leg is required")

    # Per-leg returns, aligned to the shortest leg's most-recent tail.
    raw = [_returns(leg.equity_curve) for leg in legs]
    horizon = min((len(r) for r in raw), default=0)
    if horizon < 1:
        raise ValueError("legs must have at least two equity points each")
    series = [r[-horizon:] for r in raw]

    weights = _weights(series, weighting)

    # Blend weighted per-bar returns into a portfolio equity curve.
    equity = [initial_equity]
    port_returns: list[float] = []
    for t in range(horizon):
        blended = sum(w * series[i][t] for i, w in enumerate(weights))
        port_returns.append(blended)
        equity.append(equity[-1] * (1.0 + blended))

    abs_dd, pct_dd = max_drawdown(equity)
    leg_results = [
        LegResult(
            name=leg.name,
            weight=round(weights[i], 6),
            sharpe_ratio=annualised_sharpe(leg.equity_curve, periods_per_year),
            volatility=round(_std(series[i]), 8),
            total_return=round(_total_return(leg.equity_curve), 8),
            n_trades=leg.n_trades,
        )
        for i, leg in enumerate(legs)
    ]

    correlation: dict[tuple[str, str], float] = {}
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            correlation[(legs[i].name, legs[j].name)] = round(
                _correlation(series[i], series[j]), 6
            )

    return PortfolioReport(
        legs=leg_results,
        equity_curve=equity,
        sharpe_ratio=annualised_sharpe(equity, periods_per_year),
        max_drawdown=round(abs_dd, 6),
        max_drawdown_pct=round(pct_dd, 6),
        total_return=round(equity[-1] / equity[0] - 1.0, 8),
        volatility=round(_std(port_returns), 8),
        n_bars=horizon,
        weighting=weighting,
        correlation=correlation,
    )


def _total_return(equity: Sequence[float]) -> float:
    if len(equity) < 2 or not equity[0]:
        return 0.0
    return equity[-1] / equity[0] - 1.0


def backtest_legs(specs, *, config=None) -> list[LegInput]:
    """Convenience: run ``(name, candles, strategy)`` specs into :class:`LegInput`.

    Lazily imports :class:`SimpleBacktester` so the pure portfolio math above has
    no dependency on the backtester engine.
    """
    from services.backtest_service.simple_backtester import SimpleBacktester

    out: list[LegInput] = []
    for name, candles, strategy in specs:
        report = SimpleBacktester(config).run(candles, strategy)
        out.append(LegInput.from_report(name, report))
    return out


__all__ = [
    "LegInput",
    "LegResult",
    "PortfolioReport",
    "run_portfolio",
    "backtest_legs",
]
