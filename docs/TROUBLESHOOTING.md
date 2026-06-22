# Troubleshooting

Common problems and fixes, grouped by area. If you're just getting set up, start
with [Getting started](GETTING_STARTED.md).

---

## Install & environment

**`pip install -e ".[dev]"` fails on Python version.**
The project requires **Python 3.11+**. Check `python --version`. Recreate the
venv with a 3.11 interpreter if needed.

**`ModuleNotFoundError: No module named 'services'` when running a script.**
Either install the project editable (`pip install -e .`) or run from the project
root. The operational scripts (e.g. `scripts/download_mt5_history.py`) add the
project root to `sys.path` themselves; `pytest` is configured with
`pythonpath = ["."]` so tests import `services`/`apps` without an install.

**FastAPI / httpx import errors.**
The API and its tests need the extras: `pip install -e ".[dev]"` (test client) or
`pip install -e ".[api]"` (server). `tests/test_api.py` skips itself when these
are absent rather than failing.

**`ImportError` for `MetaTrader5`, `openbb`, `requests`.**
These are **optional** extras imported lazily. Install the one you need
(`pip install -e ".[mt5]"`, `[openbb]`, `[quantdinger]`, `[worldmonitor]`). The
core system, tests, and paper pipeline run without any of them.

## Configuration & `.env`

**`ValueError: TRADING_MODE=live requires ALLOW_LIVE_TRADING=true`.**
This is the safety lock working as designed — the two switches are validated
together so live can't be half-enabled. For normal use keep
`TRADING_MODE=paper`. To intentionally enable live, set **both**
`TRADING_MODE=live` and `ALLOW_LIVE_TRADING=true` and read
[`SAFETY.md`](../SAFETY.md) first.

**My `.env` changes aren't taking effect.**
Settings are cached (`get_settings()` is `lru_cache`d). Restart the process after
editing `.env`. Confirm you edited `.env` (git-ignored), not `.env.example`.

**Config fails to load with a validation error.**
The YAML in `config/` is validated by Pydantic and fails fast on a typo or bad
value. Read the error — it names the field. `extra="forbid"` means an unknown key
is rejected; remove or correct it. The `allowlist` in `symbols.yaml` must only
reference symbols defined in the same file.

**A symbol isn't recognised / maps to the wrong broker name.**
Canonical symbols and their broker aliases live in
[`config/symbols.yaml`](../config/symbols.yaml). `BTCUSD` is `enabled: false` by
default — only enable it if your broker actually provides it.

## MetaTrader 5

**`error: could not connect to MT5`.**
- You're not on **Windows** (the `MetaTrader5` package is Windows-only).
- The MT5 **terminal isn't running** / isn't logged in. Open it and log into your
  demo account first.
- Credentials in `.env` are wrong or `MT5_TERMINAL_PATH` points at the wrong
  `terminal64.exe`.
See the [MT5 demo account runbook](RUNBOOK_MT5_DEMO_ACCOUNT.md).

**Download script reports `[FAILED] <symbol>`.**
The symbol may not exist on your broker under any mapped alias, or it isn't
visible in Market Watch. Check `config/symbols.yaml` aliases and add the symbol to
Market Watch in the terminal. The script still writes the symbols that succeeded
and exits non-zero if any failed.

**I want to download data but I'm worried about sending orders.**
The download script (`scripts/download_mt5_history.py`) is **data only** — it
never calls `order_send`. Order placement lives exclusively in
`services/execution_service/mt5_gateway.py`, which is locked off by default.

## Backtests & strategies

**A backtest produces no trades.**
The strategy may be abstaining (`NONE`) every bar — too little history
(`min_candles`), an unsupported symbol/timeframe, or conditions never triggering
on that data. Adapters fail safe to `NONE` on insufficient/bad input by design.

**My new adapter never fires / always abstains.**
- Check `get_metadata()`: `supported_symbols`, `supported_timeframes`, and
  `min_candles` must match your data.
- Confirm the candle DataFrame has the required OHLC columns.
- Remember actionable signals **must** include a non-empty `reason`, or the model
  rejects them. See the
  [Strategy porting runbook](RUNBOOK_STRATEGY_PORTING.md).

**`KeyError: adapter '<name>' is already registered`.**
Adapter names must be unique in the registry. Rename it, or register with
`replace=True` if you intend to override.

**Strategy won't execute even on paper.**
It probably isn't approved. Run it through the
[`strategy_validator`](../services/backtest_service/strategy_validator.py); only
strategies passing `config/strategy_validation.yaml` clear the risk gate's
`strategy_approved` / `not_research_only` checks.

## Risk gate & rejected trades

**Every order is rejected.**
The `RiskManager` lists **all** reasons at once (no short-circuit) — read
`decision.reasons` and the failing keys in `decision.checks`. Common causes:
missing SL/TP, symbol not allowlisted, spread above `max_spread_points`, mode not
in `allowed_modes`, per-trade risk over `max_risk_per_trade_pct`, or the **kill
switch** being active.

**"kill switch is active" on every trade.**
`kill_switch_active=True` is set on the `RiskContext` (and
`kill_switch_enabled: true` in config). This is the intended halt — clear the
flag to resume. See
[Risk manager runbook §The kill switch](RUNBOOK_RISK_MANAGER.md#the-kill-switch).

**"risk per trade … exceeds limit" or "no contract spec".**
Position size × stop distance × contract size exceeds `max_risk_per_trade_pct` of
`account_balance`; reduce volume, widen the account balance in context, or check
the symbol has a spec in
[`symbol_specs.py`](../services/risk_service/symbol_specs.py).

**How do I review what was rejected and why?**
Read `data/paper_trades/trades.jsonl` (via `TradeLogStore().rejected()`) and the
`trade_rejected` events in `data/paper_trades/audit.jsonl`. See
[Risk manager runbook §Reviewing rejected trades](RUNBOOK_RISK_MANAGER.md#reviewing-rejected-trades).

## API

**`uvicorn apps.api.main:app` won't start.**
Install the API extra (`pip install -e ".[api]"`) and run from the project root.
Liveness is at `GET /health`; interactive docs at `/docs`.

**There's no live-trading endpoint.**
By design. The API is **paper-only** and `POST /paper-trades` re-runs the
`RiskManager` itself — you can't inject a pre-approved decision to bypass the
gate. See the [Paper trading runbook](RUNBOOK_PAPER_TRADING.md#option-b-the-local-api).

## Tests

**`pytest` collects nothing / can't import.**
Run from the project root. `pyproject.toml` sets `testpaths = ["tests"]` and
`pythonpath = ["."]`.

**A test that touches MT5 / OpenBB / a network service is failing.**
The suite mocks all external services and needs **no** real credentials. A
failure there usually means a logic regression, not a missing secret — read the
assertion. The [smoke test](../tests/test_full_pipeline_smoke.py) is the fastest
way to confirm the services still wire together:

```bash
pytest tests/test_full_pipeline_smoke.py -v
```

## Still stuck?

- [`SAFETY.md`](../SAFETY.md) — the hard safety rules and why they exist.
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) — how the pieces fit together.
- The per-integration setup docs: [Kronos](KRONOS_SETUP.md),
  [OpenBB](OPENBB_SETUP.md), [QuantDinger](QUANTDINGER_SETUP.md),
  [MT5 execution](MT5_EXECUTION_SETUP.md), [WorldMonitor](WORLDMONITOR_SETUP.md).
