# quant-ai-trader

A **paper-first, risk-controlled quantitative trading AI** research system.

The system researches strategies, backtests them with realistic frictions,
validates them, paper trades them, and — only after deterministic risk checks —
can execute through MetaTrader 5. **Live trading is disabled by default.**

## Purpose

- Research and classify trading strategies (seeded from
  [je-suis-tm/quant-trading](https://github.com/je-suis-tm/quant-trading)).
- Backtest with spread, slippage, commission, and liquidity assumptions.
- Validate, then paper trade.
- Route **all** execution through a deterministic `RiskManager`.
- Eventually support MetaTrader 5 execution behind hard safety locks.

Target markets: **Forex, Gold/XAUUSD, Crypto**. Initial symbols: `EURUSD`,
`GBPUSD`, `XAUUSD`, and `BTCUSD` (if the broker provides it). Broker-specific
symbol names are mapped in `config/symbols.yaml`.

## Safety model

This project treats safety as a first-class, permanent constraint. See
[`SAFETY.md`](SAFETY.md) and [`AGENTS.md`](AGENTS.md) for the full rules. Summary:

- Default mode is **`paper`**; `live` is **disabled by default**
  (`ALLOW_LIVE_TRADING=false`).
- Allowed modes only: `research`, `backtest`, `paper`, `mt5_demo`, `live`.
  There is **no** `demo_live` mode.
- **No order without a stop loss and take profit.**
- No order if the symbol is not allowlisted, the spread exceeds the limit, the
  max daily loss is hit, or the max open-trades limit is reached.
- **No AI-generated trade bypasses `RiskManager`.** The AI agent proposes; it
  never executes.
- **No direct `mt5.order_send()`** outside the single locked MT5 gateway, and
  only when all live locks (`ALLOW_LIVE_TRADING=true`, `TRADING_MODE=live`,
  approved `RiskDecision`, allowlisted symbol) are satisfied.
- **No secrets in code.** Secrets live in `.env` (git-ignored);
  [`.env.example`](.env.example) holds placeholders only.

## Architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md). High-level flow:

```text
AI Agent → Market Data → Feature Engineering → Strategy Inventory/Adapters
  → Kronos Prediction → Backtest → RiskManager → Paper / MT5 Gateway → Logs
```

## Documentation

**Start here:** [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) — install,
configure `.env`, and run the tests.

Runbooks (task-focused, step-by-step):

- [`docs/RUNBOOK_PAPER_TRADING.md`](docs/RUNBOOK_PAPER_TRADING.md) — backtest and
  paper trade a strategy (services or the local API).
- [`docs/RUNBOOK_MT5_DEMO_ACCOUNT.md`](docs/RUNBOOK_MT5_DEMO_ACCOUNT.md) — connect
  an MT5 demo account, download candles, keep live disabled, and switch to a real
  account later.
- [`docs/RUNBOOK_STRATEGY_PORTING.md`](docs/RUNBOOK_STRATEGY_PORTING.md) — add a
  new strategy adapter.
- [`docs/RUNBOOK_RISK_MANAGER.md`](docs/RUNBOOK_RISK_MANAGER.md) — the risk gate,
  the kill switch, and reviewing rejected trades.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — common problems and fixes.

## Project docs

- [`AGENTS.md`](AGENTS.md) — rules for AI coding agents
- [`SAFETY.md`](SAFETY.md) — hard safety rules
- [`docs/FINAL_SAFETY_REVIEW.md`](docs/FINAL_SAFETY_REVIEW.md) — whole-codebase
  audit confirming no execution bypass exists (enforced by safety-guard tests)
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design
- [`PLAN.md`](PLAN.md) — milestones
- [`TASKS.md`](TASKS.md) — task checklist
- [`RESOURCES.md`](RESOURCES.md) — external references
- [`docs/CODEBASE_MEMORY_MCP.md`](docs/CODEBASE_MEMORY_MCP.md) — codebase-memory
  MCP setup for source-code intelligence (optional dev tool)
- [`docs/STRATEGY_INVENTORY.md`](docs/STRATEGY_INVENTORY.md) — quant-trading
  strategy inventory JSON format and classification process
- [`docs/OPENBB_SETUP.md`](docs/OPENBB_SETUP.md) — OpenBB research-data layer
  (**optional**, research-only; MT5 stays primary for execution-aligned data)
- [`docs/QUANTDINGER_SETUP.md`](docs/QUANTDINGER_SETUP.md) — QuantDinger client
  (**optional** external backtest platform) vs the local backtester
- [`docs/MT5_EXECUTION_SETUP.md`](docs/MT5_EXECUTION_SETUP.md) — real MT5
  execution gateway (**locked by default**; Windows-only; live locks)

## Backtesting: local engine vs QuantDinger

The project's own [`SimpleBacktester`](services/backtest_service/simple_backtester.py)
is the **primary, always-available** engine: in-process, no network, and
byte-for-byte reproducible with realistic friction. It powers the validation and
approval gate.

[QuantDinger](https://github.com/brokermr810/QuantDinger) is integrated as an
**optional** external platform behind a thin client
([`quantdinger_client.py`](services/backtest_service/quantdinger_client.py)). It
is never required: with no server configured (and in all tests) it defaults to a
deterministic in-memory mock. Use it only when you specifically want that
platform's engine — it is an alternative remote backtester, not a replacement.
See [`docs/QUANTDINGER_SETUP.md`](docs/QUANTDINGER_SETUP.md).

## Local API (FastAPI)

A local, **paper-only** HTTP API ([`apps/api/main.py`](apps/api/main.py)) exposes
market data, strategies, backtests, risk checks and paper trades. It is a thin
layer over the same services and the agent tool layer, so it inherits every
safety rule: **there is no live-execution endpoint**, and it exposes no secrets.

```bash
pip install -e ".[api]"
uvicorn apps.api.main:app --reload        # http://127.0.0.1:8000
# interactive docs at /docs ; liveness at /health
```

Endpoints: `GET /health`, `GET /symbols`, `GET /candles`, `GET /features`,
`GET /strategies/inventory`, `GET /strategies/adapters`, `POST /backtests/run`,
`POST /risk/check`, `POST /paper-trades`. The paper-trade route creates a trade
only after the deterministic `RiskManager` approves the intent.

## Getting started (development)

Requires Python 3.11.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Unix:                 source .venv/bin/activate

# 2. Install dev dependencies
pip install -e ".[dev]"

# 3. Copy environment placeholders (never commit your real .env)
cp .env.example .env

# 4. Run the tests
pytest
```

> No real credentials are required to run the test suite. Use mocks first.

## Full-system smoke test

[`tests/test_full_pipeline_smoke.py`](tests/test_full_pipeline_smoke.py) runs the
whole pipeline end to end on **fake data and mocks only** — fake candles →
features → mock Kronos prediction → all executable strategy adapters → signals →
backtest → strategy validation → `OrderIntent` → `RiskManager` → paper trade →
audit log. It touches **no MT5, no network, no external services, and no
secrets**, and is fully deterministic.

Run just the smoke test with:

```bash
pytest tests/test_full_pipeline_smoke.py -v
```

Use it as a quick confidence check that the services still wire together after a
change.
