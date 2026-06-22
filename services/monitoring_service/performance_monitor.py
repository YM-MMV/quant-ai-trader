"""Rolling performance metrics from trade logs (M23).

The :class:`PerformanceMonitor` reads a sequence of trade records — typically
:class:`~services.execution_service.trade_log.PaperTrade` objects, but anything
duck-typed with the same fields works — and computes the metrics the milestone
tracks: rolling win rate, profit factor, drawdown, losing streak, slippage vs
expected, spread at entry, Kronos prediction accuracy, signal hit rate, plus
per-strategy / per-symbol / per-session PnL breakdowns.

Everything is pure and deterministic: the same trades always yield the same
metrics. Only *closed* trades (those with a realised ``pnl``) count toward
performance; open and rejected records are ignored for PnL. A ``window`` keeps
the metrics *rolling* — only the most recent N closed trades are considered.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.data_service.sessions import session_label
from services.risk_service.symbol_specs import get_symbol_spec

DEFAULT_INITIAL_EQUITY = 10_000.0


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #
class PerformanceMetrics(BaseModel):
    """Computed performance over a (possibly windowed) set of closed trades."""

    model_config = ConfigDict(extra="forbid")

    n_trades: int = 0          # records considered (input to this evaluation)
    n_closed: int = 0          # of those, with a realised pnl
    wins: int = 0
    losses: int = 0
    breakeven: int = 0

    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0    # positive magnitude of losing pnl

    win_rate: float = 0.0
    profit_factor: Optional[float] = None  # None ⇒ no losing trades
    signal_hit_rate: Optional[float] = None  # wins / (wins+losses); None if no decisive

    max_drawdown: float = 0.0        # absolute, account currency
    max_drawdown_pct: float = 0.0    # fraction of running peak equity
    current_losing_streak: int = 0
    max_losing_streak: int = 0

    avg_slippage_points: Optional[float] = None
    slippage_samples: int = 0
    avg_spread_at_entry: Optional[float] = None
    spread_samples: int = 0

    kronos_accuracy: Optional[float] = None
    kronos_samples: int = 0


# --------------------------------------------------------------------------- #
# Duck-typed access to a trade record
# --------------------------------------------------------------------------- #
def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


@dataclass
class _TradeView:
    """Normalised view of a single trade record (built once per trade)."""

    pnl: Optional[float]
    strategy: str
    symbol: str
    timestamp: Optional[datetime]
    side: Optional[str]
    entry: Optional[float]
    expected_price: Optional[float]
    kronos_direction: Optional[str]
    spread_at_entry: Optional[float]

    @property
    def is_closed(self) -> bool:
        return self.pnl is not None

    @property
    def outcome(self) -> Optional[str]:
        if self.pnl is None:
            return None
        if self.pnl > 0:
            return "win"
        if self.pnl < 0:
            return "loss"
        return "breakeven"


def _view(trade: Any) -> _TradeView:
    pnl = _attr(trade, "pnl")
    pnl = float(pnl) if pnl is not None else None

    strategy = _attr(trade, "strategy_name") or ""
    if not strategy:
        # Fall back to the intent's strategy id if present.
        decision = _attr(trade, "risk_decision")
        intent = _attr(decision, "intent")
        strategy = _attr(intent, "strategy_id") or "unknown"

    side = _enum_value(_attr(trade, "side"))
    side = str(side).lower() if side is not None else None

    # Expected (intended) entry price for slippage — from the gated intent.
    decision = _attr(trade, "risk_decision")
    intent = _attr(decision, "intent")
    expected_price = _attr(intent, "price")
    expected_price = float(expected_price) if expected_price is not None else None

    # Kronos predicted direction (if a prediction was attached).
    kp = _attr(trade, "kronos_prediction")
    kdir = _enum_value(_attr(kp, "direction")) if kp is not None else None
    kdir = str(kdir).lower() if kdir is not None else None

    # Spread at entry — explicit field, else a feature snapshot value.
    spread = _attr(trade, "spread_at_entry")
    if spread is None:
        fs = _attr(trade, "features_snapshot")
        feats = _attr(fs, "features") or {}
        if isinstance(feats, dict):
            spread = feats.get("spread", feats.get("spread_points"))
    spread = float(spread) if spread is not None else None

    entry = _attr(trade, "entry")
    entry = float(entry) if entry is not None else None

    ts = _attr(trade, "timestamp")
    return _TradeView(
        pnl=pnl, strategy=strategy, symbol=_attr(trade, "symbol") or "unknown",
        timestamp=ts, side=side, entry=entry, expected_price=expected_price,
        kronos_direction=kdir, spread_at_entry=spread,
    )


def _point_size(symbol: str) -> float:
    spec = get_symbol_spec(symbol)
    return spec.point_size if spec else 0.0001


# --------------------------------------------------------------------------- #
# Monitor
# --------------------------------------------------------------------------- #
class PerformanceMonitor:
    """Compute rolling performance metrics and PnL breakdowns from trade logs."""

    def __init__(
        self,
        *,
        window: Optional[int] = None,
        initial_equity: float = DEFAULT_INITIAL_EQUITY,
    ) -> None:
        if window is not None and window <= 0:
            raise ValueError("window must be positive or None")
        self.window = window
        self.initial_equity = float(initial_equity)

    # -- public API -------------------------------------------------------- #
    def evaluate(self, trades: Any) -> PerformanceMetrics:
        """Compute metrics over the closed trades (most recent ``window`` of them)."""
        views = [_view(t) for t in trades]
        closed = [v for v in views if v.is_closed]
        closed.sort(key=lambda v: (v.timestamp is None, v.timestamp))
        if self.window is not None:
            closed = closed[-self.window:]
        return self._metrics(closed)

    def metrics_by_strategy(self, trades: Any) -> dict[str, PerformanceMetrics]:
        """Per-strategy metrics (each strategy windowed independently)."""
        return self._metrics_by(trades, lambda v: v.strategy)

    def pnl_by_strategy(self, trades: Any) -> dict[str, float]:
        return self._pnl_by(trades, lambda v: v.strategy)

    def pnl_by_symbol(self, trades: Any) -> dict[str, float]:
        return self._pnl_by(trades, lambda v: v.symbol)

    def pnl_by_session(self, trades: Any) -> dict[str, float]:
        return self._pnl_by(
            trades,
            lambda v: session_label(v.timestamp) if v.timestamp is not None else "unknown",
        )

    # -- internals --------------------------------------------------------- #
    def _metrics_by(
        self, trades: Any, key: Callable[[_TradeView], str]
    ) -> dict[str, PerformanceMetrics]:
        groups: dict[str, list[_TradeView]] = {}
        for v in (_view(t) for t in trades):
            if v.is_closed:
                groups.setdefault(key(v), []).append(v)
        out: dict[str, PerformanceMetrics] = {}
        for name, views in groups.items():
            views.sort(key=lambda v: (v.timestamp is None, v.timestamp))
            if self.window is not None:
                views = views[-self.window:]
            out[name] = self._metrics(views)
        return out

    def _pnl_by(self, trades: Any, key: Callable[[_TradeView], str]) -> dict[str, float]:
        totals: dict[str, float] = {}
        for v in (_view(t) for t in trades):
            if v.is_closed:
                totals[key(v)] = round(totals.get(key(v), 0.0) + v.pnl, 6)
        return totals

    def _metrics(self, closed: list[_TradeView]) -> PerformanceMetrics:
        m = PerformanceMetrics(n_trades=len(closed), n_closed=len(closed))
        if not closed:
            return m

        gross_profit = gross_loss = 0.0
        cur_streak = max_streak = 0
        equity = self.initial_equity
        peak = equity
        max_dd = max_dd_pct = 0.0

        for v in closed:
            pnl = v.pnl or 0.0
            m.net_pnl += pnl
            if pnl > 0:
                m.wins += 1
                gross_profit += pnl
                cur_streak = 0
            elif pnl < 0:
                m.losses += 1
                gross_loss += -pnl
                cur_streak += 1
                max_streak = max(max_streak, cur_streak)
            else:
                m.breakeven += 1
                cur_streak = 0

            # Equity curve & drawdown.
            equity += pnl
            peak = max(peak, equity)
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
            if peak > 0 and dd / peak > max_dd_pct:
                max_dd_pct = dd / peak

        m.net_pnl = round(m.net_pnl, 6)
        m.gross_profit = round(gross_profit, 6)
        m.gross_loss = round(gross_loss, 6)
        m.win_rate = round(m.wins / m.n_closed, 6) if m.n_closed else 0.0
        m.profit_factor = round(gross_profit / gross_loss, 6) if gross_loss > 0 else None
        decisive = m.wins + m.losses
        m.signal_hit_rate = round(m.wins / decisive, 6) if decisive else None
        m.max_drawdown = round(max_dd, 6)
        m.max_drawdown_pct = round(max_dd_pct, 6)
        m.current_losing_streak = cur_streak
        m.max_losing_streak = max_streak

        self._fill_execution_quality(closed, m)
        self._fill_kronos_accuracy(closed, m)
        return m

    @staticmethod
    def _fill_execution_quality(closed: list[_TradeView], m: PerformanceMetrics) -> None:
        slippages: list[float] = []
        spreads: list[float] = []
        for v in closed:
            if v.expected_price is not None and v.entry is not None and v.side in ("buy", "sell"):
                point = _point_size(v.symbol)
                raw = v.entry - v.expected_price
                # Positive = adverse (paid worse than expected).
                signed = raw if v.side == "buy" else -raw
                slippages.append(signed / point if point else 0.0)
            if v.spread_at_entry is not None:
                spreads.append(v.spread_at_entry)
        if slippages:
            m.avg_slippage_points = round(sum(slippages) / len(slippages), 6)
            m.slippage_samples = len(slippages)
        if spreads:
            m.avg_spread_at_entry = round(sum(spreads) / len(spreads), 6)
            m.spread_samples = len(spreads)

    @staticmethod
    def _fill_kronos_accuracy(closed: list[_TradeView], m: PerformanceMetrics) -> None:
        correct = samples = 0
        for v in closed:
            if v.kronos_direction not in ("up", "down"):
                continue
            if v.pnl is None or v.pnl == 0 or v.side not in ("buy", "sell"):
                continue
            realised_up = (v.side == "buy" and v.pnl > 0) or (v.side == "sell" and v.pnl < 0)
            predicted_up = v.kronos_direction == "up"
            samples += 1
            if predicted_up == realised_up:
                correct += 1
        if samples:
            m.kronos_accuracy = round(correct / samples, 6)
            m.kronos_samples = samples


__all__ = [
    "PerformanceMonitor",
    "PerformanceMetrics",
    "DEFAULT_INITIAL_EQUITY",
]
