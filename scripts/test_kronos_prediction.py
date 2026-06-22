#!/usr/bin/env python3
"""Run a single Kronos prediction end-to-end and store it.

Loads recent candles for a symbol/timeframe (from the local Parquet store, or
deterministic sample data if none exist), runs the configured Kronos predictor,
prints the prediction, and saves it under ``data/predictions/``.

Kronos is optional: with ``--mode auto`` (default) it uses the real model if the
package is installed, otherwise it falls back to the deterministic mock. No GPU
is required to run the mock.

Usage:
    python scripts/test_kronos_prediction.py
    python scripts/test_kronos_prediction.py --symbol EURUSD --timeframe M15 \
        --lookback 400 --pred-len 12
    python scripts/test_kronos_prediction.py --mode mock
    python scripts/test_kronos_prediction.py --mode real      # needs the package

See docs/KRONOS_SETUP.md for installing the real model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the project importable when run as a plain script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.data_service.sample_data import generate_candles  # noqa: E402
from services.data_service.storage import CandleStore  # noqa: E402
from services.kronos_service.real_kronos import (  # noqa: E402
    KronosUnavailableError,
    load_kronos,
    save_prediction,
)

DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--lookback", type=int, default=400,
                        help="recent candles fed to the model (clamped to 512)")
    parser.add_argument("--pred-len", type=int, default=12,
                        help="bars ahead to forecast")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "real", "mock", "disabled"],
                        help="which predictor to use (default: auto)")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write the prediction to data/predictions/")
    return parser


def _load_candles(symbol: str, timeframe: str, lookback: int):
    """Recent candles from the local store, or sample data as a fallback."""
    store = CandleStore(DEFAULT_RAW_DIR)
    if store.exists(symbol, timeframe):
        df = store.load(symbol, timeframe)
        print(f"loaded {len(df)} candles from {store.path_for(symbol, timeframe)}")
        return df
    n = max(lookback + 16, 64)
    print(f"no stored candles for {symbol}/{timeframe}; using {n} sample candles")
    return generate_candles(symbol, timeframe, n=n)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        predictor = load_kronos(mode=args.mode)
    except KronosUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"using predictor: {type(predictor).__name__}")

    candles = _load_candles(args.symbol, args.timeframe, args.lookback)
    try:
        prediction = predictor.predict(
            candles, symbol=args.symbol, timeframe=args.timeframe,
            lookback=args.lookback, pred_len=args.pred_len,
        )
    except KronosUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(prediction.model_dump(), indent=2, default=str))

    if not args.no_save:
        path = save_prediction(prediction)
        print(f"saved prediction -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
