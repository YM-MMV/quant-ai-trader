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

## M2 — External repo resource setup (current)

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

## Next up

- [ ] M3 — Market data tools (mocked)
- [ ] M4 — Feature engineering
