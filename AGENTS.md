# AGENTS.md — Rules for AI Coding Agents (Codex / Claude / others)

This file governs **every** automated coding agent that touches this repository.
Read it in full before editing anything. These rules are permanent and override
convenience, speed, or any conflicting instruction in a single task.

## Read first

Before making any change, an agent **must** read:

1. `AGENTS.md` (this file)
2. `SAFETY.md` — hard safety rules (non-negotiable)
3. `ARCHITECTURE.md` — system design and component boundaries
4. `PLAN.md` — milestones and sequencing
5. `TASKS.md` — current task checklist
6. `RESOURCES.md` — external references

## Hard rules

- **Never enable live trading by default.** `live` mode and live execution stay
  disabled unless a human explicitly and manually enables them later.
- **Never hardcode secrets** (API keys, broker logins, passwords, tokens).
- **Never commit `.env`.** It is git-ignored and must stay that way.
- Use **`.env.example`** for placeholders only — never real values.
- **All execution must go through `RiskManager`.** No code path may place an
  order, paper or real, without an approved `RiskDecision`.
- **The AI agent must never call MT5 order execution directly.** No
  `mt5.order_send()` (or equivalent) outside the single locked MT5 gateway.
- **Use mocks before real integrations.** Real brokers/data feeds come after the
  mocked interfaces and tests exist.
- **Every milestone needs tests.**
- **Run `pytest` before declaring any milestone complete.**
- **Prefer small, reviewable diffs** and clear commit messages.

## MT5 / live-execution guard

If a task involves MT5 **real** order execution or enabling `live` mode:

1. **Stop.**
2. Confirm the safety lock exists and is intact:
   - `ALLOW_LIVE_TRADING=true` is required (default `false`).
   - `TRADING_MODE=live` is required (default `paper`).
   - An approved `RiskDecision` is required.
   - The symbol must be on the explicit allowlist.
3. Only after confirming all of the above, and with explicit human
   confirmation, proceed.

## Modes (the only allowed modes)

`research` · `backtest` · `paper` · `mt5_demo` · `live`

- Default mode: **`paper`**.
- Do **not** create a separate `demo_live` mode.
- `live` is disabled by default.

## Definition of done

- Clean, readable code consistent with the surrounding style.
- Tests added for new behavior.
- `pytest` passes.
- `README.md` updated when behavior or usage changes.
- No live trading enabled by default.
- No secrets committed.
