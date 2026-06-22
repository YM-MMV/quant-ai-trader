"""Performance metrics for a backtest run.

Pure, deterministic functions over a list of closed trades and an equity curve.
Trades are duck-typed: each must expose ``pnl`` (net, after costs),
``r_multiple`` (pnl / initial risk) and ``bars_held``. Keeping this module free
of the trade/backtester classes avoids a circular import.

Conventions for empty/degenerate inputs: counts are 0, ratios that would divide
by zero are ``0.0`` and unbounded ratios (e.g. profit factor with no losing
trades) are ``None`` rather than ``inf``. ``sharpe_placeholder`` is exactly that
— a per-bar mean/std ratio with no risk-free rate or annualisation, to be
replaced by a proper implementation later.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class BacktestMetrics:
    """Summary statistics for a completed backtest."""

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    average_r: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0           # positive magnitude of losing pnl
    net_profit: float = 0.0
    profit_factor: Optional[float] = None
    expectancy: float = 0.0
    max_drawdown: float = 0.0          # absolute, in equity units
    max_drawdown_pct: float = 0.0      # fraction of running peak
    sharpe_placeholder: Optional[float] = None
    largest_winner_contribution: float = 0.0  # biggest win / gross profit
    max_consecutive_losses: int = 0
    average_holding_bars: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def max_drawdown(equity_curve: Sequence[float]) -> tuple[float, float]:
    """Return ``(absolute, fractional)`` max drawdown of an equity curve."""
    peak = -math.inf
    abs_dd = 0.0
    pct_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drop = peak - value
        if drop > abs_dd:
            abs_dd = drop
        if peak > 0:
            pct_dd = max(pct_dd, drop / peak)
    return float(abs_dd), float(pct_dd)


def _sharpe_from_equity(equity_curve: Sequence[float]) -> Optional[float]:
    """Placeholder Sharpe: mean/std of per-bar equity returns (no annualising)."""
    if len(equity_curve) < 3:
        return None
    rets = []
    for prev, cur in zip(equity_curve, equity_curve[1:]):
        if prev != 0:
            rets.append(cur / prev - 1.0)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return float(mean / std)


def _max_consecutive_losses(pnls: Sequence[float]) -> int:
    streak = best = 0
    for pnl in pnls:
        if pnl < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def compute_metrics(
    trades: Sequence, equity_curve: Sequence[float]
) -> BacktestMetrics:
    """Compute :class:`BacktestMetrics` from closed trades + an equity curve."""
    n = len(trades)
    if n == 0:
        abs_dd, pct_dd = max_drawdown(equity_curve) if equity_curve else (0.0, 0.0)
        return BacktestMetrics(max_drawdown=abs_dd, max_drawdown_pct=pct_dd)

    pnls = [float(t.pnl) for t in trades]
    rs = [float(t.r_multiple) for t in trades]
    bars = [float(t.bars_held) for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # positive magnitude
    net_profit = sum(pnls)

    win_rate = len(wins) / n
    average_r = sum(rs) / n
    expectancy = net_profit / n
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    largest_winner_contribution = (max(wins) / gross_profit) if wins else 0.0
    abs_dd, pct_dd = max_drawdown(equity_curve) if equity_curve else (0.0, 0.0)

    return BacktestMetrics(
        total_trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=round(win_rate, 6),
        average_r=round(average_r, 6),
        gross_profit=round(gross_profit, 6),
        gross_loss=round(gross_loss, 6),
        net_profit=round(net_profit, 6),
        profit_factor=(round(profit_factor, 6) if profit_factor is not None else None),
        expectancy=round(expectancy, 6),
        max_drawdown=round(abs_dd, 6),
        max_drawdown_pct=round(pct_dd, 6),
        sharpe_placeholder=_sharpe_from_equity(equity_curve),
        largest_winner_contribution=round(largest_winner_contribution, 6),
        max_consecutive_losses=_max_consecutive_losses(pnls),
        average_holding_bars=round(sum(bars) / n, 6),
    )


__all__ = ["BacktestMetrics", "compute_metrics", "max_drawdown"]
