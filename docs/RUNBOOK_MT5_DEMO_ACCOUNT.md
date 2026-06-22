# Runbook — MetaTrader 5 demo account

How to connect a MetaTrader 5 **demo** account, download candles, and (only if
you deliberately choose to) use the execution gateway. This runbook keeps you on
the safe path: **data download and paper trading require no live locks**, and
live execution stays disabled by default.

> ⚠️ The `MetaTrader5` Python package is **Windows-only** and talks to a locally
> installed, running MT5 terminal. The rest of the project runs anywhere because
> the package is mocked when absent. This runbook is the one part that needs a
> real terminal.

See also the deeper reference in
[`docs/MT5_EXECUTION_SETUP.md`](MT5_EXECUTION_SETUP.md).

---

## 1. Install MetaTrader 5 + a demo account

1. Install the MetaTrader 5 desktop terminal (Windows).
2. Open a **demo** account with your broker from inside the terminal
   (File → Open an Account). A demo account is fine for everything here — the
   code does not distinguish demo from real.
3. Log the terminal in and leave it running. The Python package attaches to this
   running terminal.

## 2. Install the MT5 extra

On the Windows machine, in your activated venv:

```bash
pip install -e ".[mt5]"      # installs MetaTrader5 (Windows only)
```

## 3. Configure credentials in `.env`

Most terminals auto-attach to the logged-in session, but you can pass explicit
credentials. These live in `.env` (git-ignored — never commit them):

```dotenv
MT5_LOGIN=12345678
MT5_PASSWORD=your-demo-password
MT5_SERVER=YourBroker-Demo
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

Keep the trading-mode defaults untouched while you only want data + paper:

```dotenv
TRADING_MODE=paper
ALLOW_LIVE_TRADING=false
```

## 4. Download candles

[`scripts/download_mt5_history.py`](../scripts/download_mt5_history.py) connects
to the running terminal, pulls historical rates for the canonical symbols, and
writes them to `data/raw/<symbol>/<timeframe>.parquet`. **It only reads data —
it never sends orders.**

```bash
# Defaults: EURUSD + XAUUSD, H1, last 30 days
python scripts/download_mt5_history.py

# Specific symbols + timeframe
python scripts/download_mt5_history.py --symbols EURUSD XAUUSD --timeframe H1

# A longer M15 window
python scripts/download_mt5_history.py --days 90 --timeframe M15

# An explicit date range
python scripts/download_mt5_history.py --start 2024-01-01 --end 2024-06-01
```

Options: `--symbols`, `--timeframe` (`M1 M5 M15 M30 H1 H4 D1`), `--days`,
`--start`/`--end` (`YYYY-MM-DD`), `--output-dir`. On success you'll see
`[ok] EURUSD: <n> candles -> data/raw/EURUSD/H1.parquet`; per-symbol failures are
reported and the script exits non-zero if any symbol failed.

Symbol names are canonical internally and mapped to broker aliases via
[`config/symbols.yaml`](../config/symbols.yaml) (e.g. `XAUUSD` → `GOLD`).

## 5. Backtest / paper trade on the downloaded candles

Load the Parquet and feed it to the backtester or paper executor exactly as in
the [Paper trading runbook](RUNBOOK_PAPER_TRADING.md). Nothing here sends an
order.

## 6. Keeping live trading disabled (the default)

Downloading candles and paper trading use **no** live locks. The real execution
gateway ([`mt5_gateway.py`](../services/execution_service/mt5_gateway.py)) is the
*only* place that can call `order_send`, and it refuses unless **every** lock is
satisfied:

| lock                       | source                                   |
|----------------------------|------------------------------------------|
| `TRADING_MODE=live`        | `.env` / `Settings.trading_mode`         |
| `ALLOW_LIVE_TRADING=true`  | `.env` / `Settings.allow_live_trading`   |
| `RiskDecision.approved`    | the deterministic `RiskManager`          |
| strategy approved          | the risk decision's `strategy_approved`  |
| symbol allowlisted         | `SYMBOL_ALLOWLIST` / `config/symbols.yaml` |
| stop loss present          | the `OrderIntent`                        |
| take profit present        | the `OrderIntent`                        |
| broker `order_check`       | MT5 margin / price validation            |

As long as `TRADING_MODE=paper` and `ALLOW_LIVE_TRADING=false`, the live path is
unreachable. There is **no `demo_live` mode** — the same `live` path serves a
terminal whether it is logged into a demo or a real account.

## 7. (Deliberate) using the gateway against the demo account

If you *want* the gateway to actually place orders on your **demo** terminal,
this is an intentional, two-switch action — not a default:

1. Confirm credentials are set (step 3) and your demo terminal is running.
2. Flip **both** switches (they are validated together — `live` with
   `ALLOW_LIVE_TRADING=false` is rejected at load):

   ```dotenv
   TRADING_MODE=live
   ALLOW_LIVE_TRADING=true
   ```

3. Send an order only with an **approved `RiskDecision` for that exact intent**:

   ```python
   from services.execution_service.mt5_gateway import MT5Gateway

   gw = MT5Gateway()
   gw.connect()
   result = gw.send_order(intent, decision)   # decision must be approved for THIS intent
   ```

Every gateway action and every refusal is logged; refusals raise
`LiveTradingDisabledError` **before** any broker call. Read
[`SAFETY.md`](../SAFETY.md) and [`MT5_EXECUTION_SETUP.md`](MT5_EXECUTION_SETUP.md)
first.

## 8. Switching to a real account later (manual, outside the code)

Because the code does not distinguish demo from real, moving to a live account is
a **manual** change *outside* the codebase:

1. Log the terminal into the real account.
2. Update the `MT5_*` credentials in `.env`.
3. Re-confirm your risk limits in [`config/risk.yaml`](../config/risk.yaml) and
   your `SYMBOL_ALLOWLIST`.
4. Only then flip the two live switches.

No code change is required to switch accounts — which is exactly why the two
deliberate switches, the per-intent approval, and the
[kill switch](RUNBOOK_RISK_MANAGER.md#the-kill-switch) exist.
