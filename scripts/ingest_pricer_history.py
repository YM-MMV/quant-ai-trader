"""Ingest the external pricer tick dump into the local candle store.

Resamples the raw per-day tick parquet files (``<SYMBOL>_YYYY_MM_DD.parquet``,
columns ``time``/``bid``/``ask`` + L2 depth) into the project's canonical OHLCV
candle schema and writes them to ``data/historical/<symbol>/<timeframe>.parquet``.
Run this once; afterwards the loop/backtest/validation read it via
``--source local`` (data only — never sends orders).

Examples
--------
    # XAUUSD across the timeframes the AI uses (default)
    python scripts/ingest_pricer_history.py --tick-dir pricer-output-2026-05-11_2026-06-10

    # A few symbols, specific timeframes
    python scripts/ingest_pricer_history.py --tick-dir pricer-output-2026-05-11_2026-06-10 \
        --symbols XAUUSD EURUSD --timeframes M5 M15 H1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.data_service.local_data import (  # noqa: E402
    DEFAULT_STORE_DIR,
    LocalDataError,
    ingest_symbol,
    symbol_tick_files,
)

DEFAULT_TIMEFRAMES = ["M5", "M15", "M30", "H1"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Resample pricer tick dumps into the local candle store.")
    p.add_argument("--tick-dir", required=True, help="folder of <SYMBOL>_YYYY_MM_DD.parquet tick files")
    p.add_argument("--symbols", nargs="+", default=["XAUUSD"], help="symbols to ingest")
    p.add_argument("--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES, help="candle timeframes to build")
    p.add_argument("--store-dir", default=str(DEFAULT_STORE_DIR), help="output candle store base dir")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    tick_dir = Path(args.tick_dir)
    if not tick_dir.is_dir():
        print(f"tick dir not found: {tick_dir}")
        return 2

    print("=" * 72)
    print(f" INGEST PRICER HISTORY  symbols={args.symbols}  timeframes={args.timeframes}")
    print(f" tick-dir={tick_dir}  ->  store={args.store_dir}")
    print("=" * 72)

    rc = 0
    for symbol in args.symbols:
        files = symbol_tick_files(tick_dir, symbol)
        if not files:
            print(f"\n  {symbol}: no tick files found — skipping")
            rc = 1
            continue
        print(f"\n  {symbol}: {len(files)} day files -> resampling {args.timeframes} ...")
        t0 = time.time()
        done = {"n": 0}

        def _progress(name: str) -> None:
            done["n"] += 1
            print(f"    [{done['n']}/{len(files)}] {name}", flush=True)

        try:
            written = ingest_symbol(
                symbol, args.timeframes, tick_dir=tick_dir,
                store_dir=args.store_dir, on_progress=_progress,
            )
        except LocalDataError as exc:
            print(f"  {symbol}: FAILED — {exc}")
            rc = 1
            continue
        dt = time.time() - t0
        print(f"  {symbol}: done in {dt:.0f}s")
        for tf, (path, n_bars) in written.items():
            print(f"      {tf}: {n_bars} bars -> {path}")
    print("\n" + "-" * 72)
    print("  ingest complete." if rc == 0 else "  ingest finished with warnings (see above).")
    print("-" * 72)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
