"""A small, deterministic, single-symbol backtester.

Built before integrating QuantDinger so strategies can be evaluated locally with
*realistic friction*. It is intentionally simple and honest about its
assumptions:

* **Single symbol, one position at a time.** Signals are ``BUY`` / ``SELL`` /
  ``NONE``; a new position is entered at the **next bar's open** (never the
  close the signal was computed from — no look-ahead).
* **Stop loss is mandatory.** A ``BUY``/``SELL`` signal without a valid stop is
  *rejected* (counted, never traded). Take profit is optional.
* **Exits**, checked intrabar each bar in priority order: stop loss → take
  profit → max holding period → end-of-day / session close. Any position still
  open at the last bar is closed on it.
* **Costs** (spread, slippage, commission) are applied to every fill via
  :class:`~services.backtest_service.costs.CostModel`.

No MT5, no network, no AI — given the same candles, config and strategy the run
is byte-for-byte reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import pandas as pd

from services.backtest_service.costs import CostModel
from services.backtest_service.metrics import BacktestMetrics, compute_metrics
from services.data_service.sessions import label_sessions

REQUIRED_COLUMNS = ("open", "high", "low", "close")


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class ClosePolicy(str, Enum):
    """When to force-flat an open position regardless of SL/TP."""

    NONE = "none"
    END_OF_DAY = "end_of_day"   # close on the last bar of each calendar day
    SESSION = "session"         # close when the FX session label changes


@dataclass(frozen=True)
class BacktestSignal:
    """A strategy's view at one bar. ``NONE`` (the default) means do nothing."""

    direction: Direction = Direction.NONE
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    size: Optional[float] = None  # optional per-trade size override
    reason: str = ""


# A strategy maps the candle history up to (and including) the current bar to a
# signal. It must be causal — it only ever sees the slice it is given.
Strategy = Callable[[pd.DataFrame], Optional[BacktestSignal]]


@dataclass
class Trade:
    """A single completed round-trip trade."""

    symbol: str
    direction: Direction
    size: float
    entry_index: int
    exit_index: int
    entry_time: Optional[datetime]
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: Optional[float]
    pnl: float
    r_multiple: float
    bars_held: int
    exit_reason: str


@dataclass
class BacktestConfig:
    """Knobs for a run. ``size`` is in units of the base instrument."""

    initial_equity: float = 10_000.0
    size: float = 1.0
    cost_model: CostModel = field(default_factory=lambda: CostModel(
        slippage_points=1.0, commission=0.0, spread_fraction=1.0))
    max_holding_bars: Optional[int] = None
    close_policy: ClosePolicy = ClosePolicy.NONE


@dataclass
class BacktestReport:
    symbol: str
    trades: list[Trade]
    equity_curve: list[float]
    metrics: BacktestMetrics
    rejected_signals: int = 0
    rejected_no_stop: int = 0
    n_bars: int = 0


