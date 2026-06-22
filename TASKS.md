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

## M6 — Strategy inventory scanner

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

## M7 — Strategy adapter framework (current)

- [x] `services/strategy_service/base.py` — `StrategyAdapter` ABC (`get_metadata`,
      `validate_inputs`, `supports_symbol`/`supports_timeframe`, template
      `generate_signal`); `AdapterSignal` (side BUY/SELL/NONE, confidence,
      reason, suggested SL/TP, risk notes, source strategy/repo, adapter version);
      `AdapterMetadata`
- [x] `services/strategy_service/registry.py` — `StrategyRegistry` (register/
      get/find/unregister), process-wide `default_registry`, `@register_adapter`
- [x] `services/strategy_service/adapters/` — package + `NullAdapter` (empty
      reference adapter that always abstains)
- [x] `tests/test_strategy_base.py`, `tests/test_strategy_registry.py`
- [x] Fail-safe: insufficient data / adapter error → `NONE`; deterministic;
      no trading, no MT5, no AI in strategies
- [x] Empty adapters register & run; `pytest` passes

## M8 — Technical-indicator strategy adapters (port from quant-trading)

Clean re-implementations (no original code imported/executed; no MT5; no live
trading) behind the M7 `StrategyAdapter` interface, each returning BUY/SELL/NONE.

- [x] `services/strategy_service/adapters/_common.py` — shared helpers: pip/point
      sizing, spread+slippage cost awareness, ATR-based SL/TP suggestions
- [x] `macd_oscillator.py` — MACD/signal-line crossover
- [x] `heikin_ashi.py` — Heikin-Ashi candle colour flip
- [x] `london_breakout.py` — Asia-range breakout at the London open (session-aware)
- [x] `awesome_oscillator.py` — AO zero-line crossover
- [x] `dual_thrust.py` — volatility-range breakout (k1/k2 triggers)
- [x] `parabolic_sar.py` — Wilder stop-and-reverse flip (SAR as trailing stop)
- [x] `bollinger_bands_pattern.py` — band re-entry mean-reversion
- [x] `rsi_pattern.py` — RSI exit from overbought/oversold extreme
- [x] `shooting_star.py` — shooting-star / hammer single-candle reversal
- [x] Each adapter: min data length, supported timeframes, asset classes
      (forex/metal/crypto), spread/slippage awareness, suggested SL/TP
- [x] `register_technical_indicator_adapters()` wires all nine into a registry
      (import stays side-effect free)
- [x] `tests/test_adapter_*.py` + `tests/test_adapters_registration.py` — sample
      candles force BUY/SELL/NONE per adapter; metadata + fail-safe contract
- [x] `pytest` passes (293 tests)

## M9 — Research adapters (pair/stat-arb, options, VIX, portfolio, quantamental)

Represent and classify the quant-trading projects that are **not** directly
tradable on MT5, instead of dropping them. Structurally non-executable.

- [x] `services/strategy_service/research_adapters/base.py` — `ResearchAdapter`
      ABC, `ResearchOutput` (output type signal/feature/report/ranking/
      risk_context + `MT5Applicability` + required datasets + reason), fail-safe
      `run()`; only a `DIRECT` signal is executable, enforced by
      `to_strategy_signal`/`ensure_not_executed` → `NotExecutableError`
- [x] `pair_trading.py` — cointegration-screened spread mean-reversion (adaptable)
- [x] `options_straddle.py` — straddle break-evens (not_applicable on spot MT5)
- [x] `vix_calculator.py` — realized-vol proxy feature (research_only)
- [x] `monte_carlo_project.py` — seeded GBM VaR / scenarios (research_only)
- [x] `oil_money_project.py`, `ore_money_project.py` — commodity-currency
      regressions (research_only)
- [x] `smart_farmers_project.py` — agri-commodity momentum (research_only)
- [x] `portfolio_optimization.py` — inverse-variance capital allocation ranking
      (research_only; sizing aid, not a trade signal)
- [x] `wisdom_of_crowd.py` — consensus forecast ranking (research_only)
- [x] Each adapter declares required datasets, applicability, output type, and
      why it is/ isn't tradable on MT5
- [x] `tests/test_research_base.py`, `test_research_adapters.py`,
      `test_research_functional.py` — execution barrier proven; every
      non-technical inventory project represented; applicability matches M6
- [x] No MT5 / network / execution; deterministic; `pytest` passes (272 tests)

## M10 — Simple backtesting engine

Local, deterministic, single-symbol backtester (before QuantDinger). No MT5, no
AI; every fill priced with friction.

- [x] `services/backtest_service/costs.py` — `CostModel` (spread split per side,
      slippage in points, commission placeholder); pip/point sizing
- [x] `services/backtest_service/metrics.py` — `compute_metrics`: total trades,
      win rate, average R, profit factor, max drawdown (abs + %), Sharpe
      placeholder, expectancy, largest-winner contribution, consecutive losses,
      average holding bars
- [x] `services/backtest_service/simple_backtester.py` — `SimpleBacktester`:
      BUY/SELL/NONE, next-bar-open entry (no look-ahead), SL/TP intrabar,
      max holding period, end-of-day/session close, equity curve + trade list;
      **stop loss mandatory — signals without one are rejected**
- [x] `tests/test_simple_backtester.py`, `tests/test_backtest_metrics.py`
- [x] Backtests fake candles; applies costs (frictionless round-trip = break-even,
      with friction < 0); no-stop signals rejected; deterministic; `pytest`
      passes (248 tests)

## M11 — Strategy validation & approval gate

Strict, deterministic gates a strategy must clear before paper trading. Required
gates block approval; sensitivity gates block only when stressed evidence is
supplied; parameter-sensitivity and walk-forward are explicit placeholders.

- [x] `config/strategy_validation.yaml` — thresholds (min trades, profit factor,
      max drawdown %, max largest-trade contribution %, min expectancy, require
      stop/TP, require out-of-sample positive, stress knobs)
- [x] `services/backtest_service/strategy_validator.py` — `StrategyValidator`
      over a `ValidationInput`; gates: minimum trades, OOS-positive, max
      drawdown, profit factor, expectancy, largest-trade contribution, no
      missing SL, no missing TP, slippage/spread sensitivity, parameter &
      walk-forward placeholders, no look-ahead flags. Emits a `ValidationReport`
      and, only when approved, an `ApprovalRecord`. `build_validation_input`
      glues in the M10 backtester (in/out-of-sample + stress runs)
- [x] `tests/test_strategy_validator.py` — strong strategy approved w/ record;
      each required gate rejects a weak strategy; sensitivity blocking/robust/
      not-evaluated; placeholders non-blocking; config loads from YAML;
      deterministic; end-to-end via the backtester
- [x] No MT5 / network / AI; `pytest` passes (385 tests)

## Next up

- [ ] QuantDinger integration
- [ ] RiskManager
