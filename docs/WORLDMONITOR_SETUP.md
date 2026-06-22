# WorldMonitor setup (optional)

[WorldMonitor](https://github.com/koala73/worldmonitor) is an external macro /
news / geopolitical monitoring project. This system integrates it **optionally**,
via a thin client layer (`services/macro_service/worldmonitor_client.py`), and
uses it **only for advisory risk context — never as a trading signal**.

> **Trading never depends on WorldMonitor.** It is disabled by default. When it
> is disabled or unreachable, the client returns a *neutral* context (no extra
> risk, no lockout), so the system behaves exactly as if WorldMonitor did not
> exist. Nothing in core code or tests requires it to be installed, configured,
> or running.

## What it provides

The client exposes three deliberately **placeholder** outputs (the full mapping
onto WorldMonitor's data is intentionally not built out yet):

| output | type | meaning |
|---|---|---|
| `macro_risk_score` | `float \| None` | placeholder macro risk score in `[0, 1]`; `None` when unknown/disabled |
| `event_risk` | `bool` | placeholder high-impact event-risk flag |
| `news_lockout` | `bool` | placeholder: should new entries be paused on news? (advisory) |

These are returned together as a `RiskContext`. Treat them as **context to
inform risk sizing / caution**, not as entry or exit signals.

## Modes

`load_worldmonitor(mode=...)` selects the client. **The default is `disabled`.**

| mode       | behaviour                                                                 |
|------------|---------------------------------------------------------------------------|
| `disabled` | `DisabledWorldMonitor` — neutral context, no network (**default**)        |
| `mock`     | deterministic in-memory `MockWorldMonitor` (no network; used by tests)    |
| `real`     | `RealWorldMonitor`; raises if no server URL is configured                 |
| `auto`     | real *iff* a server URL is configured, else `disabled`                    |

`auto` only talks to the network when you have explicitly pointed it at a server,
so by default (and in tests) it stays fully offline and neutral.

## Configuring the real client

The real client talks JSON over HTTP. Point it at a running WorldMonitor with an
environment variable (or pass `base_url=` directly):

```bash
export WORLDMONITOR_URL="http://localhost:8080"
export WORLDMONITOR_API_KEY="..."   # optional; sent as a Bearer token
```

`requests` is used for transport and is imported lazily — it is **not** part of
the base install. Install it when you want the real client
(`pip install requests`), or inject your own `session` (any object with a `get`
method) for tests.

## Usage

```python
from services.macro_service.worldmonitor_client import load_worldmonitor

# Default: disabled → neutral context, trading unaffected.
wm = load_worldmonitor()
ctx = wm.get_risk_context(symbol="EURUSD")
# ctx.enabled == False, ctx.macro_risk_score is None, ctx.event_risk == False ...

# Opt in to a live instance (only when WORLDMONITOR_URL is set):
wm = load_worldmonitor(mode="auto")
ctx = wm.get_risk_context(symbol="EURUSD")
if ctx.enabled and ctx.news_lockout:
    ...  # advisory: be more cautious / skip new entries — your choice, not forced
```

Because trading must not depend on WorldMonitor, callers should always tolerate a
neutral/disabled context and never *require* its outputs to make a decision.
