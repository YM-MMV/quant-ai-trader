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

> Numbering reflects the actual delivery order. Completed milestones are marked
> ✅; the rest are planned and may be refined as work proceeds.

- **M0 — Project governance** ✅
  Governance docs (`AGENTS.md`, `SAFETY.md`, `ARCHITECTURE.md`, `PLAN.md`,
  `TASKS.md`, `RESOURCES.md`, `README.md`), `.gitignore`, `.env.example`, and a
  minimal Python project so `pytest` runs.

- **M1 — Project skeleton & config foundation** ✅
  Clean directory structure (`apps/`, `services/`, `data/`, `strategies/`,
  `research/`, `external/`, `config/`), config files (`symbols.yaml`,
  `risk.yaml`, `timeframes.yaml`, `app.yaml`), typed Pydantic models, mode enum
  (`research`/`backtest`/`paper`/`mt5_demo`/`live`), and a config/`.env` loader
  with the live lock; tests.

- **M2 — External repo resource setup** ✅
  Manifest of third-party reference repos (`config/external_repos.yaml`),
  clone/update manager and CLI (`services/resource_service/`,
  `scripts/clone_external_repos.py`); offline-tested. No third-party code
  imported.

- **M3 — Codebase-memory MCP setup** ✅
  Docs and helper scripts for `codebase-memory-mcp` (source-code intelligence,
  not market data): `docs/CODEBASE_MEMORY_MCP.md`,
  `scripts/index_codebase_memory.{sh,ps1}`.

- **M4 — Market data tools (mocked)**
  Normalized OHLCV interface with a mock data source; tests.

- **M5 — Feature engineering**
  Indicator/feature library with explicit no-look-ahead guarantees; tests.

- **M6 — Strategy inventory & classification**
  Scan `external/quant-trading`, build a classification taxonomy,
  `research_only` / `not_applicable_to_mt5` tagging.

- **M7 — Strategy adapters**
  Convert feasible strategies into internal adapters with data/asset/timeframe
  contracts, signals, SL/TP, risk notes.

- **M8 — Backtest service**
  Realistic-friction backtester; per-strategy compatibility checks; tests.

- **M9 — RiskManager**
  Deterministic risk checks and `RiskDecision`; kill switch; SL/TP, spread,
  daily-loss, max-open-trades, allowlist enforcement; tests.

- **M10 — Paper execution**
  Simulated fills behind `RiskManager`; trade + audit logs; tests.

- **M11 — Kronos prediction service**
  Optional candlestick model integration (mocked first); tests.

- **M12 — MT5 gateway (demo)**
  Locked gateway, `mt5_demo` mode, live locks; mocked MT5 first; tests.

> `live` execution remains disabled by default and is only enabled by an
> explicit human action far later, per `SAFETY.md`.
