"""AI agent tool interface (M19) — paper-only, no live execution.

These are the callable tools the AI agent is given. Each is a thin, safe wrapper
over an existing service; together they let the agent **research, propose,
backtest, validate and create paper trades** — and nothing else.

Two safety properties are structural, not just documented:

* **There is no live-execution tool.** No function here sends a real order, and
  the names in :data:`~apps.agent.agent_config.FORBIDDEN_TOOLS` are deliberately
  absent. :func:`available_tools` is asserted against that set on import.
* **Paper trades go through the risk gate every time.** :func:`create_paper_trade`
  re-runs the deterministic :class:`RiskManager` itself and only proceeds on an
  approval — the agent cannot hand in a pre-approved decision (no self-bypass),
  cannot trade an unapproved intent, and cannot change the risk config (it is
  loaded read-only).

Tools return plain JSON-serialisable dicts so they can back a function-calling
LLM interface directly, while remaining ordinary Python callables for tests.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Union

import pandas as pd

from apps.agent.agent_config import (
    DEFAULT_AGENT_CONFIG,
    AgentConfig,
    AgentPermissionError,
    assert_no_live_tools,
    assert_paper_only,
)
from services.backtest_service.simple_backtester import (
    BacktestConfig,
    BacktestSignal,
    Direction,
    SimpleBacktester,
    Strategy,
)
from services.backtest_service.strategy_validator import (
    StrategyValidator,
    build_validation_input,
)
from services.config_loader import (
    PROJECT_ROOT,
    load_risk_config,
    load_symbols_config,
)
from services.data_service.features import compute_features
from services.data_service.sample_data import generate_candles
from services.execution_service.paper_execution import PaperExecutionService
from services.kronos_service.real_kronos import load_kronos
from services.models import OrderIntent, OrderType, Side, TradingMode
from services.risk_service.risk_manager import RiskContext, RiskManager
from services.strategy_service.adapters import register_technical_indicator_adapters
from services.strategy_service.base import SignalSide
from services.strategy_service.inventory_scanner import load_inventory
from services.strategy_service.registry import StrategyRegistry

DEFAULT_INVENTORY_PATH = (
    PROJECT_ROOT / "strategies" / "inventory" / "quant_trading_inventory.json"
)

# A strategy reference is either a backtester Strategy callable or an adapter name.
StrategyRef = Union[str, Strategy]

# Candle sources the research tools accept. ``sample`` is deterministic/offline
# (the safe default that keeps tests reproducible); ``mt5`` pulls *real* candles
# from a running MetaTrader 5 terminal so the AI can reason over live market data.
CandleSource = str  # "sample" | "mt5" | "local"


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _mt5_candle_frame(symbol: str, timeframe: str, n: int) -> pd.DataFrame:
    """Pull the most recent ``n`` real candles from a running MT5 terminal.

    Read-only: this only fetches data (``get_rates`` never sends an order). The
    lookback window is padded generously (markets close overnight/weekends) and
    the tail is trimmed to exactly ``n`` bars. Requires the MetaTrader5 package
    and a logged-in terminal — otherwise ``mt5_data`` raises a clear error.
    """
    from datetime import datetime, timedelta, timezone

    from services.data_service.mt5_data import connect_to_mt5, get_rates
    from services.data_service.sample_data import TIMEFRAME_MINUTES

    minutes = TIMEFRAME_MINUTES.get(timeframe.upper(), 60)
    # ~3x padding for non-trading hours + a small floor so short requests still
    # span enough calendar time to return ``n`` bars.
    span_minutes = max(int(n * minutes * 3), minutes * 64)
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(minutes=span_minutes) - timedelta(days=3)

    connect_to_mt5()
    df = get_rates(symbol, timeframe, start, end)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df.tail(n).reset_index(drop=True)


def _candle_frame(
    symbol: str,
    timeframe: str,
    n: int,
    *,
    seed: int = 42,
    source: CandleSource = "sample",
) -> pd.DataFrame:
    """Candles for research/backtest.

    ``source="sample"`` (default) returns deterministic offline candles — the
    safe, reproducible path used by tests and demos. ``source="mt5"`` returns
    real, recent candles from a running MetaTrader 5 terminal (data only).
    ``source="local"`` returns candles resampled from the local pricer tick dump
    (see ``services.data_service.local_data``) — deep history for validation.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if source == "mt5":
        return _mt5_candle_frame(symbol, timeframe, n)
    if source == "local":
        from services.data_service.local_data import load_local_candles
        return load_local_candles(symbol, timeframe, n)
    if source != "sample":
        raise ValueError(f"unknown candle source {source!r}; use 'sample', 'mt5', or 'local'")
    return generate_candles(symbol, timeframe, n=n, seed=seed)


