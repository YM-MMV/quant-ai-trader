"""Paper-first demo trading orchestrator — the loop that ties it all together.

This is the missing automation piece: each step it pulls a candle window, asks one
strategy adapter for a signal, turns an actionable signal into an ``OrderIntent``,
runs the deterministic ``RiskManager``, and routes the result to **paper execution
by default**. It is the same building blocks the rest of the system uses — nothing
here can bypass the risk gate.

Safety (by construction):

* **Paper is the default and needs no live locks.** Paper fills are simulated; no
  broker is touched.
* **Live routing is opt-in and this script never flips a switch.** ``--mode live``
  only *attempts* the real gateway; it still requires you to have set
  ``TRADING_MODE=live`` + ``ALLOW_LIVE_TRADING=true`` in ``.env`` **and** added
  ``live`` to ``allowed_modes`` in ``config/risk.yaml``. If those aren't set, the
  RiskManager/gateway refuse and nothing is sent.
* **Unvalidated strategies don't trade.** At startup the chosen adapter is run
  through the validation gate; its verdict feeds ``strategy_approved``. Use
  ``--assume-approved`` to *demonstrate* the execution leg with an unvalidated
  strategy (clearly labelled; the gate is not bypassed, only the input flipped).

Examples
--------
    # Replay 300 generated bars through the full pipeline (no terminal needed)
    python scripts/trade_demo.py --adapter macd_oscillator --iterations 300

    # Demonstrate the paper OPEN leg even on unvalidated sample data
    python scripts/trade_demo.py --adapter macd_oscillator --assume-approved

    # Replay the last 200 real demo candles (needs a running MT5 terminal)
    python scripts/trade_demo.py --adapter rsi_pattern --source mt5 --iterations 200
"""
from __future__ import annotations

import argparse
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from services.backtest_service.adapter_bridge import adapter_to_backtest_strategy
from services.backtest_service.metrics import annualised_sharpe, max_drawdown
from services.backtest_service.simple_backtester import _infer_periods_per_year
from services.backtest_service.strategy_validator import (
    StrategyValidator,
    build_validation_input,
    load_validation_config,
)
from services.config_loader import Settings, load_risk_config
from services.data_service.sample_data import generate_candles
from services.execution_service.audit_log import AuditLog
from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import TradeLogStore
from services.models import OrderIntent, OrderType, Side, TradingMode
from services.risk_service.position_sizing import compute_lot_size
from services.risk_service.risk_manager import RiskContext, RiskManager
from services.risk_service.symbol_specs import get_symbol_spec
from services.strategy_service.adapters import register_technical_indicator_adapters
from services.strategy_service.base import SignalSide
from services.strategy_service.registry import StrategyRegistry

DEFAULT_ALLOWLIST = ("EURUSD", "GBPUSD", "XAUUSD", "BTCUSD")


def build_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_technical_indicator_adapters(registry)
    return registry


def fetch_balance(source: str, gateway, fallback: float) -> float:
    """Real account balance when connected (gateway or initialised mt5), else fallback."""
    try:
        if gateway is not None:
            return float(gateway.account_info().balance)
        if source == "mt5":
            from services.data_service.mt5_data import mt5 as _mt5
            if _mt5 is not None:
                ai = _mt5.account_info()
                if ai is not None:
                    return float(ai.balance)
    except Exception:  # noqa: BLE001 — sizing falls back to the provided balance
        pass
    return fallback


# --------------------------------------------------------------------------- #
# Candle sources (each returns the full series; the loop slides a window)
# --------------------------------------------------------------------------- #
def load_series(args) -> pd.DataFrame:
    if args.source == "parquet":
        if not args.parquet:
            raise SystemExit("--source parquet requires --parquet <path>")
        df = pd.read_parquet(args.parquet)
        return df.sort_values("timestamp").reset_index(drop=True) if "timestamp" in df else df

    if args.source == "mt5":
        # Real terminal: attach (auto-attach to the running, logged-in terminal),
        # make sure the symbol is in Market Watch, then pull a recent window and
        # replay its tail. Read-only — get_rates never sends an order.
        from services.data_service.mt5_data import connect_to_mt5, get_rates, mt5 as _mt5
        connect_to_mt5()
        if _mt5 is not None:
            _mt5.symbol_select(args.symbol, True)
        end = datetime.now(timezone.utc).replace(tzinfo=None)
        start = end - timedelta(days=args.mt5_days)
        df = get_rates(args.symbol, args.timeframe, start, end)
        return df.sort_values("timestamp").reset_index(drop=True) if "timestamp" in df else df

    # sample (default): deterministic fake candles, plenty for a replay.
    n = max(args.warmup + args.iterations + 50, 400)
    return generate_candles(symbol=args.symbol, timeframe=args.timeframe, n=n, seed=args.seed)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
