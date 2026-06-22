# QuantDinger setup (optional)

[QuantDinger](https://github.com/brokermr810/QuantDinger) is an external
backtesting / trading platform. This project integrates it **optionally**, via a
thin client layer (`services/backtest_service/quantdinger_client.py`). Nothing in
core code or tests depends on QuantDinger being installed or running — the
default everywhere is a deterministic in-memory mock.

## Local backtester vs QuantDinger

The project ships its **own** backtester and that is the primary, always-available
engine:

| | Local `SimpleBacktester` | QuantDinger client |
|---|---|---|
| Location | `services/backtest_service/simple_backtester.py` | `services/backtest_service/quantdinger_client.py` |
| Runs where | in-process, no network | on an external QuantDinger server |
| Determinism | byte-for-byte reproducible | depends on the remote platform |
| Required? | **yes** — core engine | **no** — optional alternative |
| Default | always used | mock unless a server URL is set |

Use the **local backtester** for the project's deterministic, friction-aware
evaluation and the validation/approval gate. Reach for **QuantDinger** only when
you specifically want that platform's engine; it is an *alternative* remote
backtester, never a replacement for the local one.

## Modes

`load_quantdinger(mode=...)` selects the client:

| mode       | behaviour                                                              |
|------------|-----------------------------------------------------------------------|
| `auto`     | real if a server URL is configured, else the mock (default)           |
| `mock`     | deterministic in-memory `MockQuantDinger` (no network; used by tests) |
| `real`     | `RealQuantDinger`; raises if no server URL is configured              |
| `disabled` | `DisabledQuantDinger` — every call raises `QuantDingerDisabledError`  |

`auto` only talks to the network when you have explicitly pointed it at a server,
so by default (and in tests) it stays fully offline on the mock.

## Configuring the real client

The real client talks JSON over HTTP. Point it at a running server with an
environment variable (or pass `base_url=` directly):

```bash
export QUANTDINGER_URL="http://localhost:8000"
export QUANTDINGER_API_KEY="..."   # optional; sent as a Bearer token
```

`requests` is used for transport and is imported lazily — install it when you
want the real client (`pip install requests`), or inject your own `session` (any
object with `get`/`post`) for tests.

## Capabilities

```python
from services.backtest_service.quantdinger_client import load_quantdinger

qd = load_quantdinger(mode="mock")          # or "auto" with QUANTDINGER_URL set

ref = qd.submit_strategy({"name": "ma_cross", "fast": 10, "slow": 30})
job = qd.run_backtest(ref.strategy_id, symbol="EURUSD", timeframe="H1")
result = qd.fetch_backtest_result(job.job_id)
print(result.metrics)

# Paper trading — placeholders for now (not wired to a server yet):
dep = qd.submit_paper_strategy(ref.strategy_id)
logs = qd.fetch_paper_trading_logs(dep.deployment_id)
```

| method                          | status                                                 |
|---------------------------------|--------------------------------------------------------|
| `submit_strategy()`             | implemented (mock + real)                              |
| `run_backtest()`                | implemented (mock + real)                              |
| `fetch_backtest_result()`       | implemented (mock + real)                              |
| `submit_paper_strategy()`       | **placeholder** (`placeholder=True`, not implemented)  |
| `fetch_paper_trading_logs()`    | **placeholder** (`placeholder=True`, not implemented)  |

The two paper-trading methods return clearly marked placeholder objects until
that integration is built. The project's own paper executor
(`services/execution_service/paper_execution.py`) remains the supported
paper-trading path — and all execution still routes through the `RiskManager`.

## Constraints (by design)

- **Optional.** Never required by core code or tests; the local backtester is the
  primary engine.
- **Mock first.** Tests use `MockQuantDinger` (or an injected fake HTTP session)
  and never assume a live QuantDinger server — see
  `tests/test_quantdinger_client.py`.
- **No execution bypass.** QuantDinger does not place real orders for this
  project; live trading stays behind the existing safety locks and `RiskManager`.