def _adapter_registry() -> StrategyRegistry:
    """A fresh registry populated with the runnable technical-indicator adapters."""
    registry = StrategyRegistry()
    register_technical_indicator_adapters(registry)
    return registry


_BACKTEST_DIRECTION = {SignalSide.BUY: Direction.BUY, SignalSide.SELL: Direction.SELL}


def _resolve_strategy(
    strategy: StrategyRef,
    *,
    stop_fraction: float = 0.01,
    reward_ratio: float = 2.0,
) -> Strategy:
    """Turn a strategy reference into a backtester :data:`Strategy` callable.

    A callable is used as-is. An adapter *name* is looked up in a registry and
    bridged: the adapter's :class:`AdapterSignal` becomes a
    :class:`BacktestSignal`, synthesising a stop/target from ``stop_fraction`` /
    ``reward_ratio`` when the adapter does not suggest its own (the backtester
    rejects stop-less signals).
    """
    if callable(strategy):
        return strategy
    if not isinstance(strategy, str):
        raise TypeError("strategy must be an adapter name or a callable")

    adapter = _adapter_registry().get(strategy)

    def _bridge(window: pd.DataFrame) -> Optional[BacktestSignal]:
        sig = adapter.generate_signal(window)
        if not sig.is_actionable:
            return BacktestSignal(direction=Direction.NONE)
        close = float(window["close"].iloc[-1])
        direction = _BACKTEST_DIRECTION[sig.side]
        if direction is Direction.BUY:
            stop = sig.suggested_stop_loss or close * (1.0 - stop_fraction)
            dist = close - stop
            take = sig.suggested_take_profit or close + dist * reward_ratio
        else:  # SELL
            stop = sig.suggested_stop_loss or close * (1.0 + stop_fraction)
            dist = stop - close
            take = sig.suggested_take_profit or close - dist * reward_ratio
        return BacktestSignal(
            direction=direction, stop_loss=float(stop), take_profit=float(take),
            reason=sig.reason or strategy,
        )

    return _bridge


def _build_risk_context(
    *,
    config: AgentConfig,
    account_balance: Optional[float] = None,
    reference_price: Optional[float] = None,
    spread_points: float = 0.0,
    volatility: Optional[float] = None,
    realized_daily_loss: float = 0.0,
    open_trades: tuple[tuple[str, str], ...] = (),
    trades_today: int = 0,
    strategy_approved: bool = True,
    strategy_applicability: str = "direct",
    allowlist: Optional[tuple[str, ...]] = None,
) -> RiskContext:
    """Build a paper-mode :class:`RiskContext` from agent-supplied runtime state.

    Mode is forced to ``PAPER`` and live trading to disabled — the agent cannot
    select another mode here. The allowlist defaults to the canonical
    ``config/symbols.yaml`` set.
    """
    if allowlist is None:
        allowlist = tuple(load_symbols_config().allowlist)
    balance = account_balance if account_balance is not None else config.default_account_balance
    return RiskContext(
        mode=TradingMode.PAPER,
        allow_live=False,
        allowlist=tuple(allowlist),
        account_balance=float(balance),
        reference_price=reference_price,
        spread_points=spread_points,
        volatility=volatility,
        realized_daily_loss=realized_daily_loss,
        open_trades=open_trades,
        trades_today=trades_today,
        strategy_approved=strategy_approved,
        strategy_applicability=strategy_applicability,
    )


def _coerce_intent(intent: Union[OrderIntent, dict[str, Any]]) -> OrderIntent:
    if isinstance(intent, OrderIntent):
        return intent
    if isinstance(intent, dict):
        return OrderIntent.model_validate(intent)
    raise TypeError("intent must be an OrderIntent or a dict")


# --------------------------------------------------------------------------- #
# Research tools
# --------------------------------------------------------------------------- #
def get_candles(
    symbol: str, timeframe: str = "H1", n: int = 200, *, seed: int = 42,
    source: CandleSource = "sample",
) -> dict[str, Any]:
    """Return recent OHLCV candles for ``symbol``/``timeframe``.

    ``source="sample"`` is deterministic/offline; ``source="mt5"`` pulls real,
    recent candles from a running MetaTrader 5 terminal.
    """
    df = _candle_frame(symbol, timeframe, n, seed=seed, source=source)
    candles = [
        {
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        }
        for ts, o, h, l, c in zip(
            df["timestamp"], df["open"], df["high"], df["low"], df["close"]
        )
    ]
    return {"symbol": symbol, "timeframe": timeframe, "count": len(candles), "candles": candles}


