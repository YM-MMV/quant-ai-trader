# TASKS.md — Task Checklist

Living checklist of work. Keep it small and current. Check items off as
milestones land (see `PLAN.md` for the full roadmap).

## M0 — Project governance

- [x] `AGENTS.md` — agent rules
- [x] `SAFETY.md` — hard safety rules
- [x] `ARCHITECTURE.md` — system design
- [x] `PLAN.md` — milestones
- [x] `TASKS.md` — this checklist
- [x] `RESOURCES.md` — external references
- [x] `README.md` — purpose + safety model
- [x] `.gitignore` — ignore `.env`, caches, build artifacts
- [x] `.env.example` — placeholders only (no real secrets)
- [x] `pyproject.toml` — minimal project so `pytest` runs
- [x] `pytest` runs (passes with no tests yet)

## M1 — Project skeleton & config foundation

- [x] Project structure (`apps/`, `services/`, `data/`, `strategies/`,
      `research/`, `external/`, `config/`)
- [x] `config/symbols.yaml` — symbols, broker aliases, allowlist
- [x] `config/risk.yaml` — deterministic risk limits
- [x] `config/timeframes.yaml` — supported timeframes
- [x] `config/app.yaml` — app-level non-secret defaults
- [x] Pydantic models (`services/models.py`): `Candle`, `MarketFeatures`,
      `KronosPrediction`, `StrategyMetadata`, `StrategySignal`, `OrderIntent`,
      `RiskDecision`, `TradeLog`, `BacktestResult`
- [x] `services/config_loader.py` — YAML loaders + env `Settings` (live lock)
- [x] `tests/test_models.py`, `tests/test_config_loader.py`
- [x] `pytest` passes
- No real services contacted (no MT5 / OpenBB / Kronos / QuantDinger).

## M2 — External repo resource setup

- [x] `config/external_repos.yaml` — repo manifest (6 repos + roles)
- [x] `services/resource_service/repo_manifest.py` — manifest loading +
      validation (https-only urls, no path traversal)
- [x] `services/resource_service/repo_manager.py` — clone/update command
      construction with an injectable runner (offline-testable)
- [x] `scripts/clone_external_repos.py` — CLI (`--list`, `--dry-run`,
      `--only`, `--depth`)
- [x] `tests/test_repo_manifest.py` — manifest + manager tests, git mocked
- [x] `pytest` passes offline (no network, no real git)
- No third-party code imported; repos cloned into `external/` (git-ignored).

## M3 — Codebase-memory MCP setup

- [x] `docs/CODEBASE_MEMORY_MCP.md` — setup + usage (source-code intelligence,
      NOT market data); what to index; useful safety-path queries
- [x] `scripts/index_codebase_memory.sh` — index helper (bash)
- [x] `scripts/index_codebase_memory.ps1` — index helper (PowerShell)
- [x] `README.md` links to the docs
- [x] `tests/test_codebase_memory_docs.py` — presence/content checks
- Binary is optional; not required for tests/CI. Realigned `PLAN.md` numbering.

## M4 — Candle storage (Parquet + DuckDB)

- [x] `services/data_service/sample_data.py` — deterministic fake candles
      (canonical 11-column schema; no MT5/OpenBB)
- [x] `services/data_service/storage.py` — `CandleStore`: validate required
      columns, sort ascending, save/load Parquet (`<base>/<symbol>/<tf>.parquet`)
- [x] `services/data_service/query.py` — `CandleQuery` over DuckParquet:
      `last_n`, `date_range` (inclusive, ascending)
- [x] `tests/test_candle_storage.py`, `tests/test_candle_query.py` (fake data)
- [x] `pytest` passes (save EURUSD M15; query last 500; query date range)
- Deps added: pandas, pyarrow, duckdb. Raw history never fed to the LLM.

## M5 — Feature engineering

- [x] `services/data_service/features.py` — causal features: simple/log
      returns, rolling volatility, ATR, RSI, MACD (+signal/hist), Bollinger
      Bands (+width), spread percentile, range expansion, trend score, HTF
      trend placeholder; `compute_features` assembles the full frame
- [x] `services/data_service/sessions.py` — Asia/London/New York/overlap/closed
      labels from UTC timestamp (causal)
- [x] `tests/test_features.py`, `tests/test_sessions.py`
- [x] No look-ahead: proven by prefix-stability AND future-mutation tests
- [x] NaN warmup handled; edge cases (flat/constant/short series) tested
- [x] `pytest` passes. Dep added: numpy.

## M6 — Strategy inventory scanner (current)

- [x] `services/strategy_service/strategy_classifier.py` — static name-based
      MT5-applicability knowledge base (`classify`); unknown → research_only
- [x] `services/strategy_service/inventory_scanner.py` — static scan of a
      quant-trading checkout → typed `InventoryItem`s (no import/exec of code)
- [x] `strategies/inventory/quant_trading_inventory.json` — curated baseline
      (17 items; "Ore Money Project" in KB, omitted from baseline)
- [x] `docs/STRATEGY_INVENTORY.md` — JSON schema + process documented; README links
- [x] `tests/test_strategy_classifier.py`, `tests/test_strategy_inventory_scanner.py`
- [x] Tests use fixtures only; verified scanner never executes/imports scripts
- [x] `pytest` passes

## Next up

- [ ] M7 — Strategy adapters
- [ ] M8 — Backtest service
