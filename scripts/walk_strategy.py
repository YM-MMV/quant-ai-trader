"""Walk ONE real strategy adapter end-to-end: backtest -> validate -> paper trade.

Mocks/fake-data only. No MT5, no network, no secrets. Deterministic (seeded).

Flow:
    1. Generate fake candles                      (sample_data)
    2. Pick a real adapter (MACD Oscillator)       (strategy_service.adapters)
    3. Bridge AdapterSignal -> BacktestSignal and backtest it  (SimpleBacktester)
    4. Validate the strategy                       (StrategyValidator)
    5. Find the adapter's latest actionable signal -> OrderIntent
    6. Gate it through the deterministic RiskManager
    7. Open a paper trade + write the audit log    (PaperExecutionService)
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from services.backtest_service.simple_backtester import (
    BacktestSignal, Direction, SimpleBacktester,
)
from services.backtest_service.strategy_validator import (
    StrategyValidator, build_validation_input,
)
from services.data_service import features as feats
from services.data_service.sample_data import generate_candles
from services.execution_service.audit_log import AuditLog
from services.execution_service.paper_execution import PaperExecutionService
from services.execution_service.trade_log import TradeLogStore
from services.models import (
    MarketFeatures, OrderIntent, OrderType, Side, TradingMode,
)
from services.risk_service.risk_manager import RiskContext, RiskManager
from services.strategy_service.adapters.macd_oscillator import MACDOscillatorAdapter
from services.strategy_service.base import SignalSide

SYMBOL = "EURUSD"
TIMEFRAME = "H1"          # MACD adapter supports H1
NOW = datetime(2024, 1, 8, 0, 0, 0)


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n {title}\n{'=' * 70}")


def adapter_as_backtest_strategy(adapter):
    """Bridge a real AdapterSignal-producing adapter to a backtester Strategy."""
    side_map = {SignalSide.BUY: Direction.BUY, SignalSide.SELL: Direction.SELL}

    def strategy(window: pd.DataFrame) -> BacktestSignal:
        sig = adapter.generate_signal(window)  # causal: only sees this window
        if sig.side not in side_map:
            return BacktestSignal(Direction.NONE)
        return BacktestSignal(
            direction=side_map[sig.side],
            stop_loss=sig.suggested_stop_loss,
            take_profit=sig.suggested_take_profit,
            reason=sig.reason,
        )

    return strategy


def main() -> None:
    adapter = MACDOscillatorAdapter()
    meta = adapter.get_metadata()

    rule(f"STRATEGY: {meta.name} v{meta.version}  ({meta.source_strategy})")
    print(f"  category   : {meta.category}")
    print(f"  timeframes : {meta.supported_timeframes}")
    print(f"  min_candles: {meta.min_candles}")
    print(f"  desc       : {meta.description}")

    # -- 1. Candles ------------------------------------------------------- #
    candles = generate_candles(symbol=SYMBOL, timeframe=TIMEFRAME, n=400, seed=11)
    print(f"\n  generated {len(candles)} {TIMEFRAME} candles for {SYMBOL} "
          f"(close {candles['close'].iloc[0]:.5f} -> {candles['close'].iloc[-1]:.5f})")

    # -- 2 + 3. Backtest the REAL adapter --------------------------------- #
    rule("BACKTEST  (SimpleBacktester, realistic friction)")
    strategy = adapter_as_backtest_strategy(adapter)
    report = SimpleBacktester().run(candles, strategy)
    m = report.metrics
    print(f"  bars            : {report.n_bars}")
    print(f"  trades          : {len(report.trades)}")
    print(f"  rejected signals: {report.rejected_signals} "
          f"(no-stop: {report.rejected_no_stop})")
    print(f"  wins / losses   : {m.wins} / {m.losses}")
    print(f"  win rate        : {m.win_rate:.1%}")
    print(f"  net profit      : {m.net_profit:+.5f}")
    pf = "n/a (no losers)" if m.profit_factor is None else f"{m.profit_factor:.2f}"
    print(f"  profit factor   : {pf}")
    print(f"  max drawdown    : {m.max_drawdown:.5f} ({m.max_drawdown_pct:.2%})")
    print(f"  expectancy      : {m.expectancy:+.5f} / trade   (avg R {m.average_r:+.3f})")
    for t in report.trades[:5]:
        print(f"    - {t.direction.value:4} entry={t.entry_price:.5f} "
              f"exit={t.exit_price:.5f} pnl={t.pnl:+.5f} "
              f"R={t.r_multiple:+.2f} [{t.exit_reason}]")
    if len(report.trades) > 5:
        print(f"    ... and {len(report.trades) - 5} more")

    # -- 4. Validate ------------------------------------------------------ #
    rule("VALIDATION  (StrategyValidator verdict)")
    vinput = build_validation_input(candles, strategy)
    vreport = StrategyValidator().validate(vinput, strategy_id=meta.name)
    print(f"  approved : {vreport.approved}")
    for reason in (vreport.failed_rules or ["(no failed rules)"]):
        print(f"    - failed: {reason}")

    # -- 5. Find the adapter's latest actionable signal ------------------- #
    rule("LIVE SIGNAL  (latest actionable proposal from the adapter)")
    fired_idx = fired_sig = None
    for i in range(len(candles) - 1, meta.min_candles - 1, -1):
        s = adapter.generate_signal(candles.iloc[: i + 1])
        if s.is_actionable:
            fired_idx, fired_sig = i, s
            break

    if fired_sig is None:
        print("  adapter never produced an actionable signal on this series.")
        print("  (Nothing to paper-trade — the risk gate is never reached.)")
        return

    bar = candles.iloc[fired_idx]
    entry_price = float(bar["close"])
    side = Side.BUY if fired_sig.side is SignalSide.BUY else Side.SELL
    print(f"  bar #{fired_idx} @ {bar['timestamp']}")
    print(f"  side       : {side.value}  (confidence {fired_sig.confidence:.2f})")
    print(f"  reason     : {fired_sig.reason}")
    print(f"  entry      : {entry_price:.5f}")
    print(f"  suggest SL : {fired_sig.suggested_stop_loss:.5f}")
    print(f"  suggest TP : {fired_sig.suggested_take_profit:.5f}")

    # Feature snapshot at the firing bar (for the trade record).
    win = candles.iloc[: fired_idx + 1]
    raw = {
        "rsi": feats.rsi(win["close"]).iloc[-1],
        "atr": feats.atr(win["high"], win["low"], win["close"]).iloc[-1],
    }
    snapshot = MarketFeatures(
        symbol=SYMBOL, timeframe=TIMEFRAME,
        timestamp=bar["timestamp"].to_pydatetime(),
        features={k: float(v) for k, v in raw.items() if pd.notna(v)},
    )

    # -- 6. Propose intent + RiskManager gate ----------------------------- #
    rule("RISK GATE  (deterministic RiskManager)")
    intent = OrderIntent(
        symbol=SYMBOL, side=side, order_type=OrderType.MARKET, volume=0.1,
        stop_loss=round(fired_sig.suggested_stop_loss, 5),
        take_profit=round(fired_sig.suggested_take_profit, 5),
        strategy_id=meta.name,
    )
    context = RiskContext(
        mode=TradingMode.PAPER, allow_live=False,
        allowlist=(SYMBOL, "XAUUSD"), account_balance=10_000.0,
        reference_price=entry_price, spread_points=10, volatility=None,
        realized_daily_loss=0.0, open_trades=(), trades_today=0,
        strategy_approved=bool(vreport.approved), strategy_applicability="direct",
    )
    decision = RiskManager().evaluate(intent, context, now=NOW)
    print(f"  approved : {decision.approved}")
    for reason in (decision.reasons or ["(clean — no rejections)"]):
        print(f"    - {reason}")

    if not decision.approved:
        print("\n  Risk gate REJECTED the intent (strategy not validated on this")
        print("  random-walk series). This is the safety model working as designed:")
        print("  an unapproved strategy can never reach execution.")
        if not vreport.approved:
            print("\n  --- Demonstrating the PAPER-TRADE LEG with an approved strategy ---")
            print("  (Re-running the risk gate with strategy_approved=True so the")
            print("   downstream paper-execution path is exercised. Validation itself")
            print("   is NOT bypassed — this only shows what happens once a strategy")
            print("   has earned approval.)")
            context = context.model_copy(update={"strategy_approved": True}) \
                if hasattr(context, "model_copy") else \
                RiskContext(**{**context.__dict__, "strategy_approved": True})
            decision = RiskManager().evaluate(intent, context, now=NOW)
            print(f"  approved : {decision.approved}")
            for reason in (decision.reasons or ["(clean — no rejections)"]):
                print(f"    - {reason}")
        if not decision.approved:
            return

    # -- 7. Paper trade + audit log --------------------------------------- #
    rule("PAPER TRADE  (PaperExecutionService + audit log)")
    tmp = Path(tempfile.mkdtemp(prefix="walk_strategy_"))
    trade_log = TradeLogStore(tmp / "trades.jsonl")
    audit_log = AuditLog(tmp / "audit.jsonl")
    service = PaperExecutionService(trade_log=trade_log, audit_log=audit_log)
    trade = service.execute(
        decision, context, timeframe=TIMEFRAME,
        strategy_name=meta.name, strategy_version=meta.version,
        features_snapshot=snapshot, kronos_prediction=None,
        reference_price=entry_price, now=NOW,
    )
    print(f"  trade id : {trade.trade_id}")
    print(f"  status   : {trade.status.value}")
    print(f"  side     : {trade.side.value}")
    print(f"  entry    : {trade.entry:.5f}")
    print(f"  SL / TP  : {trade.stop_loss:.5f} / {trade.take_profit:.5f}")
    print(f"  lot size : {trade.lot_size}")
    print(f"  persisted: {len(trade_log.records())} trade(s) -> {tmp / 'trades.jsonl'}")
    print(f"  audit    : {len(audit_log.events())} event(s) -> {tmp / 'audit.jsonl'}")
    print("\n  DONE — strategy walked from backtest to an open paper position.")


if __name__ == "__main__":
    main()
