# Runbook — risk manager

The [`RiskManager`](../services/risk_service/risk_manager.py) is the **single
deterministic gate** every order passes through before any execution — paper or
live. It is pure code: **no AI, no MT5, no network.** The same intent + context +
config always yields the same decision. The AI agent *proposes*; it never
executes, and no order bypasses this gate.

---

## How it works

`RiskManager.evaluate(intent, context)` takes:

- an [`OrderIntent`](../services/models.py) — symbol, side, volume, price, stop
  loss, take profit, strategy id; and
- a [`RiskContext`](../services/risk_service/risk_manager.py) — the runtime state
  to judge it against (mode, allowlist, account balance, spread, open trades,
  realized daily loss, strategy approval/applicability, kill-switch flag, …).

It returns a [`RiskDecision`](../services/models.py) with:

- `approved` — boolean verdict;
- `reasons` — human-readable list of **every** violation (no short-circuit, so a
  rejection enumerates *all* problems at once);
- `checks` — a `name → pass/fail` map for auditing;
- `approved_volume` — the volume cleared to trade (only when approved).

## The rules (each maps to a `checks` key)

| Check key              | Rejects when…                                                        |
|------------------------|----------------------------------------------------------------------|
| `trading_mode_allowed` | the mode isn't in `allowed_modes` (`research`/`backtest`/`paper`/`mt5_demo`). |
| `live_trading_enabled` | mode is `live` but `ALLOW_LIVE_TRADING` is false.                    |
| `kill_switch_clear`    | the kill switch is enabled **and** active.                          |
| `symbol_allowlisted`   | the symbol isn't in the context allowlist.                          |
| `strategy_approved`    | the strategy isn't approved for trading.                            |
| `not_research_only`    | the strategy applicability isn't executable (`direct`/`adaptable`). |
| `stop_loss_present`    | stop loss missing (when `require_stop_loss`).                       |
| `take_profit_present`  | take profit missing (when `require_take_profit`).                   |
| `risk_per_trade`       | per-trade risk % exceeds `max_risk_per_trade_pct` (or no contract spec). |
| `daily_loss_limit`     | realized daily loss has hit `max_daily_loss`.                       |
| `max_open_trades`      | open positions are at `max_open_trades`.                            |
| `max_trades_per_day`   | entries opened today are at `max_trades_per_day`.                   |
| `spread_ok`            | spread exceeds `max_spread_points`.                                 |
| `volatility_ok`        | volatility exceeds `max_volatility` (when configured).             |
| `no_duplicate_trade`   | a position with the same `(symbol, side)` is already open.          |

**No order without a stop loss and take profit. No order for a non-allowlisted
symbol, an unapproved/research-only strategy, an over-budget risk %, a breached
daily-loss/open-trade/frequency limit, an excessive spread, or a duplicate.**

## Configuring the limits

The committed defaults live in [`config/risk.yaml`](../config/risk.yaml) and are
validated by the `RiskConfig` model in
[`config_loader.py`](../services/config_loader.py):

```yaml
require_stop_loss: true
require_take_profit: true

max_daily_loss: 100.0          # account currency; halt new orders past this loss
max_open_trades: 3             # max simultaneous open positions
max_spread_points: 30          # reject if spread (points) exceeds this

max_risk_per_trade_pct: 1.0    # % of equity riskable on a single trade
max_total_exposure_pct: 5.0    # % of equity across all open trades

kill_switch_enabled: true      # master switch able to halt all execution

allowed_modes: [research, backtest, paper, mt5_demo]   # live excluded by default
max_trades_per_day: 10
max_volatility: null           # optional ceiling (e.g. ATR); null = disabled
```

Load it and build a manager:

```python
from services.config_loader import load_risk_config
from services.risk_service.risk_manager import RiskManager

risk = RiskManager(load_risk_config())
```

A few of these can also be overridden per-machine from `.env`
(`MAX_DAILY_LOSS`, `MAX_OPEN_TRADES`, `MAX_SPREAD_POINTS`). Keep `config/risk.yaml`
as the conservative source of truth; use `.env` for environment-specific tweaks.

> Tuning the limits is a deliberate change — review [`SAFETY.md`](../SAFETY.md)
> first. Loosening a limit can let through trades the conservative defaults block.

## The kill switch

The kill switch is the master halt for **all** execution. It is enabled in config
by default (`kill_switch_enabled: true`) and *activated* at runtime via the
context:

```python
from services.risk_service.risk_manager import RiskContext
from services.models import TradingMode

ctx = RiskContext(
    mode=TradingMode.PAPER,
    allowlist=("EURUSD", "GBPUSD", "XAUUSD"),
    kill_switch_active=True,        # <-- halts every order, regardless of merit
)
```

When `kill_switch_enabled` is true in config **and** `kill_switch_active` is true
in the context, the `kill_switch_clear` check fails and **every** order is
rejected with reason *"kill switch is active"* — no matter how otherwise valid.

To enable it across your driving code, set `kill_switch_active=True` on the
`RiskContext` you pass to every `evaluate(...)` call (and ensure
`kill_switch_enabled: true` stays in `config/risk.yaml`). To resume trading,
clear the flag. Because the switch is checked inside the deterministic gate, it
cannot be bypassed by the agent, the API, or the execution services.

## Reviewing rejected trades

Every paper trade — approved *or* rejected — is logged. To review rejections:

```python
from services.execution_service.trade_log import TradeLogStore

log = TradeLogStore()                  # data/paper_trades/trades.jsonl
for t in log.rejected():               # only trades the gate denied
    print(t.trade_id, t.symbol, t.side)
    print("  reasons:", t.risk_decision.reasons)
    print("  checks :", {k: v for k, v in t.risk_decision.checks.items() if not v})
```

The append-only **audit log** (`data/paper_trades/audit.jsonl`) records a
`trade_rejected` event with the same reasons, alongside the `risk_decision`,
`config_snapshot`, and `system_mode` in force at the time — so you can answer
"why was this rejected?" after the fact without re-running anything. See the
audit event types in
[`audit_log.py`](../services/execution_service/audit_log.py).

Because every rule is evaluated (no short-circuit), a single rejected decision
lists *all* the reasons at once — fix them together rather than one resubmission
at a time.

## Related

- [Paper trading runbook](RUNBOOK_PAPER_TRADING.md) — drives the gate end to end.
- [MT5 demo account runbook](RUNBOOK_MT5_DEMO_ACCOUNT.md) — the additional live
  locks layered *on top of* this gate.
- [`SAFETY.md`](../SAFETY.md) — the hard, permanent safety rules these limits
  encode.