class SimpleBacktester:
    """Run a single-symbol strategy over a candle frame with friction."""

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self.config = config or BacktestConfig()

    # -- public API -------------------------------------------------------- #
    def run(self, candles: pd.DataFrame, strategy: Strategy) -> BacktestReport:
        self._validate_frame(candles)
        c = self.config
        n = len(candles)

        opens = candles["open"].astype(float).to_numpy()
        highs = candles["high"].astype(float).to_numpy()
        lows = candles["low"].astype(float).to_numpy()
        closes = candles["close"].astype(float).to_numpy()
        spreads = (
            candles["spread"].astype(float).to_numpy()
            if "spread" in candles.columns else [0.0] * n
        )
        symbol = str(candles["symbol"].iloc[0]) if "symbol" in candles.columns else ""
        times = (
            pd.to_datetime(candles["timestamp"]).reset_index(drop=True)
            if "timestamp" in candles.columns else None
        )
        boundary = self._close_boundaries(times, n)

        trades: list[Trade] = []
        equity_curve: list[float] = []
        realized = 0.0
        position: Optional[dict] = None
        pending: Optional[BacktestSignal] = None
        rejected = rejected_no_stop = 0

        for i in range(n):
            # 1) Open a position scheduled at the previous bar.
            if position is None and pending is not None:
                position = self._open(pending, i, opens[i], spreads[i], symbol, times)
                pending = None

            # 2) Manage the open position on this bar.
            if position is not None:
                trade = self._try_exit(
                    position, i, highs[i], lows[i], closes[i], spreads[i],
                    symbol, times, last_bar=(i == n - 1), boundary=boundary[i],
                )
                if trade is not None:
                    realized += trade.pnl
                    trades.append(trade)
                    position = None

            # 3) Ask the strategy for the next move when flat.
            if position is None and pending is None and i < n - 1:
                sig = strategy(candles.iloc[: i + 1])
                if sig is not None and sig.direction in (Direction.BUY, Direction.SELL):
                    ok, no_stop = self._signal_is_tradable(sig, closes[i])
                    if ok:
                        pending = sig
                    else:
                        rejected += 1
                        rejected_no_stop += int(no_stop)

            # 4) Mark equity to market.
            equity_curve.append(
                self.config.initial_equity + realized
                + self._unrealized(position, closes[i])
            )

        return BacktestReport(
            symbol=symbol,
            trades=trades,
            equity_curve=equity_curve,
            metrics=compute_metrics(trades, equity_curve),
            rejected_signals=rejected,
            rejected_no_stop=rejected_no_stop,
            n_bars=n,
        )

    # -- helpers ----------------------------------------------------------- #
    def _validate_frame(self, candles: pd.DataFrame) -> None:
        if not isinstance(candles, pd.DataFrame):
            raise TypeError("candles must be a pandas DataFrame")
        missing = [c for c in REQUIRED_COLUMNS if c not in candles.columns]
        if missing:
            raise ValueError(f"candles missing required columns: {missing}")
        if len(candles) == 0:
            raise ValueError("candles is empty")
        if self.config.close_policy is not ClosePolicy.NONE and "timestamp" not in candles.columns:
            raise ValueError(f"{self.config.close_policy.value} close needs a timestamp column")

    def _close_boundaries(self, times: Optional[pd.Series], n: int) -> list[bool]:
        """Per-bar flag: is this the last bar of its day/session?"""
        policy = self.config.close_policy
        if policy is ClosePolicy.NONE or times is None:
            return [False] * n
        if policy is ClosePolicy.END_OF_DAY:
            keys = times.dt.normalize().to_numpy()
        else:  # SESSION
            keys = label_sessions(times).to_numpy()
        return [(i == n - 1) or (keys[i] != keys[i + 1]) for i in range(n)]

    def _signal_is_tradable(self, sig: BacktestSignal, ref: float) -> tuple[bool, bool]:
        """Return (tradable, no_stop). Rejects missing/ill-placed stops."""
        if sig.stop_loss is None or sig.stop_loss <= 0:
            return False, True
        if sig.direction is Direction.BUY:
            if not (sig.stop_loss < ref):
                return False, False
            if sig.take_profit is not None and not (sig.take_profit > ref):
                return False, False
        else:  # SELL
            if not (sig.stop_loss > ref):
                return False, False
            if sig.take_profit is not None and not (sig.take_profit < ref):
                return False, False
        return True, False

    def _open(self, sig, index, raw_open, spread, symbol, times) -> dict:
        side = sig.direction.value
        entry = self.config.cost_model.fill_price(
            raw_open, symbol=symbol, spread_points=spread, side=side, opening=True
        )
        size = sig.size if sig.size is not None else self.config.size
        return {
            "direction": sig.direction,
            "entry_index": index,
            "entry_time": (times.iloc[index] if times is not None else None),
            "entry_price": entry,
            "stop_loss": float(sig.stop_loss),
            "take_profit": (float(sig.take_profit) if sig.take_profit is not None else None),
            "size": float(size),
        }

    def _try_exit(
        self, pos, i, high, low, close, spread, symbol, times, *, last_bar, boundary
    ) -> Optional[Trade]:
        """Resolve an exit for this bar (priority: SL → TP → max-hold → close)."""
        direction = pos["direction"]
        stop, take = pos["stop_loss"], pos["take_profit"]
        held = i - pos["entry_index"]

        raw_exit: Optional[float] = None
        reason = ""
        if direction is Direction.BUY:
            if low <= stop:
                raw_exit, reason = stop, "stop_loss"
            elif take is not None and high >= take:
                raw_exit, reason = take, "take_profit"
        else:  # SELL
            if high >= stop:
                raw_exit, reason = stop, "stop_loss"
            elif take is not None and low <= take:
                raw_exit, reason = take, "take_profit"

        if raw_exit is None and self.config.max_holding_bars is not None \
                and held >= self.config.max_holding_bars:
            raw_exit, reason = close, "max_holding"
        if raw_exit is None and boundary and self.config.close_policy is not ClosePolicy.NONE:
            raw_exit, reason = close, self.config.close_policy.value
        if raw_exit is None and last_bar:
            raw_exit, reason = close, "end_of_data"
        if raw_exit is None:
            return None

        side = direction.value
        exit_price = self.config.cost_model.fill_price(
            raw_exit, symbol=symbol, spread_points=spread, side=side, opening=False
        )
        size = pos["size"]
        if direction is Direction.BUY:
            gross = (exit_price - pos["entry_price"]) * size
        else:
            gross = (pos["entry_price"] - exit_price) * size
        pnl = gross - self.config.cost_model.commission
        risk = abs(pos["entry_price"] - stop) * size
        r_multiple = (pnl / risk) if risk > 0 else 0.0

        return Trade(
            symbol=symbol,
            direction=direction,
            size=size,
            entry_index=pos["entry_index"],
            exit_index=i,
            entry_time=pos["entry_time"],
            exit_time=(times.iloc[i] if times is not None else None),
            entry_price=round(pos["entry_price"], 6),
            exit_price=round(exit_price, 6),
            stop_loss=stop,
            take_profit=take,
            pnl=round(pnl, 6),
            r_multiple=round(r_multiple, 6),
            bars_held=held,
            exit_reason=reason,
        )

    def _unrealized(self, pos: Optional[dict], close: float) -> float:
        if pos is None:
            return 0.0
        if pos["direction"] is Direction.BUY:
            return (close - pos["entry_price"]) * pos["size"]
        return (pos["entry_price"] - close) * pos["size"]


__all__ = [
    "Direction",
    "ClosePolicy",
    "BacktestSignal",
    "Trade",
    "BacktestConfig",
    "BacktestReport",
    "SimpleBacktester",
    "Strategy",
]
