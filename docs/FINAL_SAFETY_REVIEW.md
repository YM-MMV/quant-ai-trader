# Final safety review (M28)

A whole-codebase audit for unsafe execution paths. This is the deliberate
"can anything send a real order without passing every safety gate?" review
required before the project is considered complete.

- **Date:** 2026-06-23
- **Branch reviewed:** `milestone-28-safety-review` (on top of `main` through M27)
- **Scope:** `services/`, `apps/`, `scripts/`, `config/`, root config files.
- **Verdict:** ✅ **No direct execution bypass exists.** Every order placement is
  funnelled through one locked gateway, behind the deterministic `RiskManager`
  and the two-switch live lock. Paper is the default; live is disabled by
  default; no secrets are committed.

The findings below are **enforced as regression tests** in
[`tests/test_execution_safety_guards.py`](../tests/test_execution_safety_guards.py)
(19 guards), so a future change that reopens any of these paths fails CI.

---

## How to reproduce the audit

```bash
# Execution surface
grep -rn "order_send"          services apps scripts
grep -rn "send_order"          services apps scripts
grep -rn "import MetaTrader5"  services apps scripts

# Safety switches
grep -rn "ALLOW_LIVE_TRADING\|allow_live_trading" services apps
grep -rn "TRADING_MODE\|trading_mode"             services apps

# Secrets
grep -rn "\.env" .            # references
git ls-files | grep -i "\.env"   # tracked env files (expect ONLY .env.example)

# Run the guards
pytest tests/test_execution_safety_guards.py -v
```

---

## 1. `order_send` — broker order placement

**Finding:** the literal `order_send(` call appears in production code in exactly
**one** module, twice, both inside the locked gateway:

| Location | Purpose |
|----------|---------|
| [`services/execution_service/mt5_gateway.py:398`](../services/execution_service/mt5_gateway.py) | `_execute_order` — open a position, *after* the approval gate **and** every live lock pass. |
| [`services/execution_service/mt5_gateway.py:454`](../services/execution_service/mt5_gateway.py) | `close_position` — close an existing position (risk-reducing; can only act on a position the locked open path created). |