def get_market_features(
    symbol: str, timeframe: str = "H1", n: int = 200, *, seed: int = 42,
    source: CandleSource = "sample",
) -> dict[str, Any]:
    """Compute causal technical features and return the latest (non-NaN) row.

    ``source="mt5"`` computes the features on real, recent terminal candles.
    """
    df = _candle_frame(symbol, timeframe, n, seed=seed, source=source)
    feats = compute_features(df)
    last = feats.iloc[-1]
    features: dict[str, Any] = {}
    for key, val in last.items():
        if key in ("timestamp", "session"):
            continue
        fval = float(val)
        if fval == fval:  # drop NaN warm-up values
            features[key] = fval
    ts = last["timestamp"]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "session": str(last["session"]),
        "features": features,
    }


def get_kronos_prediction(
    symbol: str,
    timeframe: str = "H1",
    n: int = 200,
    *,
    lookback: int = 64,
    pred_len: int = 1,
    mode: str = "mock",
    seed: int = 42,
    source: CandleSource = "sample",
) -> dict[str, Any]:
    """Optional Kronos forecast (advisory). Defaults to the deterministic mock."""
    df = _candle_frame(symbol, timeframe, n, seed=seed, source=source)
    predictor = load_kronos(mode=mode)
    pred = predictor.predict(
        df, symbol=symbol, timeframe=timeframe, lookback=lookback, pred_len=pred_len
    )
    return pred.model_dump(mode="json")


def list_strategy_inventory(
    *, path: Optional[str] = None
) -> dict[str, Any]:
    """List the classified strategy inventory (static, read-only)."""
    inv_path = path or DEFAULT_INVENTORY_PATH
    items = load_inventory(inv_path)
    return {
        "count": len(items),
        "strategies": [
            {
                "name": it.name,
                "category": it.category,
                "mt5_applicability": it.mt5_applicability,
                "porting_status": it.porting_status,
                "supported_asset_classes": it.supported_asset_classes,
            }
            for it in items
        ],
    }


def list_strategy_adapters() -> dict[str, Any]:
    """List the runnable strategy adapters (the ones ``run_backtest`` accepts)."""
    registry = _adapter_registry()
    adapters = []
    for adapter in registry.adapters():
        meta = adapter.get_metadata()
        adapters.append({
            "name": meta.name,
            "version": meta.version,
            "category": meta.category,
            "description": meta.description,
            "supported_symbols": meta.supported_symbols,
            "supported_timeframes": meta.supported_timeframes,
        })
    return {"count": len(adapters), "adapters": adapters}


# --------------------------------------------------------------------------- #
# Backtest / validation tools
# --------------------------------------------------------------------------- #
def run_backtest(
    strategy: StrategyRef,
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    n: int = 400,
    *,
    seed: int = 42,
    stop_fraction: float = 0.01,
    reward_ratio: float = 2.0,
    source: CandleSource = "sample",
) -> dict[str, Any]:
    """Backtest ``strategy`` (adapter name or callable) with realistic friction.

    ``source="mt5"`` backtests on real, recent terminal candles instead of the
    deterministic sample series.
    """
    df = _candle_frame(symbol, timeframe, n, seed=seed, source=source)
    fn = _resolve_strategy(strategy, stop_fraction=stop_fraction, reward_ratio=reward_ratio)
    report = SimpleBacktester(BacktestConfig()).run(df, fn)
    return {
        "strategy": strategy if isinstance(strategy, str) else "callable",
        "symbol": symbol,
        "timeframe": timeframe,
        "n_bars": report.n_bars,
        "num_trades": len(report.trades),
        "rejected_signals": report.rejected_signals,
        "metrics": report.metrics.to_dict(),
        "final_equity": report.equity_curve[-1] if report.equity_curve else None,
    }


