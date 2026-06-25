"""Always-on, AI-driven trading loop (M29) — the missing automation piece.

This is the daemon the project was missing: on a cadence (each closed bar) it
pulls market data, asks the **AI brain** (:mod:`apps.agent.ai_decider`) for the
single best move, watches/manages the open position, and routes any approved
trade through the deterministic ``RiskManager`` to **paper** (default) or the
**MT5 demo/live** gateway behind the project's hard locks.

Safety (unchanged from the rest of the system):

* **The AI proposes; the RiskManager decides; the gateway executes.** The AI has
  no live-execution tool — its move is a proposal that is re-gated here every
  tick. Paper is the default and needs no locks.
* **demo/live is opt-in and this script never flips a switch.** ``--mode demo``
  (or ``live``) only *attempts* the real gateway; it still requires
  ``TRADING_MODE=live`` + ``ALLOW_LIVE_TRADING=true`` in the environment **and**
  ``live`` in ``allowed_modes`` in ``config/risk.yaml``. If those aren't set, the
  loop prints exactly what's missing and exits without trading.
* **--mock-ai** runs the whole loop with a deterministic offline brain (no API
  key, no network) — used by tests and for a dry run of the pipeline.

Examples
--------
    # Dry single decision, no key/terminal needed (offline)
    python scripts/ai_trade_loop.py --once --source sample --mock-ai --symbol XAUUSD

    # Real data + real AI, paper routing (needs a running MT5 terminal + AI_API_KEY)
    python scripts/ai_trade_loop.py --symbol XAUUSD --timeframe H1 --mode paper --max-ticks 3

    # MT5 demo execution (after the three locks are set + Algo Trading is ON)
    python scripts/ai_trade_loop.py --symbol XAUUSD --timeframe H1 --mode demo
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Run-as-script bootstrap: put the repo root on sys.path so the sibling
# ``scripts.trade_demo`` module imports when launched as
# ``python scripts/ai_trade_loop.py`` (apps/services come from the install).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from apps.agent.ai_decider import AIDecider
from apps.agent.llm_runner import LLMResponse, MockLLMClient, ToolUseBlock, build_client
from services.backtest_service.metrics import annualised_sharpe, max_drawdown
from services.config_loader import Settings, load_risk_config
from services.data_service.sample_data import TIMEFRAME_MINUTES, generate_candles
from services.execution_service.audit_log import AuditLog
from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import TradeLogStore
from services.models import OrderIntent, OrderType, Side, TradingMode
from services.risk_service.position_sizing import compute_lot_size
from services.risk_service.risk_manager import RiskContext, RiskManager
from services.risk_service.symbol_specs import get_symbol_spec

# trade_demo holds the paper orchestrator we reuse wholesale for --mode paper.
from scripts.trade_demo import DemoTrader

DEFAULT_ALLOWLIST = ("EURUSD", "GBPUSD", "XAUUSD", "BTCUSD")


# --------------------------------------------------------------------------- #
# Mock brain (offline; --mock-ai)
# --------------------------------------------------------------------------- #
def _mock_responder(action: str, strategy: Optional[str]):
    """A deterministic offline brain that submits a fixed decision each call.

    ``action`` is 'hold' (default), 'buy' or 'sell'. For an entry it submits an
    ``open`` decision (SL/TP synthesised by the decider). Useful for exercising
    the full route offline; pair an entry with ``--assume-approved`` since sample
    strategies rarely pass the validation gate.
    """
    def respond(*, system, messages, tools) -> LLMResponse:
        if action in ("buy", "sell"):
            payload = {
                "action": "open", "side": action, "strategy": strategy,
                "confidence": 0.6, "rationale": f"mock {action} decision",
            }
        else:
            payload = {"action": "hold", "rationale": "mock hold (no edge)"}
        return LLMResponse(
            content=[ToolUseBlock(id="mock-decision", name="submit_decision", input=payload)],
            stop_reason="tool_use",
        )
    return respond


# --------------------------------------------------------------------------- #
# Live/demo router: single AI call per tick, then manage the real position
# --------------------------------------------------------------------------- #
class LiveTrader:
    """Routes the AI's decision to the MT5 gateway and manages the position.

    Used for ``--mode demo`` / ``--mode live`` (and offline tests via a mock
    gateway). Reuses the standalone risk + sizing services; the same hard locks
    are re-checked inside the gateway before any ``order_send``.
    """

    def __init__(
        self,
        *,
        decider: AIDecider,
        gateway,
        risk: RiskManager,
        symbol: str,
        timeframe: str,
        risk_pct: float,
        allowlist: tuple[str, ...],
        spread_points: float,
        volume: Optional[float] = None,
        strategy_approved: bool = True,
    ) -> None:
        self.decider = decider
        self.gateway = gateway
        self.risk = risk
        self.symbol = symbol
        self.timeframe = timeframe
        self.risk_pct = risk_pct
        self.allowlist = allowlist
        self.spread_points = spread_points
        self.volume = volume
        self.strategy_approved = strategy_approved
        self.trades_today = 0
        self.current_day = None
        self.opens = 0
        self.closes = 0

    def _roll_day(self, now: datetime) -> None:
        if now.date() != self.current_day:
            self.current_day = now.date()
            self.trades_today = 0

    def _live_levels(self, side: Side, close: float, sig_sl: float, sig_tp: float):
        """Entry + SL/TP re-anchored to the live quote (broker min distance enforced)."""
        quote = self.gateway.get_quote(self.symbol)
        entry = quote.ask if side is Side.BUY else quote.bid
        spec = get_symbol_spec(self.symbol)
        digits = spec.digits if spec else 5
        try:
            min_dist = self.gateway.min_stop_distance(self.symbol)
        except Exception:  # noqa: BLE001 — fall back to the signal distances
            min_dist = 0.0
        sl_dist = max(abs(sig_sl - close), min_dist)
        tp_dist = max(abs(sig_tp - close), min_dist)
        if side is Side.BUY:
            return entry, round(entry - sl_dist, digits), round(entry + tp_dist, digits)
        return entry, round(entry + sl_dist, digits), round(entry - tp_dist, digits)

    def tick(self, window: pd.DataFrame, now: datetime) -> list[str]:
        self._roll_day(now)
        lines: list[str] = []
        close = float(window["close"].iloc[-1])

        # 1) Snapshot the account + open position; feed them to the AI.
        balance = float(self.gateway.account_info().balance)
        positions = self.gateway.positions(self.symbol)
        pos = positions[0] if positions else None
        self.decider.update_context(
            account_balance=balance,
            open_position=pos.model_dump(mode="json") if pos is not None else None,
        )

        # 2) One AI call → an AdapterSignal proposal + the structured decision.
        signal = self.decider.generate_signal(window)
        decision = self.decider.last_decision
        if pos is not None:
            lines.append(f"  watching {pos.side} {pos.symbol} {pos.volume} lots "
                         f"@ {pos.entry_price} P&L {pos.profit:+.2f}")

        # 3) Explicit close of the open position.
        if decision is not None and decision.is_close:
            if pos is None:
                return lines + [f"{now:%Y-%m-%d %H:%M}  CLOSE requested but no open position"]
            res = self.gateway.close_position(pos.ticket, now=now)
            self.closes += 1
            return lines + [f"{now:%Y-%m-%d %H:%M}  CLOSED {self.symbol} ticket={pos.ticket} "
                            f"-> {res.comment} ({decision.rationale})"]

        # 4) Non-actionable → hold.
        if not signal.is_actionable:
            return lines + [f"{now:%Y-%m-%d %H:%M}  HOLD {self.symbol}: {signal.reason}"]

        side = Side.BUY if signal.side.value == "BUY" else Side.SELL

        # 5) Position-aware entry: skip same-side stacking; flip on the opposite.
        if pos is not None:
            if pos.side == side.value:
                return lines + [f"{now:%Y-%m-%d %H:%M}  already {side.value} {self.symbol}; holding"]
            res = self.gateway.close_position(pos.ticket, now=now)
            self.closes += 1
            lines.append(f"{now:%Y-%m-%d %H:%M}  FLIP: closed {pos.side} ticket={pos.ticket} "
                         f"-> {res.comment}")

        # 6) Re-anchor levels to the live quote, size to risk, gate, send.
        entry, sl, tp = self._live_levels(
            side, close, float(signal.suggested_stop_loss), float(signal.suggested_take_profit)
        )
        lots, size_reason = self._size(entry, sl, balance)
        if lots <= 0:
            return lines + [f"{now:%Y-%m-%d %H:%M}  {side.value} {self.symbol} SKIP (sizing): {size_reason}"]

        intent = OrderIntent(
            symbol=self.symbol, side=side, order_type=OrderType.MARKET,
            volume=lots, stop_loss=sl, take_profit=tp,
            strategy_id=signal.source_strategy or self.decider.name,
        )
        context = RiskContext(
            mode=TradingMode.LIVE, allow_live=True, allowlist=self.allowlist,
            account_balance=balance, reference_price=entry, spread_points=self.spread_points,
            realized_daily_loss=0.0, open_trades=(), trades_today=self.trades_today,
            strategy_approved=self.strategy_approved, strategy_applicability="direct",
        )
        decision_r = self.risk.evaluate(intent, context, now=now)
        if not decision_r.approved:
            return lines + [f"{now:%Y-%m-%d %H:%M}  {side.value} {self.symbol} "
                            f"REJECTED: {decision_r.reasons[0] if decision_r.reasons else 'risk'}"]

        try:
            result = self.gateway.send_order(intent, decision_r, now=now)
        except Exception as exc:  # noqa: BLE001 — surface refusal, never crash the loop
            return lines + [f"{now:%Y-%m-%d %H:%M}  {side.value} {self.symbol} LIVE REFUSED: {exc}"]
        self.trades_today += 1
        self.opens += 1
        return lines + [f"{now:%Y-%m-%d %H:%M}  {side.value} {self.symbol} SENT: "
                        f"{result.volume} lots @ {result.price} SL {sl} TP {tp} "
                        f"(ticket={result.position_id}) - {signal.reason}"]

    def _size(self, entry: float, stop_loss: float, balance: float) -> tuple[float, str]:
        if self.volume is not None:
            return self.volume, "fixed"
        spec = get_symbol_spec(self.symbol)
        if spec is None:
            return 0.0, f"no contract spec for {self.symbol}"
        r = compute_lot_size(
            account_balance=balance, risk_pct=self.risk_pct,
            entry_price=entry, stop_loss=stop_loss, spec=spec,
        )
        return r.lots, (r.reason or f"sized to {self.risk_pct}% risk")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def _fetch_mt5_window(symbol: str, timeframe: str, n: int) -> pd.DataFrame:
    """Most-recent ``n`` real candles from a running terminal (read-only).

    Uses ``copy_rates_from_pos`` (newest ``n`` bars *by position*) so the
    broker's server-time vs UTC offset can never drop or misalign the latest
    bar — the loop only ever wants "the last N candles".
    """
    from services.data_service.mt5_data import connect_to_mt5, get_latest_rates, mt5 as _mt5
    connect_to_mt5()
    if _mt5 is not None:
        _mt5.symbol_select(symbol, True)
    df = get_latest_rates(symbol, timeframe, n)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df.reset_index(drop=True)


# A live feed's newest bar is at most ~1 period old. Brokers also stamp bars in
# server time (commonly UTC+2/+3), so we measure age against a generous floor
# that absorbs that offset: a live feed never trips it, but a disconnected
# terminal serving cached history (bars hours/days old) does.
STALE_FLOOR = timedelta(hours=6)


def _bar_age(window: pd.DataFrame, now: Optional[datetime] = None) -> timedelta:
    """Age of the newest bar relative to wall-clock UTC (naive)."""
    now = now if now is not None else datetime.now(timezone.utc).replace(tzinfo=None)
    last = window["timestamp"].iloc[-1]
    last = last.to_pydatetime() if hasattr(last, "to_pydatetime") else last
    return now - last


def _is_stale(window: pd.DataFrame, timeframe: str, now: Optional[datetime] = None) -> bool:
    """True when the newest bar is too old to safely trade on."""
    minutes = TIMEFRAME_MINUTES.get(timeframe.upper(), 60)
    period = timedelta(minutes=minutes)
    return _bar_age(window, now) > max(STALE_FLOOR, 3 * period)


def _print_mt5_status(symbol: str) -> None:
    """Read-only connection preflight: surface a disconnected terminal loudly."""
    try:
        from services.data_service.mt5_data import connect_to_mt5, mt5 as _mt5
        connect_to_mt5()
        ti = _mt5.terminal_info() if _mt5 is not None else None
    except Exception as exc:  # noqa: BLE001 — never block the loop on a preflight
        print(f"  MT5 status: check failed ({type(exc).__name__}: {exc})")
        return
    if ti is None:
        print("  MT5 status: MetaTrader5 package unavailable")
        return
    connected = getattr(ti, "connected", None)
    trade_allowed = getattr(ti, "trade_allowed", None)
    print(f"  MT5 status: connected={connected} trade_allowed={trade_allowed}")
    if connected is False:
        print("  WARNING: terminal is NOT connected to the broker — bars will be "
              "stale until you log in / restore the feed.")


def _seconds_to_next_bar(timeframe: str) -> float:
    """Seconds until the next bar boundary for ``timeframe`` (+2s settle)."""
    minutes = TIMEFRAME_MINUTES.get(timeframe.upper(), 60)
    period = minutes * 60
    now = time.time()
    return max(5.0, period - (now % period) + 2.0)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Always-on, AI-driven trading loop.")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--timeframe", default="H1")
    p.add_argument("--source", choices=["sample", "mt5", "local"], default="sample",
                   help="market data for the AI: 'mt5' = real terminal candles, "
                        "'local' = resampled candles from the pricer tick dump (offline replay)")
    p.add_argument("--validate-source", choices=["sample", "mt5", "local"], default=None,
                   help="data source for the validation gate + the AI's backtests "
                        "(default: same as --source). Use 'local' with --source mt5 to "
                        "validate on deep history while trading live data")
    p.add_argument("--mode", choices=["paper", "demo", "live"], default="paper")
    p.add_argument("--once", action="store_true", help="run a single decision then exit")
    p.add_argument("--max-ticks", type=int, default=0, help="stop after N ticks (0 = run forever)")
    p.add_argument("--interval", type=float, default=0.0,
                   help="seconds to sleep between ticks (0 = align to the bar for mt5)")
    p.add_argument("--warmup", type=int, default=80, help="bars before the first decision (sample replay)")
    p.add_argument("--lookback", type=int, default=64, help="min candles the AI needs")
    p.add_argument("--validate-bars", type=int, default=600,
                   help="bars the deterministic validation gate runs on; the validator "
                        "needs >=100 trades, so the 600 default rarely validates -- pass "
                        "~4000+ (esp. on M15/M30) to make a real trade reachable")
    p.add_argument("--min-trades", type=int, default=30,
                   help="override the validator's minimum-trades gate (validator default 100). "
                        "Lower accepts strategies on less data -- weaker statistical confidence")
    p.add_argument("--force-position", action="store_true",
                   help="DEMO/PAPER ONLY (refused on --mode live): if nothing validates, trade "
                        "the best-scoring adapter that currently signals (logged UNVALIDATED; "
                        "the RiskManager still gates it)")
    p.add_argument("--mt5-days", type=int, default=30,
                   help="(deprecated/unused) the live poll now fetches the latest N bars by position")
    p.add_argument("--risk-pct", type=float, default=None,
                   help="percent of balance to risk per trade (default: risk.yaml limit)")
    p.add_argument("--volume", type=float, default=None, help="fixed lot size (omit to risk-size)")
    p.add_argument("--balance", type=float, default=10_000.0, help="paper balance for sizing")
    p.add_argument("--max-iterations", type=int, default=12, help="max AI tool-loop steps per tick")
    p.add_argument("--mock-ai", action="store_true", help="offline deterministic brain (no API/network)")
    p.add_argument("--mock-action", choices=["hold", "buy", "sell"], default="hold",
                   help="decision the mock brain submits (with --mock-ai)")
    p.add_argument("--mock-strategy", default=None, help="strategy name for a mock entry")
    p.add_argument("--assume-approved", action="store_true",
                   help="skip the AI's validation gate (DEMO ONLY; the risk gate still runs)")
    p.add_argument("--seed", type=int, default=11, help="sample-candle seed")
    p.add_argument("--log-dir", help="dir for trades.jsonl / audit.jsonl (paper mode)")
    return p.parse_args(argv)


def _build_client(args, settings):
    if args.mock_ai:
        return MockLLMClient(responder=_mock_responder(args.mock_action, args.mock_strategy))
    return build_client(settings)


def _live_preflight(settings: Settings, risk_cfg) -> list[str]:
    """Return the unmet hard locks for demo/live (empty list = good to go)."""
    problems = []
    if settings.trading_mode is not TradingMode.LIVE:
        problems.append("TRADING_MODE != live in the environment")
    if not settings.allow_live_trading:
        problems.append("ALLOW_LIVE_TRADING != true in the environment")
    if TradingMode.LIVE not in risk_cfg.allowed_modes:
        problems.append("'live' not in allowed_modes in config/risk.yaml")
    return problems


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = Settings()
    risk_cfg = load_risk_config()
    risk_pct = args.risk_pct if args.risk_pct is not None else risk_cfg.max_risk_per_trade_pct

    allowlist = tuple(settings.symbol_allowlist) or DEFAULT_ALLOWLIST
    if args.symbol not in allowlist:
        allowlist = (args.symbol, *allowlist)

    # Hard safety guard: forcing an unvalidated trade is for paper/demo only.
    if args.force_position and args.mode == "live":
        print("  REFUSED: --force-position is paper/demo only and must never trade an "
              "unvalidated position on a live (real-money) account. Exiting.")
        return 3
    force_position = args.force_position and args.mode != "live"

    print("=" * 72)
    print(f" AI TRADE LOOP  symbol={args.symbol} {args.timeframe}  mode={args.mode}  "
          f"source={args.source}  brain={'mock' if args.mock_ai else settings.ai_model}")
    print("=" * 72)

    client = _build_client(args, settings)
    decider = AIDecider(
        symbol=args.symbol, timeframe=args.timeframe, client=client, source=args.source,
        validate_source=args.validate_source,
        risk_pct=risk_pct, require_validation=not args.assume_approved,
        validate_bars=args.validate_bars, min_trades=args.min_trades,
        force_position=force_position,
        lookback=args.lookback, max_iterations=args.max_iterations,
        on_event=lambda kind, data: print(f"    - {kind}: {data.get('name', data)}"),
    )
    if args.assume_approved:
        print("  validation gate: SKIPPED (DEMO ONLY) - strategy_approved forced True")
    if args.min_trades != 100:
        print(f"  validation gate: minimum_trades lowered to {args.min_trades} "
              f"(weaker statistical confidence)")
    if force_position:
        print("  force-position: ON (DEMO/PAPER only) - will trade the best-scoring "
              "candidate when nothing validates; such trades are logged UNVALIDATED")

    # --- mode / gateway pre-flight ---------------------------------------- #
    mode = TradingMode.PAPER if args.mode == "paper" else TradingMode.LIVE
    gateway = None
    if mode is TradingMode.LIVE:
        problems = _live_preflight(settings, risk_cfg)
        if problems:
            print("\n  demo/live refused — locks not set (this script will not flip them):")
            for prob in problems:
                print(f"    - {prob}")
            print("  See docs/RUNBOOK_AI_TRADING_LOOP.md. Staying safe; exiting.")
            return 3
        from services.execution_service.mt5_gateway import MT5Gateway
        gateway = MT5Gateway()
        gateway.connect()
        acct = gateway.account_info()
        print(f"  gateway connected: login={acct.login} server={acct.server} "
              f"balance={acct.balance:,.2f} ({args.mode.upper()}; orders WILL be sent)")

    # --- wire the per-tick trader ----------------------------------------- #
    spread_points = min(10.0, risk_cfg.max_spread_points)
    if mode is TradingMode.PAPER:
        log_dir = Path(args.log_dir) if args.log_dir else None
        trade_log = TradeLogStore(log_dir / "trades.jsonl") if log_dir else TradeLogStore()
        audit_log = AuditLog(log_dir / "audit.jsonl") if log_dir else AuditLog()
        trader = DemoTrader(
            adapter=decider, risk=RiskManager(risk_cfg),
            paper=PaperExecutionService(trade_log=trade_log, audit_log=audit_log, config=risk_cfg),
            symbol=args.symbol, timeframe=args.timeframe, volume=args.volume,
            balance=args.balance, risk_pct=risk_pct, allowlist=allowlist,
            spread_points=spread_points, strategy_approved=True, mode=TradingMode.PAPER,
        )
    else:
        trader = LiveTrader(
            decider=decider, gateway=gateway, risk=RiskManager(risk_cfg),
            symbol=args.symbol, timeframe=args.timeframe, risk_pct=risk_pct,
            allowlist=allowlist, spread_points=spread_points, volume=args.volume,
            strategy_approved=True,
        )

    # --- run --------------------------------------------------------------- #
    try:
        if args.source in ("sample", "local"):
            return _run_sample(args, trader, decider, mode)
        return _run_live_poll(args, trader, decider, mode)
    except KeyboardInterrupt:
        print("\n  interrupted — shutting down cleanly.")
        return 0


def _run_one(trader, mode, window, now) -> None:
    lines = trader.step(window, now) if mode is TradingMode.PAPER else trader.tick(window, now)
    for line in lines:
        print("  " + line if not line.startswith("  ") else line)


def _run_sample(args, trader, decider, mode) -> int:
    """Offline replay over a candle series (generated 'sample' or 'local' history)."""
    ticks = 1 if args.once else (args.max_ticks or 50)
    n = max(args.warmup + ticks + args.lookback + 10, 200)
    if args.source == "local":
        from services.data_service.local_data import load_local_candles
        series = load_local_candles(args.symbol, args.timeframe, n)
        print(f"  local replay: {len(series)} bars from the pricer history store")
    else:
        series = generate_candles(symbol=args.symbol, timeframe=args.timeframe, n=n, seed=args.seed)
    start = max(args.warmup, args.lookback)
    end = min(len(series), start + ticks)
    print(f"  replay: bars {start}..{end} ({end - start} ticks)\n")
    for i in range(start, end):
        window = series.iloc[: i + 1]
        ts = window["timestamp"].iloc[-1]
        now = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        _run_one(trader, mode, window, now)
        if args.interval > 0:
            time.sleep(args.interval)
    _summary(trader, mode)
    return 0


def _run_live_poll(args, trader, decider, mode) -> int:
    """Poll the most-recent real candle each bar and act on it."""
    _print_mt5_status(args.symbol)
    tick = 0
    n = max(args.lookback + 50, 200)
    while True:
        tick += 1
        window = _fetch_mt5_window(args.symbol, args.timeframe, n)
        ts = window["timestamp"].iloc[-1] if "timestamp" in window.columns else datetime.now()
        now = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        print(f"\n  tick {tick}  latest bar {now:%Y-%m-%d %H:%M}  close={float(window['close'].iloc[-1]):.5f}")
        if _is_stale(window, args.timeframe):
            hrs = _bar_age(window).total_seconds() / 3600.0
            print(f"  STALE DATA: newest bar is {hrs:.1f}h old — terminal likely "
                  f"disconnected or {args.symbol} not streaming; skipping tick (no decision)")
        else:
            _run_one(trader, mode, window, now)
        if args.once or (args.max_ticks and tick >= args.max_ticks):
            break
        wait = args.interval if args.interval > 0 else _seconds_to_next_bar(args.timeframe)
        print(f"  sleeping {wait:.0f}s to next bar...")
        time.sleep(wait)
    _summary(trader, mode)
    return 0


def _summary(trader, mode) -> None:
    print("\n" + "-" * 72)
    if mode is TradingMode.PAPER:
        s = trader.stats
        print(f"  signals={s['signal']}  approved={s['approved']}  rejected={s['rejected']}  "
              f"holds={s['hold'] + s['hold_same_side']}")
        print(f"  paper closed={s['closed']}  wins={s['win']}  losses={s['loss']}  "
              f"realized pnl={trader.realized_pnl:+.2f}")
        eq = trader.equity_curve
        if len(eq) >= 3:
            sharpe = annualised_sharpe(eq, None)
            abs_dd, pct_dd = max_drawdown(eq)
            print(f"  run Sharpe: {'n/a' if sharpe is None else f'{sharpe:.2f}'}  "
                  f"max drawdown: {abs_dd:.2f} ({pct_dd:.1%})")
    else:
        print(f"  opens={trader.opens}  closes={trader.closes}  trades_today={trader.trades_today}")
    print("-" * 72)


if __name__ == "__main__":
    raise SystemExit(main())
