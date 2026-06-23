"""Validate one strategy adapter against the approval gates — honestly.

Runs a real adapter through in-sample / out-of-sample / stress backtests using
the project's own engine, prints the full ValidationReport (every gate, observed
vs threshold), and exits non-zero if the strategy is **not approved**. This is the
gate a strategy must clear before the RiskManager will let it trade.

Examples
--------
    # List the adapters you can validate
    python scripts/validate_strategy.py --list

    # Validate MACD on generated H1 candles (no terminal needed)
    python scripts/validate_strategy.py --adapter macd_oscillator --timeframe H1

    # Validate on real downloaded demo candles, and require a Sharpe >= 0.5
    python scripts/validate_strategy.py --adapter rsi_pattern \
        --parquet data/raw/EURUSD/H1.parquet --min-sharpe 0.5

No MT5, no network, no orders — this only reads candles and computes metrics.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

from services.backtest_service.adapter_bridge import adapter_to_backtest_strategy
from services.backtest_service.strategy_validator import (
    StrategyValidator,
    build_validation_input,
    load_validation_config,
)
from services.data_service.sample_data import generate_candles
from services.strategy_service.adapters import register_technical_indicator_adapters
from services.strategy_service.registry import StrategyRegistry


def build_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_technical_indicator_adapters(registry)
    return registry


def load_candles(args) -> pd.DataFrame:
    if args.parquet:
        candles = pd.read_parquet(args.parquet)
        if "timestamp" in candles.columns:
            candles = candles.sort_values("timestamp").reset_index(drop=True)
        return candles
    return generate_candles(
        symbol=args.symbol, timeframe=args.timeframe, n=args.n, seed=args.seed
    )


def fmt(value, spec="+.5f") -> str:
    return "n/a" if value is None else format(value, spec)


def print_metrics(title: str, m) -> None:
    if m is None:
        print(f"  {title}: (not available)")
        return
    print(f"  {title}:")
    print(f"    trades={m.total_trades}  win_rate={m.win_rate:.1%}  "
          f"net_profit={fmt(m.net_profit)}")
    print(f"    profit_factor={fmt(m.profit_factor, '.2f')}  "
          f"expectancy={fmt(m.expectancy)}  "
          f"max_dd%={m.max_drawdown_pct * 100:.2f}")
    print(f"    sharpe(annualised)={fmt(m.sharpe_ratio, '.3f')}  "
          f"sharpe(per-bar)={fmt(m.sharpe_placeholder, '.4f')}")


def main(argv=None) -> int:
    registry = build_registry()
    parser = argparse.ArgumentParser(description="Validate one strategy adapter.")
    parser.add_argument("--adapter", help="adapter name (see --list)")
    parser.add_argument("--list", action="store_true", help="list adapters and exit")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--parquet", help="path to a candles parquet (else generated)")
    parser.add_argument("--n", type=int, default=1500, help="generated candle count")
    parser.add_argument("--seed", type=int, default=7, help="generated candle seed")
    parser.add_argument("--split", type=float, default=0.7,
                        help="in-sample fraction (rest is out-of-sample)")
    parser.add_argument("--min-sharpe", type=float, default=None,
                        help="override the minimum annualised Sharpe gate")
    args = parser.parse_args(argv)

    if args.list or not args.adapter:
        print("Available adapters:")
        for name in registry.names():
            print(f"  - {name}")
        return 0 if args.list else 2

    if args.adapter not in registry:
        print(f"unknown adapter {args.adapter!r}; known: {registry.names()}")
        return 2
    if not (0.3 <= args.split <= 0.9):
        print("--split should be between 0.3 and 0.9")
        return 2

    adapter = registry.get(args.adapter)
    strategy = adapter_to_backtest_strategy(adapter)

    candles = load_candles(args)
    cut = int(len(candles) * args.split)
    candles_in = candles.iloc[:cut].reset_index(drop=True)
    candles_out = candles.iloc[cut:].reset_index(drop=True)

    cfg = load_validation_config()
    if args.min_sharpe is not None:
        cfg = cfg.model_copy(update={"minimum_sharpe": args.min_sharpe})

    print("=" * 72)
    print(f" VALIDATING: {adapter.name} v{adapter.version}")
    print(f" candles: {len(candles)} {args.timeframe} "
          f"({'parquet' if args.parquet else 'generated'})  "
          f"in-sample={len(candles_in)}  out-of-sample={len(candles_out)}")
    print("=" * 72)

    vin = build_validation_input(
        candles_in, strategy, candles_out=candles_out, validation_config=cfg
    )
    report = StrategyValidator(cfg).validate(
        vin, strategy_id=adapter.name, now=datetime.now()
    )

    print_metrics("in-sample", vin.in_sample)
    print_metrics("out-of-sample", vin.out_of_sample)

    print("\n  Gates:")
    mark = {"pass": "PASS", "fail": "FAIL", "not_evaluated": "n/a "}
    for r in report.results:
        block = "required" if r.blocking else "advisory"
        detail = r.detail.replace("—", "-")
        print(f"    [{mark[r.status.value]}] {r.name:<34} ({block})  {detail}")

    print("\n" + "-" * 72)
    print(f"  VERDICT: {report.summary}")
    if not report.approved:
        print("  -> The RiskManager will REJECT this strategy until it is approved.")
        print("     Levers: more/longer candles, tune adapter params, or trade only")
        print("     higher-quality sessions. See docs/RUNBOOK_PAPER_TRADING.md.")
    print("-" * 72)
    return 0 if report.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
