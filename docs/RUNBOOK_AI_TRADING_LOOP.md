# Runbook — AI-driven, always-on trading loop (M29)

This is the loop that ties everything together: it pulls market data, asks an
**AI brain** to reason over many strategies and the live data, runs the
deterministic **risk assessment**, and routes the approved move to **paper**
(default) or your **MT5 demo** account — then keeps watching and managing the
position bar after bar.

> Safety spine (unchanged): **the AI proposes → the `RiskManager` decides → the
> gateway executes behind hard locks.** The AI has no live-execution tool. Paper
> is the default and needs no locks.

Entry point: [`scripts/ai_trade_loop.py`](../scripts/ai_trade_loop.py).

---

## What each piece does

| Stage | Where | Notes |
|-------|-------|-------|
| Pull live data | `services/data_service/mt5_data.py` (`--source mt5`) | Read-only; never sends orders. |
| AI reasons over strategies + data | `apps/agent/llm_runner.py` + `apps/agent/ai_decider.py` | Uses the 11 paper-safe tools (backtest / score / validate / propose / risk-check) to pick a move. |
| Risk assessment | `services/risk_service/risk_manager.py` | Re-run every tick; the only gate to execution. |
| Execute + manage | `DemoTrader` (paper) / `LiveTrader` (demo/live) | Opens with SL/TP, watches the position, closes/flips. |

The AI only emits an **actionable** signal after the chosen strategy passes the
deterministic `validate_strategy` gate — so "actionable ⇒ validated".

---

## 1. Install

```powershell
pip install -e ".[dev]"          # core + test deps
pip install -e ".[ai]"           # the AI brain (anthropic SDK) — skip if only using --mock-ai
pip install -e ".[mt5]"          # MetaTrader5 (Windows) — needed for real data / demo orders
```

## 2. Configure the brain

The model is configurable; it defaults to **Anthropic Claude Opus 4.8**. This repo
forbids a committed `.env`, so set the key via your shell env (same as the live
locks):

```powershell
$env:AI_API_KEY = '<your key>'           # or set ANTHROPIC_API_KEY
# optional overrides:
$env:AI_MODEL    = 'claude-opus-4-8'      # any model your key/endpoint serves
$env:AI_BASE_URL = ''                     # set for an Anthropic-compatible endpoint
```

## 3. Dry run (offline — no key, no terminal)

```powershell
python scripts/ai_trade_loop.py --once --source sample --mock-ai --symbol XAUUSD
# Demonstrate the full execution leg with a forced entry:
python scripts/ai_trade_loop.py --source sample --mock-ai --mock-action buy `
    --mock-strategy macd_oscillator --assume-approved --mode paper --max-ticks 3
```

## 4. Real data + real AI, paper routing (needs MT5 terminal running + AI_API_KEY)

```powershell
python scripts/ai_trade_loop.py --symbol XAUUSD --timeframe H1 --source mt5 --mode paper --max-ticks 3
```
The AI pulls real candles, backtests/validates candidates, proposes a move, and
the risk gate logs an approve/reject with reasons. No broker order is sent.

## 5. MT5 demo execution

`--mode demo` uses the **live gateway path** pointed at whatever account your
terminal is logged into (there is no separate `demo` mode). It is refused unless
**all three hard locks** are set — this script never flips them:

```powershell
$env:TRADING_MODE = 'live'
$env:ALLOW_LIVE_TRADING = 'true'
# AND add `live` to allowed_modes in config/risk.yaml (separate, second lock)
```

Also on the terminal:
- it must be **running and logged into your demo account**;
- **Algo Trading must be toggled ON** (`terminal_info().trade_allowed` is OFF by default);
- Gold is **`XAUUSD`** (3 digits) on this broker — confirm it is in Market Watch.

Then:
```powershell
python scripts/ai_trade_loop.py --symbol XAUUSD --timeframe H1 --source mt5 --mode demo --max-ticks 1
```
Confirm the order appears in the demo terminal with SL/TP attached, and that the
next tick reports the open position (and closes it on a `close` decision).

> ⚠️ Reconfirm the account is a **demo** before any `--mode demo`/`live` run. The
> same code path would trade a funded account if the terminal were logged into one.

---

## Key flags

| Flag | Meaning |
|------|---------|
| `--source sample\|mt5` | Offline deterministic candles vs real terminal data. |
| `--mode paper\|demo\|live` | Routing target. demo/live require the locks above. |
| `--once` / `--max-ticks N` | One decision / stop after N ticks (0 = run forever). |
| `--interval S` | Seconds between ticks (0 = align to the bar close for mt5). |
| `--risk-pct` / `--volume` | Risk-budget the lot size, or fix it. |
| `--mock-ai` (+ `--mock-action`, `--mock-strategy`) | Offline brain for tests/demos. |
| `--assume-approved` | Skip the AI's validation gate (DEMO ONLY; the risk gate still runs). |

## Stopping
`Ctrl-C` shuts the loop down cleanly. Open positions carry their broker-side
SL/TP, so they remain protected after the loop stops.

## Troubleshooting
- **"demo/live refused — locks not set"** → set the three locks in §5.
- **"the 'anthropic' package is required"** → `pip install -e ".[ai]"` or use `--mock-ai`.
- **"Invalid stops" / order rejected** → the broker minimum stop distance; the
  loop re-anchors SL/TP to the live quote and enforces `min_stop_distance`, but a
  very tight AI stop can still be widened — check the printed SL/TP.
- **No orders sent in demo** → confirm Algo Trading is ON in the terminal.