@dataclass
class DemoTrader:
    adapter: object
    risk: RiskManager
    paper: PaperExecutionService
    symbol: str
    timeframe: str
    volume: Optional[float]            # None => size each trade to risk_pct
    balance: float
    risk_pct: float
    allowlist: tuple[str, ...]
    spread_points: float
    strategy_approved: bool
    mode: TradingMode = TradingMode.PAPER
    allow_live: bool = False
    gateway: object = None                       # set only for --mode live

    # mutable run state
    position: object = None                      # the open PaperTrade, if any
    trades_today: int = 0
    realized_pnl: float = 0.0                    # lifetime, for the equity curve
    day_pnl: float = 0.0                         # within the current calendar day
    current_day: object = None
    equity_curve: list[float] = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)
    rejections: Counter = field(default_factory=Counter)

    def _roll_day(self, now: datetime) -> None:
        """Reset the per-day counters when the calendar day changes."""
        day = now.date()
        if day != self.current_day:
            self.current_day = day
            self.trades_today = 0
            self.day_pnl = 0.0

    def _contract(self) -> float:
        spec = get_symbol_spec(self.symbol)
        return spec.contract_size if spec else 1.0

    def _live_levels(self, side: Side, ref_close: float, sig_sl: float, sig_tp: float):
        """Re-anchor SL/TP to the LIVE quote for a market order.

        The adapter's SL/TP are offsets from the (historical) bar close; a market
        order fills at the current price, so apply the *distances* to the live
        quote and enforce the broker's minimum stop distance. Returns
        ``(entry, sl, tp)`` or ``None`` if no live price is available.
        """
        quote = self.gateway.get_quote(self.symbol)
        entry = quote.ask if side is Side.BUY else quote.bid
        if entry <= 0:
            return None
        spec = get_symbol_spec(self.symbol)
        digits = spec.digits if spec else 5
        min_dist = 0.0
        try:
            min_dist = self.gateway.min_stop_distance(self.symbol)
        except Exception:  # noqa: BLE001 — fall back to the signal distances
            pass
        sl_dist = max(abs(sig_sl - ref_close), min_dist)
        tp_dist = max(abs(sig_tp - ref_close), min_dist)
        if side is Side.BUY:
            return entry, round(entry - sl_dist, digits), round(entry + tp_dist, digits)
        return entry, round(entry + sl_dist, digits), round(entry - tp_dist, digits)

    def _size(self, entry: float, stop_loss: float) -> tuple[float, str]:
        """Lots for a trade: the fixed --volume, else risk-budgeted to risk_pct."""
        if self.volume is not None:
            return self.volume, "fixed"
        spec = get_symbol_spec(self.symbol)
        if spec is None:
            return 0.0, f"no contract spec for {self.symbol}"
        r = compute_lot_size(
            account_balance=self.balance, risk_pct=self.risk_pct,
            entry_price=entry, stop_loss=stop_loss, spec=spec,
        )
        return r.lots, (r.reason or f"sized to {self.risk_pct}% risk")

    def _open_tuples(self) -> tuple[tuple[str, str], ...]:
        return () if self.position is None else ((self.position.symbol, self.position.side.value),)

    def context(self, entry: float) -> RiskContext:
        return RiskContext(
            mode=self.mode,
            allow_live=self.allow_live,
            allowlist=self.allowlist,
            account_balance=self.balance,
            reference_price=entry,
            spread_points=self.spread_points,
            realized_daily_loss=max(0.0, -self.day_pnl),   # today's loss magnitude
            open_trades=self._open_tuples(),
            trades_today=self.trades_today,
            strategy_approved=self.strategy_approved,
            strategy_applicability="direct",
        )

    def _unrealized(self, close: float) -> float:
        if self.position is None:
            return 0.0
        contract = self._contract() * self.position.lot_size
        if self.position.side is Side.BUY:
            return (close - self.position.entry) * contract
        return (self.position.entry - close) * contract

    def step(self, window: pd.DataFrame, now: datetime) -> list[str]:
        """Run one decision cycle on ``window``. Returns zero or more log lines."""
        self._roll_day(now)
        lines: list[str] = []
        high = float(window["high"].iloc[-1])
        low = float(window["low"].iloc[-1])
        close = float(window["close"].iloc[-1])

        # 1) Manage an open position first: intrabar SL/TP exit on this bar.
        hit = self._exit_price(high, low)
        if hit is not None:
            price, reason = hit
            lines.append(self._close(price, reason, now))

        # 2) Ask the adapter for a fresh view.
        signal = self.adapter.generate_signal(window)
        if not signal.is_actionable:
            self.stats["hold"] += 1
            self._mark_equity(close)
            return lines
        self.stats["signal"] += 1

        side = Side.BUY if signal.side is SignalSide.BUY else Side.SELL

        # 3) Flip on an opposing signal; hold on a same-side repeat.
        if self.position is not None:
            if self.position.side is side:
                self.stats["hold_same_side"] += 1
                self._mark_equity(close)
                return lines
            lines.append(self._close(close, "reverse", now))

        # 4) Levels + reference price. For a LIVE market order, re-anchor SL/TP
        #    to the current quote (the bar's levels are stale at fill time).
        sl = round(float(signal.suggested_stop_loss), 5)
        tp = round(float(signal.suggested_take_profit), 5)
        ref = close
        if self.mode is TradingMode.LIVE and self.gateway is not None:
            live = self._live_levels(
                side, close,
                float(signal.suggested_stop_loss), float(signal.suggested_take_profit),
            )
            if live is None:
                self.stats["sizing_skip"] += 1
                lines.append(f"{now:%Y-%m-%d %H:%M}  {side.value:4} {self.symbol}  "
                             f"SKIP: no live quote")
                self._mark_equity(close)
                return lines
            ref, sl, tp = live

        # 5) Size the trade (risk-budgeted unless a fixed volume was given).
        lots, size_reason = self._size(ref, sl)
        if lots <= 0:
            self.stats["sizing_skip"] += 1
            lines.append(f"{now:%Y-%m-%d %H:%M}  {side.value:4} {self.symbol}  "
                         f"SKIP (sizing): {size_reason}")
            self._mark_equity(close)
            return lines

        # 6) Propose → gate → route.
        intent = OrderIntent(
            symbol=self.symbol, side=side, order_type=OrderType.MARKET,
            volume=lots, stop_loss=sl, take_profit=tp,
            strategy_id=self.adapter.name,
        )
        decision = self.risk.evaluate(intent, self.context(ref), now=now)

        if not decision.approved:
            self.stats["rejected"] += 1
            for r in decision.reasons:
                self.rejections[r] += 1
            self.paper.execute(decision, self.context(close), timeframe=self.timeframe,
                               strategy_name=self.adapter.name,
                               strategy_version=self.adapter.version,
                               reference_price=close, now=now)
            lines.append(f"{now:%Y-%m-%d %H:%M}  {side.value:4} {self.symbol}  "
                         f"REJECTED: {decision.reasons[0]}")
            self._mark_equity(close)
            return lines

        self.stats["approved"] += 1
        if self.mode is TradingMode.LIVE:
            lines.append(self._route_live(intent, decision, now))
        else:
            lines.append(self._route_paper(intent, decision, close, now))
        self._mark_equity(close)
        return lines

    def _exit_price(self, high: float, low: float) -> Optional[tuple[float, str]]:
        """Return (price, reason) if the open position's SL/TP is hit this bar."""
        p = self.position
        if p is None:
            return None
        if p.side is Side.BUY:
            if low <= p.stop_loss:
                return p.stop_loss, "stop_loss"
            if p.take_profit and high >= p.take_profit:
                return p.take_profit, "take_profit"
        else:
            if high >= p.stop_loss:
                return p.stop_loss, "stop_loss"
            if p.take_profit and low <= p.take_profit:
                return p.take_profit, "take_profit"
        return None

    def _close(self, price: float, reason: str, now: datetime) -> str:
        closed = self.paper.close(self.position, exit_price=price, now=now)
        pnl = closed.pnl or 0.0
        self.realized_pnl += pnl
        self.day_pnl += pnl
        self.stats["closed"] += 1
        self.stats["win" if pnl > 0 else "loss"] += 1
        self.position = None
        return (f"{now:%Y-%m-%d %H:%M}  {closed.side.value:4} {self.symbol}  "
                f"CLOSE {closed.trade_id} [{reason}] @ {price:.5f} pnl={pnl:+.2f}")

    def _route_paper(self, intent, decision, entry, now) -> str:
        trade = self.paper.execute(
            decision, self.context(entry), timeframe=self.timeframe,
            strategy_name=self.adapter.name, strategy_version=self.adapter.version,
            reference_price=entry, now=now,
        )
        self.position = trade
        self.trades_today += 1
        return (f"{now:%Y-%m-%d %H:%M}  {intent.side.value:4} {self.symbol}  "
                f"PAPER OPEN {trade.trade_id} @ {trade.entry:.5f} "
                f"SL {trade.stop_loss:.5f} TP {trade.take_profit:.5f}")

    def _route_live(self, intent, decision, now) -> str:
        # Defence in depth: the gateway re-checks every live lock and the
        # per-intent approval before it can reach order_send.
        from services.execution_service.mt5_gateway import MT5Gateway  # noqa
        try:
            result = self.gateway.send_order(intent, decision, now=now)
        except Exception as exc:  # noqa: BLE001 — surface refusal/why, never crash loop
            self.stats["live_refused"] += 1
            return f"{now:%Y-%m-%d %H:%M}  {intent.side.value:4} LIVE REFUSED: {exc}"
        self.trades_today += 1
        return f"{now:%Y-%m-%d %H:%M}  {intent.side.value:4} LIVE SENT: {result}"

    def _mark_equity(self, close: float) -> None:
        self.equity_curve.append(self.balance + self.realized_pnl + self._unrealized(close))