def score_backtest(metrics: dict[str, Any]) -> dict[str, Any]:
    """Reduce backtest metrics to a single 0–1 score and a letter grade.

    Deterministic and weighting-explicit: rewards profit factor, win rate and
    expectancy; penalises drawdown. Accepts either a ``run_backtest`` result
    (with a ``metrics`` key) or a bare metrics dict.
    """
    m = metrics.get("metrics", metrics)

    def clamp(x: float) -> float:
        return max(0.0, min(1.0, x))

    pf = m.get("profit_factor")
    pf_score = 1.0 if pf is None else clamp((pf - 1.0) / 1.0)        # pf 1→0, 2→1
    win_score = clamp(float(m.get("win_rate", 0.0)))
    dd_score = clamp(1.0 - float(m.get("max_drawdown_pct", 0.0)) / 0.3)  # 30% dd → 0
    exp_score = clamp(0.5 + float(m.get("expectancy", 0.0)))         # >0 expectancy helps

    components = {
        "profit_factor": round(pf_score, 4),
        "win_rate": round(win_score, 4),
        "drawdown": round(dd_score, 4),
        "expectancy": round(exp_score, 4),
    }
    weights = {"profit_factor": 0.35, "win_rate": 0.2, "drawdown": 0.3, "expectancy": 0.15}
    score = sum(components[k] * weights[k] for k in weights)
    has_trades = int(m.get("total_trades", 0)) > 0
    if not has_trades:
        score = 0.0
    grade = (
        "A" if score >= 0.8 else "B" if score >= 0.6
        else "C" if score >= 0.4 else "D" if score >= 0.2 else "F"
    )
    return {"score": round(score, 4), "grade": grade, "components": components,
            "has_trades": has_trades}


def validate_strategy(
    strategy: StrategyRef,
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    n: int = 600,
    *,
    out_of_sample_fraction: float = 0.3,
    seed: int = 42,
    stop_fraction: float = 0.01,
    reward_ratio: float = 2.0,
    source: CandleSource = "sample",
) -> dict[str, Any]:
    """Run the approval gates against a strategy's in/out-of-sample backtests.

    ``source="mt5"`` validates on real, recent terminal candles.
    """
    df = _candle_frame(symbol, timeframe, n, seed=seed, source=source)
    split = int(len(df) * (1.0 - out_of_sample_fraction))
    candles_in = df.iloc[:split].reset_index(drop=True)
    candles_out = df.iloc[split:].reset_index(drop=True)
    fn = _resolve_strategy(strategy, stop_fraction=stop_fraction, reward_ratio=reward_ratio)

    data = build_validation_input(candles_in, fn, candles_out=candles_out)
    strategy_id = strategy if isinstance(strategy, str) else "callable"
    report = StrategyValidator().validate(data, strategy_id=strategy_id)
    return report.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Proposal / risk / paper-execution tools
# --------------------------------------------------------------------------- #
def propose_order_intent(
    symbol: str,
    side: str,
    volume: float,
    stop_loss: float,
    take_profit: float,
    *,
    order_type: str = "market",
    price: Optional[float] = None,
    strategy_id: Optional[str] = None,
    comment: str = "",
    config: AgentConfig = DEFAULT_AGENT_CONFIG,
) -> dict[str, Any]:
    """Build a proposed :class:`OrderIntent` (a proposal — never an order).

    The model itself refuses to exist without a stop loss and take profit, so a
    proposal is already the first safety checkpoint.
    """
    if not config.permissions.can_propose:
        raise AgentPermissionError("agent is not permitted to propose order intents")
    intent = OrderIntent(
        symbol=symbol,
        side=Side(side.lower()),
        order_type=OrderType(order_type.lower()),
        volume=volume,
        stop_loss=stop_loss,
        take_profit=take_profit,
        price=price,
        strategy_id=strategy_id,
        comment=comment,
    )
    return intent.model_dump(mode="json")


def risk_check_order_intent(
    intent: Union[OrderIntent, dict[str, Any]],
    *,
    reference_price: Optional[float] = None,
    account_balance: Optional[float] = None,
    spread_points: float = 0.0,
    volatility: Optional[float] = None,
    realized_daily_loss: float = 0.0,
    open_trades: tuple[tuple[str, str], ...] = (),
    trades_today: int = 0,
    strategy_approved: bool = True,
    strategy_applicability: str = "direct",
    allowlist: Optional[tuple[str, ...]] = None,
    config: AgentConfig = DEFAULT_AGENT_CONFIG,
) -> dict[str, Any]:
    """Ask the deterministic :class:`RiskManager` to approve/deny an intent.

    Uses the canonical risk config (loaded read-only — the agent cannot change
    it) and a paper-mode context. Returns the full :class:`RiskDecision`.
    """
    assert_paper_only(config)
    order = _coerce_intent(intent)
    context = _build_risk_context(
        config=config, account_balance=account_balance,
        reference_price=reference_price, spread_points=spread_points,
        volatility=volatility, realized_daily_loss=realized_daily_loss,
        open_trades=open_trades, trades_today=trades_today,
        strategy_approved=strategy_approved,
        strategy_applicability=strategy_applicability, allowlist=allowlist,
    )
    manager = RiskManager(load_risk_config())  # read-only canonical config
    decision = manager.evaluate(order, context)
    return decision.model_dump(mode="json")


