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

## Project docs

- [`AGENTS.md`](AGENTS.md) — rules for AI coding agents
- [`SAFETY.md`](SAFETY.md) — hard safety rules
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design
- [`PLAN.md`](PLAN.md) — milestones
- [`TASKS.md`](TASKS.md) — task checklist
- [`RESOURCES.md`](RESOURCES.md) — external references
- [`docs/CODEBASE_MEMORY_MCP.md`](docs/CODEBASE_MEMORY_MCP.md) — codebase-memory
  MCP setup for source-code intelligence (optional dev tool)
- [`docs/STRATEGY_INVENTORY.md`](docs/STRATEGY_INVENTORY.md) — quant-trading
  strategy inventory JSON format and classification process
- [`docs/QUANTDINGER_SETUP.md`](docs/QUANTDINGER_SETUP.md) — QuantDinger client
  (**optional** external backtest platform) vs the local backtester

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
