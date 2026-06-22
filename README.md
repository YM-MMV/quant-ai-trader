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
