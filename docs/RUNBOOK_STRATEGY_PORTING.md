# Runbook — porting a strategy (adding an adapter)

How to add a new strategy to the system by writing a **strategy adapter**. An
adapter wraps one strategy's deterministic decision logic behind a single uniform
interface so the rest of the pipeline treats every strategy identically.

See [`docs/STRATEGY_INVENTORY.md`](STRATEGY_INVENTORY.md) for how source
strategies (seeded from
[`je-suis-tm/quant-trading`](https://github.com/je-suis-tm/quant-trading)) are
catalogued and classified before porting.

---

## The adapter contract

Adapters are intentionally narrow and safe (enforced by the base class in
[`services/strategy_service/base.py`](../services/strategy_service/base.py)):

- **No direct trading.** An adapter only ever *proposes* an `AdapterSignal`; it
  never places orders. Execution is owned by the `RiskManager` + execution
  service.
- **No MT5 / broker calls.** Adapters compute on in-memory candles/features only.
- **No AI calls.** Strategy logic is deterministic — same inputs, same output.
- **No look-ahead.** Use only candles/features up to and including the current
  bar (the M5 features are already causal).
- **Fail safe.** If inputs are insufficient — or the adapter raises — the
  framework returns a `NONE` (abstain) signal rather than guessing. This is
  enforced centrally in `generate_signal`, so an adapter can't crash the pipeline
  or emit a signal on bad data.

A concrete adapter implements just **two** methods; the base
`generate_signal` template handles validation and the fail-safe wrapper.

| Method                       | You implement                                              |
|------------------------------|------------------------------------------------------------|
| `get_metadata()`             | Static `AdapterMetadata` (name, version, supported symbols/timeframes, `min_candles`, asset classes, provenance). |
| `_compute_signal(candles, features, kronos_prediction)` | The deterministic strategy logic. Return a signal via `make_signal(...)` or `none_signal(...)`. |

## Step 1 — create the adapter file

Adapters live in
[`services/strategy_service/adapters/`](../services/strategy_service/adapters).
Copy the shape of an existing one — for example
[`rsi_pattern.py`](../services/strategy_service/adapters/rsi_pattern.py) — and
reuse the shared helpers in
[`adapters/_common.py`](../services/strategy_service/adapters/_common.py)
(`atr_value`, `sl_tp_from_atr`, `cost_notes`, `clamp_confidence`, the repo URL,
asset classes, etc.).

```python
# services/strategy_service/adapters/my_strategy.py
from __future__ import annotations

from typing import Any, Optional

from services.strategy_service.adapters import _common as c
from services.strategy_service.base import (
    AdapterMetadata, AdapterSignal, SignalSide, StrategyAdapter,
)


class MyStrategyAdapter(StrategyAdapter):
    """One-line description of the ported idea (not the original code)."""

    VERSION = "1.0.0"

    def __init__(self, lookback: int = 20) -> None:
        self.lookback = lookback

    def get_metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            name="my_strategy",                    # unique registry key
            version=self.VERSION,
            source_repo_url=c.REPO_URL,
            source_strategy="My Strategy",         # canonical source name
            category="technical_indicator",
            description="What it does, in one line.",
            supported_symbols=None,                # None = any; or a list
            supported_timeframes=["M15", "H1", "H4"],
            min_candles=self.lookback + 2,         # enough history to be valid
            asset_classes=c.ASSET_CLASSES,
        )

    def _compute_signal(
        self, candles: Any, features: Any, kronos_prediction: Optional[Any]
    ) -> AdapterSignal:
        close = candles["close"].astype(float)

        # ... deterministic logic on candles/features up to the LAST bar only ...

        if not <enough_data_warmed_up>:
            return self.none_signal("indicator not warmed up yet")

        if <buy_condition>:
            entry = float(close.iloc[-1])
            atr = c.atr_value(candles)
            sl, tp = c.sl_tp_from_atr(SignalSide.BUY, entry, atr)
            return self.make_signal(
                SignalSide.BUY, confidence=0.6,
                reason="why this is a BUY",        # actionable signals NEED a reason
                suggested_stop_loss=sl, suggested_take_profit=tp,
                risk_notes=c.cost_notes(candles),
                symbol=c.last_symbol(candles) or None,
            )

        return self.none_signal("no actionable setup at the latest bar")
```

Invariants the base enforces for you:

- A `NONE` signal is coerced to confidence `0` with no SL/TP.
- An actionable (`BUY`/`SELL`) signal **must** carry a non-empty `reason`.
- Suggested SL/TP are *hints* — the `RiskManager` re-validates and may override
  them. An adapter never bypasses the gate.

## Step 2 — register the adapter

Adapters are discovered through a `StrategyRegistry`
([`registry.py`](../services/strategy_service/registry.py)). Use the
`@register_adapter` decorator (or the process-wide `default_registry`) so it
self-registers on import, keyed by its unique metadata `name`:

```python
from services.strategy_service.registry import register_adapter

@register_adapter
class MyStrategyAdapter(StrategyAdapter):
    ...
```

Make sure the module is imported wherever the registry is populated (follow how
the existing adapters in `adapters/__init__.py` are wired). A duplicate `name`
raises unless `replace=True` is passed — names must be unique.

## Step 3 — test it

Add a test under [`tests/`](../tests) mirroring the existing adapter tests. At
minimum cover:

- **Metadata** is valid (`name`, `version`, `min_candles`, supported timeframes).
- **A clear BUY case** and a clear SELL case produce the expected side + a reason.
- **Insufficient data** returns `NONE` (the fail-safe path) rather than raising.
- **Determinism** — same candles in, same signal out.

```bash
pytest tests/test_my_strategy.py -v
```

## Step 4 — backtest and validate

Before a strategy can execute (even on paper) it must pass validation:

1. Backtest it with the
   [`SimpleBacktester`](../services/backtest_service/simple_backtester.py) — see
   the [Paper trading runbook](RUNBOOK_PAPER_TRADING.md#2-run-a-backtest).
2. Run it through the
   [`strategy_validator`](../services/backtest_service/strategy_validator.py),
   which checks the backtest against the thresholds in
   [`config/strategy_validation.yaml`](../config/strategy_validation.yaml).
3. Only an approved strategy clears the risk gate's `strategy_approved` and
   `not_research_only` checks (see the
   [Risk manager runbook](RUNBOOK_RISK_MANAGER.md)). A **research-only**
   classification can never reach execution.

## Step 5 — paper trade it

Once registered and approved, the strategy flows through the same paper pipeline
as any other — see the [Paper trading runbook](RUNBOOK_PAPER_TRADING.md). Run the
[full-pipeline smoke test](../tests/test_full_pipeline_smoke.py) as a final
wiring check:

```bash
pytest tests/test_full_pipeline_smoke.py -v
```

## Checklist

- [ ] New file in `services/strategy_service/adapters/`, subclassing `StrategyAdapter`.
- [ ] `get_metadata()` and `_compute_signal()` implemented; logic deterministic and causal.
- [ ] Registered with a **unique** `name`.
- [ ] Actionable signals carry a `reason`; insufficient data returns `NONE`.
- [ ] Tests: metadata, BUY/SELL, fail-safe, determinism.
- [ ] Backtested and passes `strategy_validator`.
- [ ] Smoke test still green.
