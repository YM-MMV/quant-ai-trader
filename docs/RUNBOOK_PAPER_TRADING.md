# Runbook — paper trading

How to backtest a strategy and then paper trade it. **Paper trading simulates
fills from a reference price and never touches a broker.** Every paper trade —
approved *or* rejected — is gated by the deterministic `RiskManager` and written
to an append-only log.

Prerequisites: you've completed [Getting started](GETTING_STARTED.md) (venv +
`pip install -e ".[dev]"` + `.env`).

---

## 1. Get candles

You can paper trade on either:

- **Real MT5 candles** — see the [MT5 demo account runbook](RUNBOOK_MT5_DEMO_ACCOUNT.md)
  for `scripts/download_mt5_history.py`, which writes
  `data/raw/<symbol>/<timeframe>.parquet`.
- **Sample / generated candles** — `services/data_service/sample_data.py`
  produces deterministic fake candles, which is what the tests and the
  [smoke test](../tests/test_full_pipeline_smoke.py) use. Good for trying the
  pipeline without a terminal.

The pipeline only needs a `pandas` DataFrame with the canonical OHLC schema
(`open`, `high`, `low`, `close`, plus optional `symbol`/`timeframe`).

## 2. Run a backtest

The primary engine is the in-process
[`SimpleBacktester`](../services/backtest_service/simple_backtester.py) — no
network, realistic frictions (spread, slippage, commission), and byte-for-byte
reproducible. It powers the validation/approval gate.

```python
from services.backtest_service.simple_backtester import SimpleBacktester, BacktestConfig
from services.strategy_service.adapters.rsi_pattern import RSIPatternAdapter

candles = ...  # DataFrame of OHLC candles (from MT5 download or sample_data)

backtester = SimpleBacktester(BacktestConfig())   # tune costs via BacktestConfig
report = backtester.run(candles, RSIPatternAdapter())
print(report)   # trades, P&L, metrics
```

`BacktestConfig` controls the friction assumptions (spread/slippage/commission).
See [`backtest_service/costs.py`](../services/backtest_service/costs.py) and
`metrics.py` for what's modelled and reported.

> **Optional external engine.** [QuantDinger](QUANTDINGER_SETUP.md) is wired in
> behind a thin client as an *alternative* remote backtester. It is never
> required — with no server configured it falls back to a deterministic mock.
> Use the local `SimpleBacktester` unless you specifically want that platform.

## 3. Validate before trading

Strategies must pass validation before they're allowed to execute. The
[`strategy_validator`](../services/backtest_service/strategy_validator.py) checks
a backtest against the thresholds in
[`config/strategy_validation.yaml`](../config/strategy_validation.yaml). A
strategy that isn't approved is rejected by the risk gate (see the
`strategy_approved` / `not_research_only` checks in the
[Risk manager runbook](RUNBOOK_RISK_MANAGER.md)).

## 4. Paper trade

A paper trade is created **only after** the `RiskManager` approves the
`OrderIntent`. There are two ways to drive it.

### Option A: the services directly

```python
from services.models import OrderIntent, Side, TradingMode
from services.risk_service.risk_manager import RiskManager, RiskContext
from services.execution_service.paper_execution import PaperExecutionService
from services.config_loader import load_risk_config

risk_cfg = load_risk_config()
risk = RiskManager(risk_cfg)
paper = PaperExecutionService(config=risk_cfg)

intent = OrderIntent(
    symbol="EURUSD", side=Side.BUY, volume=0.01,
    price=1.0850, stop_loss=1.0800, take_profit=1.0950,
    strategy_id="rsi_pattern",
)
ctx = RiskContext(
    mode=TradingMode.PAPER,
    allowlist=("EURUSD", "GBPUSD", "XAUUSD"),
    account_balance=10_000.0,
    reference_price=1.0850,
    spread_points=8,
)

decision = risk.evaluate(intent, ctx)             # deterministic gate
trade = paper.execute(decision, ctx, timeframe="H1", strategy_name="rsi_pattern")

print(trade.status)   # OPEN if approved, REJECTED otherwise
```

- If `decision.approved` is **True**, a `PaperTrade` is opened (status `OPEN`).
- If **False**, the trade is still logged (status `REJECTED`) with the risk
  reasons — rejected trades are recorded just like approved ones.

Close an open trade later with `paper.close(trade, exit_price=...)`; use
`paper.mark(trade, high=..., low=...)` to track adverse/favourable excursions
bar by bar.

### Option B: the local API

The [local FastAPI backend](../apps/api/main.py) exposes the same flow over
HTTP. It is **paper-only** — there is no live-execution endpoint, and the
`POST /paper-trades` route re-runs the `RiskManager` itself, so a pre-approved
decision can never be injected to bypass the gate.

```bash
pip install -e ".[api]"
uvicorn apps.api.main:app --reload      # http://127.0.0.1:8000
# interactive docs at /docs ; liveness at /health
```

Endpoints: `GET /health`, `GET /symbols`, `GET /candles`, `GET /features`,
`GET /strategies/inventory`, `GET /strategies/adapters`, `POST /backtests/run`,
`POST /risk/check`, `POST /paper-trades`.

Example — propose a trade and let the gate decide:

```bash
curl -s -X POST http://127.0.0.1:8000/paper-trades \
  -H "content-type: application/json" \
  -d '{
        "intent": {"symbol":"EURUSD","side":"BUY","volume":0.01,
                   "price":1.0850,"stop_loss":1.0800,"take_profit":1.0950,
                   "strategy_id":"rsi_pattern"},
        "context": {"account_balance":10000,"reference_price":1.0850,"spread_points":8},
        "timeframe":"H1"
      }'
```

The response includes `created`, `approved`, and either the `trade` + its
`risk_decision`, or the `reasons` + per-check `checks` for a rejection.

## 5. Review the trade and audit logs

Both logs are append-only JSONL under `data/paper_trades/`:

- `trades.jsonl` — every `PaperTrade` (open / closed / rejected).
- `audit.jsonl` — every decision on the path from proposed signal to fill
  (`signal_proposed`, `risk_decision`, `execution_decision`, `trade_rejected`,
  `config_snapshot`, `system_mode`).

```python
from services.execution_service.trade_log import TradeLogStore

log = TradeLogStore()           # defaults to data/paper_trades/trades.jsonl
for t in log.latest():          # most recent state of each trade
    print(t.trade_id, t.symbol, t.side, t.status, t.pnl)

rejected = log.rejected()       # only trades the risk gate denied
```

To understand *why* a trade was rejected, see the
[Risk manager runbook §Reviewing rejected trades](RUNBOOK_RISK_MANAGER.md#reviewing-rejected-trades).

## 6. Keep live trading disabled

While paper trading you should never need to touch the live locks. Confirm:

```dotenv
TRADING_MODE=paper
ALLOW_LIVE_TRADING=false
```

`paper` mode physically cannot reach the real MT5 gateway, and `live` is rejected
at config load unless both switches are flipped. See the
[MT5 demo account runbook](RUNBOOK_MT5_DEMO_ACCOUNT.md) for the (deliberate)
steps to go further — and the [Risk manager runbook](RUNBOOK_RISK_MANAGER.md) for
the kill switch.
