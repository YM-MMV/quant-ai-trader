"""AI decider (M29) — the AI brain dressed as a strategy adapter.

:class:`AIDecider` wraps :mod:`apps.agent.llm_runner` so the LLM's per-bar
decision can be consumed by the existing orchestrator (``scripts/trade_demo.py``)
with **no changes to the execution path**: it duck-types the small slice of the
adapter surface that :class:`~scripts.trade_demo.DemoTrader` uses (``name``,
``version``, ``get_metadata``, ``generate_signal`` → :class:`AdapterSignal`).

Safety properties carried over from the rest of the system:

* The AI only ever produces an :class:`AdapterSignal` *proposal*; the
  deterministic ``RiskManager`` in ``DemoTrader.step`` still gates every order.
* An actionable (BUY/SELL) signal is emitted **only after** the chosen strategy
  passes the deterministic ``validate_strategy`` gate (unless validation is
  explicitly disabled for a demo). So "actionable ⇒ validated".
* Any error — model, network, parsing — fails safe to a NONE (abstain) signal,
  never a crash and never a trade on bad data.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import pandas as pd

from apps.agent.llm_runner import (
    DEFAULT_MAX_ITERATIONS,
    AgentDecision,
    LLMClient,
    RunResult,
    run_decision,
)
from apps.agent.tools import validate_strategy
from services.strategy_service.base import AdapterMetadata, AdapterSignal, SignalSide

CandleSource = str  # "sample" | "mt5" | "local"


class AIDecider:
    """An LLM-backed decider that looks like a strategy adapter to the loop."""

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        client: LLMClient,
        source: CandleSource = "sample",
        validate_source: Optional[CandleSource] = None,
        risk_pct: float = 1.0,
        require_validation: bool = True,
        stop_fraction: float = 0.01,
        reward_ratio: float = 2.0,
        validate_bars: int = 600,
        min_trades: Optional[int] = None,
        force_position: bool = False,
        lookback: int = 64,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        name: str = "ai_brain",
        version: str = "0.1.0",
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.client = client
        self.source = source
        # Where the deterministic gate + the AI's backtests get their candles.
        # Defaults to ``source`` but can point at deep local history (``local``)
        # while live decisions still read ``source`` (e.g. mt5) — the broker's
        # shallow intraday history can't satisfy the validator's trade-count gate.
        self.validate_source = validate_source or source
        self.risk_pct = risk_pct
        self.require_validation = require_validation
        self.stop_fraction = stop_fraction
        self.reward_ratio = reward_ratio
        # Bars the deterministic validation gate runs on. The default (600) is
        # too shallow to clear the validator's 100-trade gate for most adapters
        # (~52 in-sample trades), so a strategy can never validate and the loop
        # always holds. Pass a deeper window (~4000+) to make trades reachable.
        self.validate_bars = max(int(validate_bars), 1)
        # Override the validator's minimum-trades gate (None = its default, 100).
        self.min_trades = min_trades
        # DEMO/PAPER fallback: if nothing validates, trade the best-scoring
        # applicable adapter that currently signals (UNVALIDATED). The caller
        # (the loop) must keep this off for live trading.
        self.force_position = bool(force_position)
        self.lookback = max(int(lookback), 1)
        self.max_iterations = max_iterations
        self.name = name
        self.version = version
        self.on_event = on_event

        # Per-tick context the loop refreshes via :meth:`update_context`.
        self.account_balance: float = 10_000.0
        self.open_position: Optional[dict[str, Any]] = None

        # Last run state (the loop reads these to log + to handle 'close').
        self.last_decision: Optional[AgentDecision] = None
        self.last_run: Optional[RunResult] = None

    # -- adapter-compatible surface --------------------------------------- #
    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name=self.name,
            version=self.version,
            description="LLM decision brain (proposes; RiskManager still gates).",
            category="ai_decision",
            min_candles=self.lookback,
        )

    def update_context(
        self,
        *,
        account_balance: Optional[float] = None,
        open_position: Optional[dict[str, Any]] = None,
    ) -> None:
        """Refresh the runtime context fed into the next decision prompt."""
        if account_balance is not None:
            self.account_balance = float(account_balance)
        self.open_position = open_position

    def generate_signal(
        self,
        candles: Any,
        features: Any = None,
        kronos_prediction: Optional[Any] = None,
    ) -> AdapterSignal:
        """Ask the AI for a move and map it to an :class:`AdapterSignal`.

        Fails safe to NONE on any error or abstention; emits an actionable signal
        only when the AI proposes an entry whose strategy passes validation.
        """
        try:
            window = candles if isinstance(candles, pd.DataFrame) else pd.DataFrame(candles)
            task = self._build_task(window)
            run = run_decision(
                task,
                client=self.client,
                dispatch=self._research_dispatch(),
                max_iterations=self.max_iterations,
                on_event=self.on_event,
            )
            self.last_run = run
            self.last_decision = run.decision
            signal = self._to_signal(run.decision, window)
            # DEMO/PAPER force fallback: nothing validated and the AI isn't
            # closing -> trade the best-scoring live candidate anyway (unvalidated).
            if (
                self.force_position
                and not signal.is_actionable
                and not run.decision.is_close
            ):
                forced = self._forced_signal(window)
                if forced.is_actionable:
                    return forced
            return signal
        except Exception as exc:  # noqa: BLE001 — abstain on any failure
            self.last_decision = AgentDecision.hold(f"decider error: {type(exc).__name__}: {exc}")
            return self._none(self.last_decision.rationale)

    # -- internals --------------------------------------------------------- #
    def _research_dispatch(self):
        """Bind this run's symbol/timeframe/source/depth/min-trades onto the AI's
        data + backtest + validation tools.

        The model calls those tools with its own arguments; left to defaults it
        would research the wrong symbol, a shallow window, or the 100-trade gate
        — so its conclusions would diverge from the deterministic gate and from
        ``--min-trades`` / ``--validate-bars`` / ``--validate-source``. We inject
        the configured values (only the params each tool actually accepts), so
        the flags are authoritative no matter what the model passes.
        """
        import inspect

        from apps.agent.tools import get_tool

        live = {"symbol": self.symbol, "timeframe": self.timeframe, "source": self.source}
        research = {
            "symbol": self.symbol, "timeframe": self.timeframe,
            "source": self.validate_source, "n": self.validate_bars,
            "min_trades": self.min_trades,
        }
        overrides = {
            "get_candles": live,
            "get_market_features": live,
            "get_kronos_prediction": live,
            "run_backtest": research,
            "validate_strategy": research,
        }

        def dispatch(name: str):
            fn = get_tool(name)
            ov = overrides.get(name)
            if not ov:
                return fn
            params = inspect.signature(fn).parameters
            inject = {k: v for k, v in ov.items() if v is not None and k in params}

            def wrapped(**kwargs):
                merged = {**kwargs, **inject}
                return fn(**merged)

            return wrapped

        return dispatch

    def _to_signal(self, decision: AgentDecision, window: pd.DataFrame) -> AdapterSignal:
        if not decision.is_open:
            # 'hold' and 'close' are non-entries here; the loop handles 'close'
            # against the live position via the gateway.
            return self._none(decision.rationale or f"action={decision.action}")

        if self.require_validation and decision.strategy:
            if not self._strategy_validated(decision.strategy):
                return self._none(
                    f"strategy {decision.strategy!r} failed the validation gate; abstaining"
                )

        side = SignalSide.BUY if decision.side == "buy" else SignalSide.SELL
        close = float(window["close"].iloc[-1])
        sl, tp = self._levels(side, close, decision.stop_loss, decision.take_profit)
        reason = decision.rationale or f"AI {decision.side} via {decision.strategy or 'analysis'}"
        notes = [f"ai_confidence={decision.confidence:.2f}"]
        if decision.strategy:
            notes.append(f"basis={decision.strategy}")
        return AdapterSignal(
            side=side,
            confidence=decision.confidence,
            reason=reason,
            suggested_stop_loss=sl,
            suggested_take_profit=tp,
            risk_notes=notes,
            source_strategy=decision.strategy or self.name,
            adapter_version=self.version,
            symbol=self.symbol,
            timeframe=self.timeframe,
        )

    def _strategy_validated(self, strategy: str) -> bool:
        """Deterministic approval gate: only validated strategies may trade."""
        try:
            report = validate_strategy(
                strategy, symbol=self.symbol, timeframe=self.timeframe,
                n=self.validate_bars, source=self.validate_source,
                min_trades=self.min_trades,
            )
        except Exception:  # noqa: BLE001 — unknown/invalid strategy ⇒ not approved
            return False
        return bool(report.get("approved", False))

    def _forced_signal(self, window: pd.DataFrame) -> AdapterSignal:
        """DEMO/PAPER fallback when nothing validates: trade the best-scoring
        applicable adapter that currently signals BUY/SELL (UNVALIDATED).

        Ranks only the adapters with a *live* actionable signal on this bar by
        their backtest score, and returns the top one's direction. Returns NONE
        if no adapter currently signals — there is no position to force from
        nothing. The RiskManager still gates the resulting order downstream.
        """
        from apps.agent.tools import _adapter_registry, run_backtest, score_backtest

        registry = _adapter_registry()
        best: Optional[tuple[float, AdapterSignal, str]] = None
        for name in registry.names():
            try:
                adapter = registry.get(name)
                if not adapter.supports_timeframe(self.timeframe):
                    continue
                sig = adapter.generate_signal(window)
                if not sig.is_actionable:
                    continue
                bt = run_backtest(
                    name, symbol=self.symbol, timeframe=self.timeframe,
                    n=self.validate_bars, source=self.validate_source,
                    stop_fraction=self.stop_fraction, reward_ratio=self.reward_ratio,
                )
                score = float(score_backtest(bt).get("score", 0.0))
            except Exception:  # noqa: BLE001 — a bad adapter must not break the fallback
                continue
            if best is None or score > best[0]:
                best = (score, sig, name)

        close = float(window["close"].iloc[-1])
        if best is not None:
            score, sig, name = best
            side = SignalSide.BUY if sig.side is SignalSide.BUY else SignalSide.SELL
            sl, tp = self._levels(side, close, sig.suggested_stop_loss, sig.suggested_take_profit)
            reason = f"FORCED (UNVALIDATED): best live candidate {name!r} by score={score:.2f}"
            notes = ["forced_unvalidated", f"score={score:.2f}", f"basis={name}"]
        else:
            # No adapter signals on this bar — still take a position (the point of
            # --force-position): a simple momentum stance, close vs SMA(lookback).
            closes = window["close"].astype(float)
            if len(closes) < 2:
                return self._none("forced: not enough data to take a position")
            sma = float(closes.tail(min(len(closes), self.lookback)).mean())
            side = SignalSide.BUY if close >= sma else SignalSide.SELL
            sl, tp = self._levels(side, close, None, None)
            reason = (f"FORCED (UNVALIDATED): no adapter signal; momentum stance "
                      f"close={close:.5f} vs SMA={sma:.5f}")
            notes = ["forced_unvalidated", "momentum_fallback"]
            name = f"{self.name}_forced"

        return AdapterSignal(
            side=side,
            confidence=0.0,
            reason=reason,
            suggested_stop_loss=sl,
            suggested_take_profit=tp,
            risk_notes=notes,
            source_strategy=name,
            adapter_version=self.version,
            symbol=self.symbol,
            timeframe=self.timeframe,
        )

    def _levels(
        self, side: SignalSide, close: float, sl: Optional[float], tp: Optional[float]
    ) -> tuple[float, float]:
        """Coerce SL/TP to valid, correctly-oriented prices around ``close``.

        Uses the AI's levels when they sit on the right side of price; otherwise
        synthesises them from ``stop_fraction`` / ``reward_ratio`` (the same
        fallback the backtest bridge uses), so a proposal can never carry an
        inverted or missing stop into the order model.
        """
        sf = self.stop_fraction
        if side is SignalSide.BUY:
            if not sl or sl >= close:
                sl = close * (1.0 - sf)
            dist = close - sl
            if not tp or tp <= close:
                tp = close + dist * self.reward_ratio
        else:  # SELL
            if not sl or sl <= close:
                sl = close * (1.0 + sf)
            dist = sl - close
            if not tp or tp >= close:
                tp = close - dist * self.reward_ratio
        return round(float(sl), 5), round(float(tp), 5)

    def _none(self, reason: str) -> AdapterSignal:
        return AdapterSignal(
            side=SignalSide.NONE,
            reason=reason,
            source_strategy=self.name,
            adapter_version=self.version,
            symbol=self.symbol,
            timeframe=self.timeframe,
        )

    def _build_task(self, window: pd.DataFrame) -> str:
        """Compose the per-bar instruction for the model."""
        close = float(window["close"].iloc[-1]) if len(window) else 0.0
        tail = window.tail(min(len(window), 20))
        hi = float(tail["high"].max()) if len(tail) else close
        lo = float(tail["low"].min()) if len(tail) else close
        ts = ""
        if "timestamp" in window.columns and len(window):
            ts = str(window["timestamp"].iloc[-1])

        pos_line = "Open position: NONE."
        if self.open_position:
            p = self.open_position
            pos_line = (
                f"Open position: {p.get('side')} {p.get('volume')} lots @ "
                f"{p.get('entry_price')} (SL {p.get('stop_loss')}, TP {p.get('take_profit')}, "
                f"floating P&L {p.get('profit')})."
            )

        return (
            f"Decide the single best trading move RIGHT NOW for {self.symbol} on the "
            f"{self.timeframe} timeframe.\n\n"
            f"Current bar: time={ts or 'n/a'}, close={close:.5f}, "
            f"recent-20 high={hi:.5f}, low={lo:.5f}.\n"
            f"{pos_line}\n"
            f"Account balance: {self.account_balance:,.2f}. "
            f"Risk budget: up to {self.risk_pct:.2f}% of balance per trade.\n\n"
            f"Use source='{self.source}' for current market data (get_candles, "
            f"get_market_features, get_kronos_prediction) so you reason on the right "
            f"prices. Use source='{self.validate_source}' for run_backtest, "
            f"score_backtest and validate_strategy (deeper history). Research recent "
            f"price action and features, then backtest, score and VALIDATE one or more "
            f"candidate strategy adapters on {self.symbol}/{self.timeframe}; only trust "
            f"a validated strategy.\n"
            f"When you backtest/validate, pass n={self.validate_bars} (the gate needs "
            f"≥100 trades, so a shallow window will spuriously fail validation).\n\n"
            f"Then call submit_decision with your final move:\n"
            f"- action='open' (with side, the strategy name, and absolute stop_loss "
            f"and take_profit prices) only if the evidence clearly supports a trade;\n"
            f"- action='close' to exit the open position above;\n"
            f"- action='hold' to abstain (the correct choice when evidence is weak).\n"
            f"Prefer abstaining to a marginal trade. Stops are mandatory for entries."
        )


__all__ = ["AIDecider"]
