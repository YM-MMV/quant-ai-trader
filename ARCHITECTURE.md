# ARCHITECTURE.md — System Design

`quant-ai-trader` is a paper-first, risk-controlled quantitative trading research
system. It researches strategies, backtests them, validates them, paper trades
them, and — only after deterministic risk checks — can execute through
MetaTrader 5. Real/live execution is disabled by default.

## Core data flow

```text
AI Agent
  ↓
Market Data Tools
  ↓
Feature Engineering
  ↓
Strategy Inventory + Strategy Adapters
  ↓
Kronos Prediction Service
  ↓
Backtest Service
  ↓
RiskManager
  ↓
Paper Execution or MT5 Gateway
  ↓
Trade Logs + Audit Logs
```

## Component responsibilities

- **AI Agent** — Coordinator / research assistant. Proposes strategies and
  `OrderIntent` objects. **Never executes trades directly.**
- **Market Data Tools** — Fetch and normalize OHLCV and related data (mocked
  first, real feeds such as OpenBB later).
- **Feature Engineering** — Derive indicators/features without look-ahead bias.
- **Strategy Inventory + Adapters** — Scan and classify strategies (e.g. from
  the `quant-trading` repo), then expose feasible ones as clean internal
  adapters with explicit data/asset/timeframe contracts.
- **Kronos Prediction Service** — Optional candlestick foundation-model
  predictions feeding strategy/decision logic.
- **Backtest Service** — Evaluate strategies with realistic frictions: spread,
  slippage, commission, and liquidity assumptions. No frictionless backtests.
- **RiskManager** — The **only** path to execution. Validates every order against
  the hard rules in `SAFETY.md` and emits an approved/denied `RiskDecision`.
- **Paper Execution** — Default execution path; simulates fills.
- **MT5 Gateway** — The single locked component allowed to call broker
  execution. Acts only when all live locks are satisfied.
- **Trade Logs + Audit Logs** — Persistent, auditable record of intents,
  decisions, and fills.

## The deterministic core controls

Risk, position sizing, execution, logging, and kill switches are owned by
deterministic system components — **not** the AI agent.

## Modes

`research` · `backtest` · `paper` (default) · `mt5_demo` · `live` (disabled by
default). No `demo_live` mode. The MT5 account is currently a **demo** account
used via `mt5_demo`.

## Target markets & symbols

- Markets: Forex, Gold/XAUUSD, Crypto.
- Initial symbols: EURUSD, GBPUSD, XAUUSD, and BTCUSD (only if the broker
  provides it).
- Broker symbol names vary (e.g. `EURUSD`, `EURUSD.`, `EURUSDm`, `XAUUSD`,
  `GOLD`, `BTCUSD`, `BTCUSDm`), so mappings are configured in
  `config/symbols.yaml`.

## Safety boundaries

See `SAFETY.md` and `AGENTS.md`. Summary: no execution without SL/TP, allowlist,
spread/loss/open-trade limits; no AI bypass of `RiskManager`; no direct MT5
execution outside the locked gateway; live disabled by default.
