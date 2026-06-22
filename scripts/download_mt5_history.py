#!/usr/bin/env python3
"""Download historical candles from a MetaTrader 5 demo terminal to Parquet.

**Windows only, data only.** This connects to a locally running MT5 terminal,
pulls historical rates for one or more canonical symbols, and writes them to
``data/raw/<symbol>/<timeframe>.parquet`` in the project's canonical schema. It
never sends orders.

Usage (run locally on Windows with MT5 installed and a demo account logged in):

    python scripts/download_mt5_history.py
    python scripts/download_mt5_history.py --symbols EURUSD XAUUSD --timeframe H1
    python scripts/download_mt5_history.py --days 90 --timeframe M15
    python scripts/download_mt5_history.py --start 2024-01-01 --end 2024-06-01

Credentials (if your terminal needs an explicit login) come from ``.env``:
``MT5_LOGIN``, ``MT5_PASSWORD``, ``MT5_SERVER``, ``MT5_TERMINAL_PATH``.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the project importable when run as a plain script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.data_service import mt5_data  # noqa: E402

DEFAULT_SYMBOLS = ["EURUSD", "XAUUSD"]


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS, metavar="SYMBOL",
        help=f"canonical symbols to download (default: {' '.join(DEFAULT_SYMBOLS)})",
    )
    parser.add_argument(
        "--timeframe", default="H1",
        help="timeframe, e.g. M1 M5 M15 M30 H1 H4 D1 (default: H1)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="how many days back from now to download (default: 30)",
    )
    parser.add_argument("--start", type=_parse_date, default=None,
                        help="explicit start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", type=_parse_date, default=None,
                        help="explicit end date YYYY-MM-DD (default: now)")
    parser.add_argument("--output-dir", default=None,
                        help="base output dir (default: data/raw)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    end = args.end or datetime.now(timezone.utc)
    start = args.start or (end - timedelta(days=args.days))
    if end < start:
        print("error: end must be >= start", file=sys.stderr)
        return 2

    try:
        mt5_data.connect_to_mt5()
    except mt5_data.MT5Error as exc:
        print(f"error: could not connect to MT5: {exc}", file=sys.stderr)
        return 1

    print(f"connected to MT5; downloading {args.timeframe} "
          f"{start:%Y-%m-%d} .. {end:%Y-%m-%d}")
    failures = 0
    try:
        available = mt5_data.get_symbols()
        for symbol in args.symbols:
            try:
                df = mt5_data.get_rates(
                    symbol, args.timeframe, start, end, available=available
                )
                path = mt5_data.save_rates_to_parquet(
                    df, base_dir=args.output_dir, symbol=symbol,
                    timeframe=args.timeframe,
                )
                print(f"[ok] {symbol}: {len(df)} candles -> {path}")
            except mt5_data.MT5Error as exc:
                failures += 1
                print(f"[FAILED] {symbol}: {exc}", file=sys.stderr)
    finally:
        mt5_data.shutdown_mt5()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
