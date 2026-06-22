# SAFETY.md — Hard Safety Rules

These rules are **non-negotiable**. Any code, agent, or human action that
conflicts with them is wrong. When in doubt, choose the safer behavior and stop.

## Defaults (must hold at all times unless a human manually changes them)

- Default trading mode is **`paper`**.
- `ALLOW_LIVE_TRADING=false` by default.
- **Live trading is disabled** unless manually changed later by a human.

## Pre-execution checks (every order, paper or real, goes through `RiskManager`)

An order **must not** be placed if any of the following is true:

- **No stop loss** is defined.
- **No take profit** is defined.
- The **symbol is not allowlisted**.
- The **spread exceeds** the configured limit.
- The **max daily loss** has been exceeded.
- The **max open trades** limit has been reached.

## Execution boundaries

- **No AI-generated trade bypasses `RiskManager`.** The AI agent may only
  propose `OrderIntent` objects; it never executes.
- **No direct `mt5.order_send()` calls** (or equivalent broker execution calls)
  **outside the single locked MT5 gateway.**
- The MT5 gateway only acts when **all** live locks are satisfied:
  - `ALLOW_LIVE_TRADING=true`
  - `TRADING_MODE=live`
  - an approved `RiskDecision`
  - the symbol is on the explicit allowlist

## Kill switch

- A kill switch must be able to halt all execution immediately.
- When triggered, no new orders may be opened in any mode.

## Secrets

- No real credentials in code, tests, fixtures, logs, or version control.
- Secrets live only in `.env` (git-ignored). `.env.example` holds placeholders.
