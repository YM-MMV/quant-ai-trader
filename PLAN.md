# PLAN.md — Build Plan & Milestones

Paper-first, risk-controlled. Each milestone must add tests and pass `pytest`
before being considered done. Prefer small, reviewable commits.

## Guiding principles

- Paper trading first; `live` disabled by default.
- Mocks before real integrations.
- All execution flows through `RiskManager`.
- Realistic backtests only (spread, slippage, commission, liquidity).
- No secrets in code; `.env` never committed.

## Milestones

- **M0 — Project governance** *(current)*
  Create governance docs (`AGENTS.md`, `SAFETY.md`, `ARCHITECTURE.md`,
  `PLAN.md`, `TASKS.md`, `RESOURCES.md`, `README.md`), `.gitignore`,
  `.env.example`, and a minimal Python project so `pytest` runs.

- **M1 — Config & modes**
  Mode enum (`research`/`backtest`/`paper`/`mt5_demo`/`live`), settings loader
  from `.env`, `config/symbols.yaml` broker symbol mapping, allowlist.

- **M2 — Market data tools (mocked)**
  Normalized OHLCV interface with a mock data source; tests.

- **M3 — Feature engineering**
  Indicator/feature library with explicit no-look-ahead guarantees; tests.

- **M4 — Strategy inventory & classification**
  Vendor `quant-trading` under `external/`, scanner, classification taxonomy,
  `research_only` / `not_applicable_to_mt5` tagging.

- **M5 — Strategy adapters**
  Convert feasible strategies into internal adapters with data/asset/timeframe
  contracts, signals, SL/TP, risk notes.

- **M6 — Backtest service**
  Realistic-friction backtester; per-strategy compatibility checks; tests.

- **M7 — RiskManager**
  Deterministic risk checks and `RiskDecision`; kill switch; SL/TP, spread,
  daily-loss, max-open-trades, allowlist enforcement; tests.

- **M8 — Paper execution**
  Simulated fills behind `RiskManager`; trade + audit logs; tests.

- **M9 — Kronos prediction service**
  Optional candlestick model integration (mocked first); tests.

- **M10 — MT5 gateway (demo)**
  Locked gateway, `mt5_demo` mode, live locks; mocked MT5 first; tests.

> `live` execution remains disabled by default and is only enabled by an
> explicit human action far later, per `SAFETY.md`.