def create_paper_trade(
    intent: Union[OrderIntent, dict[str, Any]],
    *,
    timeframe: str = "H1",
    reference_price: Optional[float] = None,
    account_balance: Optional[float] = None,
    spread_points: float = 0.0,
    volatility: Optional[float] = None,
    realized_daily_loss: float = 0.0,
    open_trades: tuple[tuple[str, str], ...] = (),
    trades_today: int = 0,
    strategy_approved: bool = True,
    strategy_applicability: str = "direct",
    allowlist: Optional[tuple[str, ...]] = None,
    execution_service: Optional[PaperExecutionService] = None,
    config: AgentConfig = DEFAULT_AGENT_CONFIG,
) -> dict[str, Any]:
    """Create a **paper** trade — but only after the risk gate approves it.

    The risk check is re-run here against the canonical config; the agent cannot
    pass in a pre-approved decision (no self-approval bypass). If the intent is
    rejected, **no trade is created** and the reasons are returned. Paper only —
    nothing is ever sent to a live broker.
    """
    assert_paper_only(config)
    if not config.permissions.can_create_paper_trades:
        raise AgentPermissionError("agent is not permitted to create paper trades")

    order = _coerce_intent(intent)
    context = _build_risk_context(
        config=config, account_balance=account_balance,
        reference_price=reference_price, spread_points=spread_points,
        volatility=volatility, realized_daily_loss=realized_daily_loss,
        open_trades=open_trades, trades_today=trades_today,
        strategy_approved=strategy_approved,
        strategy_applicability=strategy_applicability, allowlist=allowlist,
    )
    # The gate: re-run risk ourselves. Self-approval is impossible.
    decision = RiskManager(load_risk_config()).evaluate(order, context)
    if not decision.approved:
        return {
            "created": False,
            "approved": False,
            "reasons": decision.reasons,
            "checks": decision.checks,
        }

    service = execution_service or PaperExecutionService(config=load_risk_config())
    trade = service.execute(
        decision, context, timeframe=timeframe, reference_price=reference_price,
        strategy_name=order.strategy_id or "",
    )
    return {
        "created": True,
        "approved": True,
        "trade": trade.model_dump(mode="json"),
        "risk_decision": decision.model_dump(mode="json"),
    }


# --------------------------------------------------------------------------- #
# Tool registry
# --------------------------------------------------------------------------- #
AGENT_TOOLS: dict[str, Callable[..., Any]] = {
    "get_candles": get_candles,
    "get_market_features": get_market_features,
    "get_kronos_prediction": get_kronos_prediction,
    "list_strategy_inventory": list_strategy_inventory,
    "list_strategy_adapters": list_strategy_adapters,
    "run_backtest": run_backtest,
    "score_backtest": score_backtest,
    "validate_strategy": validate_strategy,
    "propose_order_intent": propose_order_intent,
    "risk_check_order_intent": risk_check_order_intent,
    "create_paper_trade": create_paper_trade,
}


def available_tools() -> list[str]:
    """Names of the tools the agent can call."""
    return sorted(AGENT_TOOLS)


def get_tool(name: str) -> Callable[..., Any]:
    """Look up a tool callable by name."""
    if name not in AGENT_TOOLS:
        raise KeyError(f"unknown tool {name!r}; available: {available_tools()}")
    return AGENT_TOOLS[name]


# Structural guarantee: no live-execution / risk-bypass tool is ever exposed.
assert_no_live_tools(set(AGENT_TOOLS))


__all__ = [
    "AGENT_TOOLS",
    "available_tools",
    "get_tool",
    "get_candles",
    "get_market_features",
    "get_kronos_prediction",
    "list_strategy_inventory",
    "list_strategy_adapters",
    "run_backtest",
    "score_backtest",
    "validate_strategy",
    "propose_order_intent",
    "risk_check_order_intent",
    "create_paper_trade",
]