Everywhere else the string appears it is **documentation or a test assertion**,
never a call:
[`mt5_data.py`](../services/data_service/mt5_data.py) (docstring: "no
`order_send`"), the safety docs, and the gateway test suite.

✅ **Confirmed: only `MT5Gateway` can call `order_send`.** Guarded by
`test_order_send_called_only_in_mt5_gateway` and
`test_mt5_data_module_never_sends_orders`. (The pre-existing
`test_mt5_gateway_safety.py::test_order_send_only_called_in_mt5_gateway` asserts
the same.)

## 2. `send_order` — the gateway entry point

**Finding:** `send_order` is the public template method defined once on the base
class and inherited by both gateways:

- [`base_gateway.py:175`](../services/execution_service/base_gateway.py) —
  `ExecutionGateway.send_order` is a **concrete template** that calls
  `approval_problem(intent, decision)` and **refuses** unless it is handed an
  *approved* `RiskDecision` whose `intent` matches the submitted order. Subclasses
  implement `_execute_order` and **cannot weaken the gate**.
- The real [`mt5_gateway.py`](../services/execution_service/mt5_gateway.py) and
  the [`mock_mt5_gateway.py`](../services/execution_service/mock_mt5_gateway.py)
  both go through this one gate.

✅ **Confirmed: an unapproved or mismatched order can never reach a broker through
any gateway.** Guarded by the four `test_approval_gate_*` tests.

## 3. RiskManager is always required before execution

**Finding:** there is no execution path that skips the deterministic
[`RiskManager`](../services/risk_service/risk_manager.py):

- **Paper:** [`PaperExecutionService.execute`](../services/execution_service/paper_execution.py)
  raises `TypeError` unless given a `RiskDecision` — *"every trade must be
  gated"*. Every `PaperTrade` (approved **or** rejected) carries its decision.
- **Agent:** [`create_paper_trade`](../apps/agent/tools.py) **re-runs**
  `RiskManager(load_risk_config()).evaluate(...)` itself
  ([tools.py:480](../apps/agent/tools.py)). The agent **cannot** pass in a
  pre-approved decision — *"self-approval is impossible"*. A rejected intent
  creates no trade.
- **Live (MT5):** the base `send_order` approval gate (§2) plus the gateway's own
  `evaluate_live_locks` ([mt5_gateway.py:283](../services/execution_service/mt5_gateway.py)),
  which are **re-checked** inside `_execute_order`
  ([mt5_gateway.py:381](../services/execution_service/mt5_gateway.py)) **before**
  any broker call. The live locks require `RiskDecision.approved` *and* the
  `strategy_approved` check.
- **API:** the only state-changing route, `POST /paper-trades`, delegates to
  `create_paper_trade`, so it inherits the same re-run gate; there is **no
  live-execution endpoint**.

✅ **Confirmed: the AI proposes; it never executes. No order bypasses
`RiskManager`.** Guarded by `test_paper_execution_requires_a_risk_decision` and
`test_create_paper_trade_reruns_risk_and_blocks_unallowlisted`.

## 4. Direct MetaTrader5 imports

**Finding:** the `MetaTrader5` package is imported in exactly **two** production
modules, each **lazily and guarded** (inside `try/except`, so absence of the
Windows-only package never breaks import):

| Location | Role |
|----------|------|
| [`services/data_service/mt5_data.py:39`](../services/data_service/mt5_data.py) | Read-only candle download. **No order placement.** |
| [`services/execution_service/mt5_gateway.py:61`](../services/execution_service/mt5_gateway.py) | The single locked execution gateway. |

No other module imports it; nothing imports it unconditionally at top level.

✅ **Confirmed.** Guarded by `test_metatrader5_imported_only_in_allowed_modules`
and `test_metatrader5_import_is_lazy_guarded`.

## 5. `TRADING_MODE` — paper is the default

**Finding:**

- [`config_loader.py:140`](../services/config_loader.py) —
  `trading_mode: TradingMode = Field(TradingMode.PAPER, alias="TRADING_MODE")`.
- [`app.yaml`](../config/app.yaml) / `AppConfig.default_mode` defaults to `paper`.
- [`agent_config.py:62`](../apps/agent/agent_config.py) — the agent is
  `TradingMode.PAPER` and `assert_paper_only` rejects anything else.
- [`.env.example`](../.env.example) ships `TRADING_MODE=paper`.

✅ **Confirmed: paper is the default everywhere.** Guarded by
`test_paper_is_the_default_mode` and `test_agent_*`.

## 6. `ALLOW_LIVE_TRADING` — live is disabled by default

**Finding:**

- [`config_loader.py:141`](../services/config_loader.py) —
  `allow_live_trading: bool = Field(False, alias="ALLOW_LIVE_TRADING")`.
- [`config_loader.py:166-172`](../services/config_loader.py) — a model validator
  **refuses** `TRADING_MODE=live` unless `ALLOW_LIVE_TRADING=true`: the safety
  lock cannot be half-enabled by accident.
- The MT5 live locks require **both** `mode_is_live` **and** `allow_live_trading`
  ([mt5_gateway.py:293-294](../services/execution_service/mt5_gateway.py)) —
  defence in depth on top of the config validator.
- [`.env.example`](../.env.example) ships `ALLOW_LIVE_TRADING=false`.

There is **no `demo_live` mode**: a demo and a real account share the one `live`
path, gated identically.

✅ **Confirmed: live is disabled by default and requires two deliberate
switches.** Guarded by `test_live_is_disabled_by_default` and
`test_live_requires_both_switches`.

## 7. `.env` / secrets — nothing sensitive is committed

**Finding:**

- [`.gitignore`](../.gitignore) ignores `.env` and `.env.*` but **un-ignores**
  `!.env.example`.
- `git ls-files | grep .env` returns **only** `.env.example`.
- [`.env.example`](../.env.example) holds **placeholders only**: every secret key
  (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `MT5_TERMINAL_PATH`, `OPENBB_PAT`)
  is blank, and it documents the paper-safe defaults.
- Secrets are loaded only from the environment / `.env` via
  `Settings` ([config_loader.py:124](../services/config_loader.py)); no secret is
  hard-coded. Gateway result models (`AccountInfo`, etc.) expose the login id but
  **never** a password.

✅ **Confirmed: no secrets are committed.** Guarded by
`test_env_is_gitignored_and_example_is_not`, `test_no_committed_dotenv_with_secrets`,
and `test_env_example_holds_placeholders_only`.

---

## Summary of guarantees

| # | Guarantee | Enforced by |
|---|-----------|-------------|
| 1 | Only `MT5Gateway` calls `order_send` | `test_order_send_called_only_in_mt5_gateway`, `test_mt5_data_module_never_sends_orders` |
| 2 | Every order needs an approved, matching `RiskDecision` | `test_approval_gate_*` (×4) |
| 3 | `RiskManager` is unavoidable on every path | `test_paper_execution_requires_a_risk_decision`, `test_create_paper_trade_reruns_risk_and_blocks_unallowlisted` |
| 4 | `MetaTrader5` imported only in 2 guarded modules, lazily | `test_metatrader5_imported_only_in_allowed_modules`, `test_metatrader5_import_is_lazy_guarded` |
| 5 | Paper is the default mode | `test_paper_is_the_default_mode`, `test_agent_*` |
| 6 | Live disabled by default; needs two switches | `test_live_is_disabled_by_default`, `test_live_requires_both_switches` |
| 7 | No secrets committed | `test_env_*` (×3) |

**Result: no direct execution bypass exists.** See [`SAFETY.md`](../SAFETY.md) for
the underlying rules and [`docs/MT5_EXECUTION_SETUP.md`](MT5_EXECUTION_SETUP.md)
for the (deliberate) steps to enable live trading.
