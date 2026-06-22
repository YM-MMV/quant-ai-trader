# MT5 execution setup (locked by default)

The real MetaTrader 5 execution gateway
([`services/execution_service/mt5_gateway.py`](../services/execution_service/mt5_gateway.py))
is the **only** place in the codebase that may call `order_send`. It is **off by
default** and refuses to place any order unless every live lock is satisfied.

> ⚠️ **Live trading is disabled by default and stays that way until a human
> explicitly flips two switches.** The mock gateway
> (`mock_mt5_gateway.py`) and the paper executor remain the normal path. Only
> enable the real gateway when you intend to send orders to *your own* terminal.

## Requirements

- **Windows.** The `MetaTrader5` Python package is Windows-only and talks to a
  locally installed, running MT5 terminal.
- A MetaTrader 5 terminal logged into an account (the user's current account is a
  **demo** account — that is fine; see below).
- Install the optional extra:

  ```bash
  pip install -e ".[mt5]"      # installs MetaTrader5 on Windows
  ```

The package is imported lazily and guarded, so the rest of the project (and the
test suite) works without it — tests mock the package and never touch a terminal.

## The live locks

`send_order` places nothing unless **all** of these hold (see
`MT5Gateway.evaluate_live_locks`):

| lock                  | source                                             |
|-----------------------|----------------------------------------------------|
| `TRADING_MODE=live`   | `.env` / `Settings.trading_mode`                   |
| `ALLOW_LIVE_TRADING=true` | `.env` / `Settings.allow_live_trading`         |
| `RiskDecision.approved` | the deterministic `RiskManager`                  |
| strategy approved     | the risk decision's `strategy_approved` check      |
| symbol allowlisted    | `SYMBOL_ALLOWLIST` env, else `config/symbols.yaml` |
| stop loss present     | the `OrderIntent` (required by the model)          |
| take profit present   | the `OrderIntent` (required by the model)          |
| broker `order_check`  | MetaTrader 5 margin / price validation             |

The base `ExecutionGateway.send_order` template *also* re-checks that the decision
is approved and matches the exact intent (no approval reuse). If any lock fails,
the attempt is logged and `LiveTradingDisabledError` is raised **before** any
broker call — `order_send` is never reached.

There is **no `demo_live` mode.** The same `live` path is used regardless of
whether the terminal is logged into a demo or a real account.

## Enabling it (deliberately)

1. Configure credentials in `.env` (never commit it; see
   [`.env.example`](../.env.example)):

   ```dotenv
   MT5_LOGIN=...
   MT5_PASSWORD=...
   MT5_SERVER=...
   MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
   SYMBOL_ALLOWLIST=EURUSD,GBPUSD,XAUUSD
   ```

2. Flip **both** safety switches (they are validated together — `live` with
   `ALLOW_LIVE_TRADING=false` is rejected outright):

   ```dotenv
   TRADING_MODE=live
   ALLOW_LIVE_TRADING=true
   ```

3. Use the gateway:

   ```python
   from services.execution_service.mt5_gateway import MT5Gateway

   gw = MT5Gateway()
   gw.connect()
   # `decision` must be an approved RiskDecision for THIS intent:
   result = gw.send_order(intent, decision)
   ```

### Using it with the demo account now

The user's MT5 is currently a **demo** account. The gateway can be used with it
today by setting the demo credentials and flipping the two switches above — the
code does not distinguish demo from real. Switching to a real account later is a
manual change *outside* the code (log the terminal into the real account and
update `.env`); no code change is required.

## Safety notes

- **`order_send` is encapsulated.** A test asserts that a literal `order_send(`
  call appears only in `mt5_gateway.py`; nothing else in the codebase may send
  orders directly.
- **No secrets are logged or returned.** `account_info()` exposes the login id
  but never the password; the connect log records only the server name.
- **Everything is logged.** Every gateway action (and every refusal) is recorded
  to `gateway.events`, and order events mirror to the shared `AuditLog` when one
  is provided.
