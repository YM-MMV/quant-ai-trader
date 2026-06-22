# Getting started

This guide takes you from a fresh clone to a working, **paper-only** install with
the test suite passing. It is the entry point for the runbooks:

- [Paper trading](RUNBOOK_PAPER_TRADING.md)
- [MT5 demo account](RUNBOOK_MT5_DEMO_ACCOUNT.md)
- [Porting a strategy](RUNBOOK_STRATEGY_PORTING.md)
- [Risk manager](RUNBOOK_RISK_MANAGER.md)
- [Troubleshooting](TROUBLESHOOTING.md)

> **Safety first.** This project is paper-first. Live trading is **disabled by
> default** and stays off until a human deliberately flips two switches. Read
> [`SAFETY.md`](../SAFETY.md) before doing anything beyond paper. Nothing in this
> guide sends a real order.

## 1. Prerequisites

- **Python 3.11+** (the project targets 3.11; 3.12 also works).
- **git**.
- **Windows** is only required for the optional MetaTrader 5 data/execution
  pieces. Everything else (tests, backtests, paper trading, the API) runs on any
  OS, because MT5 is mocked when the package is absent.

Check your Python:

```bash
python --version      # 3.11.x or newer
```

## 2. Clone and create a virtual environment

```bash
git clone <your-fork-url> quant-ai-trader
cd quant-ai-trader

python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
```

## 3. Install dependencies

The core install is enough to run backtests and paper trade. Optional extras add
the API, MT5, and external data providers — install only what you need.

```bash
# Core + dev tools (pytest, FastAPI test client). Recommended first install:
pip install -e ".[dev]"
```

| Extra            | Install                          | Adds                                            |
|------------------|----------------------------------|-------------------------------------------------|
| (core)           | `pip install -e .`               | pydantic, pandas, numpy, duckdb — backtests, paper trading |
| `dev`            | `pip install -e ".[dev]"`        | pytest + FastAPI/httpx for the test suite        |
| `api`            | `pip install -e ".[api]"`        | FastAPI + uvicorn for the local paper-only API   |
| `mt5`            | `pip install -e ".[mt5]"`        | `MetaTrader5` (Windows only) for data + execution|
| `openbb`         | `pip install -e ".[openbb]"`     | OpenBB research data (optional, research-only)   |
| `quantdinger`    | `pip install -e ".[quantdinger]"`| QuantDinger external backtest client (optional)  |
| `worldmonitor`   | `pip install -e ".[worldmonitor]"`| WorldMonitor macro/news *context* (optional, off by default) |

You can combine extras: `pip install -e ".[dev,api]"`.

## 4. Configure `.env`

Secrets and per-machine toggles live in `.env`, which is **git-ignored**. Copy
the placeholder file and edit it locally:

```bash
cp .env.example .env
```

[`.env.example`](../.env.example) is the source of truth for the keys. The
defaults are paper-safe:

```dotenv
# Allowed modes: research | backtest | paper | mt5_demo | live
TRADING_MODE=paper
ALLOW_LIVE_TRADING=false          # live stays off; do not change without reading SAFETY.md

# Risk limits (the .env values are per-machine overrides; the committed
# defaults live in config/risk.yaml)
MAX_DAILY_LOSS=0.0
MAX_OPEN_TRADES=0
MAX_SPREAD_POINTS=0

# Symbols you allow (comma-separated; mapped via config/symbols.yaml)
SYMBOL_ALLOWLIST=EURUSD,GBPUSD,XAUUSD

# MetaTrader 5 — leave blank until you configure a demo account locally
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=

# OpenBB / data providers (optional)
OPENBB_PAT=
```

Key rules:

- **Never commit your real `.env`.** Only `.env.example` (placeholders) is
  tracked.
- `TRADING_MODE=live` is *rejected at load time* unless `ALLOW_LIVE_TRADING=true`
  — the safety lock cannot be half-enabled by accident.
- The non-secret defaults (risk limits, symbol specs, timeframes) are
  version-controlled YAML in [`config/`](../config): `risk.yaml`, `symbols.yaml`,
  `timeframes.yaml`, `app.yaml`.

## 5. Verify the install — run the tests

No real credentials are required. The suite uses mocks and never touches a
broker or the network.

```bash
pytest
```

For a fast end-to-end confidence check that all the services still wire
together, run just the full-pipeline smoke test (fake data + mocks only):

```bash
pytest tests/test_full_pipeline_smoke.py -v
```

## 6. Where to go next

| You want to…                                   | Go to                                                |
|------------------------------------------------|------------------------------------------------------|
| Download real candles from MT5                 | [MT5 demo account runbook](RUNBOOK_MT5_DEMO_ACCOUNT.md) |
| Backtest and paper trade a strategy            | [Paper trading runbook](RUNBOOK_PAPER_TRADING.md)    |
| Add a new strategy adapter                     | [Strategy porting runbook](RUNBOOK_STRATEGY_PORTING.md) |
| Understand / tune the risk gate and kill switch| [Risk manager runbook](RUNBOOK_RISK_MANAGER.md)      |
| Run the local HTTP API                         | [Paper trading runbook §API](RUNBOOK_PAPER_TRADING.md#option-b-the-local-api) |
| Fix something that's broken                    | [Troubleshooting](TROUBLESHOOTING.md)                |

## Project map

| Path                         | What it holds                                            |
|------------------------------|----------------------------------------------------------|
| `services/data_service/`     | MT5 download, storage (Parquet), feature engineering     |
| `services/strategy_service/` | Strategy adapters, registry, classifier                  |
| `services/backtest_service/` | `SimpleBacktester`, costs, metrics, validator            |
| `services/risk_service/`     | `RiskManager`, position sizing, symbol specs             |
| `services/execution_service/`| Paper executor, trade/audit logs, MT5 gateways           |
| `services/monitoring_service/`| Performance monitor, degradation detection              |
| `apps/api/`                  | Local FastAPI backend (paper-only)                       |
| `apps/agent/`                | Agent tool layer (proposes; never executes)              |
| `scripts/`                   | `download_mt5_history.py` and other operational scripts  |
| `config/`                    | Version-controlled non-secret YAML config                |