# --------------------------------------------------------------------------- #
# Startup validation (feeds strategy_approved)
# --------------------------------------------------------------------------- #
def validate_at_startup(adapter, series: pd.DataFrame) -> bool:
    strategy = adapter_to_backtest_strategy(adapter)
    cut = int(len(series) * 0.7)
    cfg = load_validation_config()
    vin = build_validation_input(
        series.iloc[:cut].reset_index(drop=True), strategy,
        candles_out=series.iloc[cut:].reset_index(drop=True), validation_config=cfg,
    )
    report = StrategyValidator(cfg).validate(vin, strategy_id=adapter.name)
    print(f"  startup validation: {'APPROVED' if report.approved else 'REJECTED'}"
          + ("" if report.approved else f" (failed: {report.failed_rules})"))
    return report.approved


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Paper-first demo trading orchestrator.")
    p.add_argument("--adapter", required=True, help="adapter name (see validate_strategy.py --list)")
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--timeframe", default="H1")
    p.add_argument("--source", choices=["sample", "parquet", "mt5"], default="sample")
    p.add_argument("--parquet", help="candles parquet path (for --source parquet)")
    p.add_argument("--mt5-days", type=int, default=30, help="lookback days for --source mt5")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--iterations", type=int, default=300, help="bars to replay")
    p.add_argument("--warmup", type=int, default=60, help="bars before the first decision")
    p.add_argument("--interval", type=float, default=0.0, help="seconds to sleep between bars")
    p.add_argument("--volume", type=float, default=None,
                   help="fixed lot size; omit to risk-size each trade to --risk-pct")
    p.add_argument("--risk-pct", type=float, default=None,
                   help="percent of balance to risk per trade (default: risk.yaml limit)")
    p.add_argument("--balance", type=float, default=10_000.0,
                   help="account balance for sizing; overridden by the real balance on --source mt5/live")
    p.add_argument("--seed", type=int, default=11, help="sample-candle seed")
    p.add_argument("--assume-approved", action="store_true",
                   help="force strategy_approved=True to demo the execution leg")
    p.add_argument("--skip-validation", action="store_true",
                   help="don't run startup validation (implies unapproved unless --assume-approved)")
    p.add_argument("--log-dir", help="directory for trades.jsonl / audit.jsonl (default data/paper_trades)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    registry = build_registry()
    if args.adapter not in registry:
        print(f"unknown adapter {args.adapter!r}; known: {registry.names()}")
        return 2
    adapter = registry.get(args.adapter)

    settings = Settings()
    risk_cfg = load_risk_config()
    allowlist = tuple(settings.symbol_allowlist) or DEFAULT_ALLOWLIST
    if args.symbol not in allowlist:
        allowlist = (args.symbol, *allowlist)

    print("=" * 72)
    print(f" DEMO TRADER  adapter={adapter.name}  symbol={args.symbol} "
          f"{args.timeframe}  mode={args.mode}  source={args.source}")
    print("=" * 72)

    series = load_series(args)
    print(f"  series: {len(series)} candles")

    # --- strategy approval -------------------------------------------------- #
    if args.assume_approved:
        approved = True
        print("  startup validation: SKIPPED -> strategy_approved=True (DEMO ONLY)")
    elif args.skip_validation:
        approved = False
        print("  startup validation: skipped -> strategy_approved=False")
    else:
        approved = validate_at_startup(adapter, series)

    # --- mode / live-lock pre-flight --------------------------------------- #
    mode = TradingMode.PAPER
    gateway = None
    if args.mode == "live":
        mode = TradingMode.LIVE
        problems = []
        if settings.trading_mode is not TradingMode.LIVE:
            problems.append("TRADING_MODE != live in .env")
        if not settings.allow_live_trading:
            problems.append("ALLOW_LIVE_TRADING != true in .env")
        if TradingMode.LIVE not in risk_cfg.allowed_modes:
            problems.append("'live' not in allowed_modes in config/risk.yaml")
        if problems:
            print("\n  LIVE refused — locks not set (this script will not flip them):")
            for prob in problems:
                print(f"    - {prob}")
            print("  See docs/RUNBOOK_MT5_DEMO_ACCOUNT.md section 7. Staying safe; exiting.")
            return 3
        from services.execution_service.mt5_gateway import MT5Gateway
        gateway = MT5Gateway()
        gateway.connect()
        print("  LIVE locks satisfied; gateway connected (orders will be SENT).")

    # --- sizing: real balance + risk budget -------------------------------- #
    risk_pct = args.risk_pct if args.risk_pct is not None else risk_cfg.max_risk_per_trade_pct
    balance = fetch_balance(args.source, gateway, args.balance)
    sizing = (f"fixed {args.volume} lots" if args.volume is not None
              else f"auto: {risk_pct}% risk of {balance:,.2f}")
    print(f"  balance={balance:,.2f}  sizing={sizing}")

    # --- wire the orchestrator --------------------------------------------- #
    log_dir = Path(args.log_dir) if args.log_dir else None
    trade_log = TradeLogStore(log_dir / "trades.jsonl") if log_dir else TradeLogStore()
    audit_log = AuditLog(log_dir / "audit.jsonl") if log_dir else AuditLog()
    trader = DemoTrader(
        adapter=adapter,
        risk=RiskManager(risk_cfg),
        paper=PaperExecutionService(trade_log=trade_log, audit_log=audit_log, config=risk_cfg),
        symbol=args.symbol, timeframe=args.timeframe,
        volume=args.volume, balance=balance, risk_pct=risk_pct, allowlist=allowlist,
        spread_points=min(10.0, risk_cfg.max_spread_points),
        strategy_approved=approved, mode=mode, allow_live=settings.allow_live_trading,
        gateway=gateway,
    )

    # --- replay loop -------------------------------------------------------- #
    start = max(args.warmup, adapter.get_metadata().min_candles)
    end = min(len(series), start + args.iterations)
    if mode is TradingMode.LIVE:
        # Live trades the present: act once on the most recent bar, never replay
        # stale history (whose levels would be invalid against the live price).
        start, end = max(start, len(series) - 1), len(series)
        print("  LIVE: acting on the latest bar only (no historical replay)")
    print(f"  replaying bars {start}..{end} ({end - start} decisions)\n")
    has_ts = "timestamp" in series.columns
    for i in range(start, end):
        window = series.iloc[: i + 1]
        if has_ts:
            ts = window["timestamp"].iloc[-1]
            now = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        else:
            now = datetime.now()
        for line in trader.step(window, now):
            print("  " + line)
        if args.interval > 0:
            time.sleep(args.interval)

    # --- summary ------------------------------------------------------------ #
    s = trader.stats
    decisions = s["hold"] + s["hold_same_side"] + s["signal"]
    print("\n" + "-" * 72)
    print(f"  decisions={decisions}  signals={s['signal']}  approved={s['approved']}  "
          f"rejected={s['rejected']}  holds={s['hold'] + s['hold_same_side']}")
    print(f"  paper round-trips closed={s['closed']}  wins={s['win']}  losses={s['loss']}"
          + (f"  win_rate={s['win'] / s['closed']:.1%}" if s["closed"] else ""))
    print(f"  realized paper pnl: {trader.realized_pnl:+.2f} (account ccy)")

    eq = trader.equity_curve
    if len(eq) >= 3:
        ppy = _infer_periods_per_year(
            pd.to_datetime(series["timestamp"]) if has_ts else None, len(series)
        )
        sharpe = annualised_sharpe(eq, ppy)
        abs_dd, pct_dd = max_drawdown(eq)
        print(f"  run Sharpe (annualised, paper equity): "
              f"{'n/a' if sharpe is None else f'{sharpe:.2f}'}"
              f"   max drawdown: {abs_dd:.2f} ({pct_dd:.1%})")
    if trader.rejections:
        print("  rejections by reason:")
        for reason, count in trader.rejections.most_common():
            print(f"    {count:>4}x  {reason}")
    if not log_dir:
        print("  logs: data/paper_trades/{trades,audit}.jsonl")
    print("-" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
