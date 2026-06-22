# OpenBB setup (optional)

[OpenBB](https://github.com/OpenBB-finance/OpenBB) is an open-source financial
data and research platform. This project integrates it **optionally**, as a
**research-only supplementary data layer** behind
`services/data_service/openbb_data.py`. The system runs fine without it: the
package is imported lazily and guarded, so nothing breaks (and no tests fail)
when OpenBB is absent — tests mock it.

> **OpenBB is research only — it never feeds execution.** For forex/gold the
> execution-aligned source is **MetaTrader 5**
> (`services/data_service/mt5_data.py`). Use OpenBB for cross-checks,
> supplementary history, and the macro / asset-context research contracts —
> never to drive orders. Frames it produces carry `source="openbb"` so their
> provenance is never confused with broker data.

You only need this if you want supplementary research data from OpenBB.

## What's here

`services/data_service/openbb_data.py` exposes:

| function                          | purpose                                                      |
|-----------------------------------|-------------------------------------------------------------|
| `get_historical_data()`           | supplementary historical candles → canonical schema         |
| `get_macro_data_placeholder()`    | stable stub contract for macro data (offline, no OpenBB)     |
| `get_asset_context_placeholder()` | stable stub contract for per-asset research context          |
| `normalise_openbb_data()`         | pure transform: OpenBB result → canonical candle schema      |

The two `*_placeholder` functions return clearly marked stubs (`placeholder:
True`) so downstream code can be written against a fixed interface before the
full macro / context integration lands. They require no OpenBB install.

## Installing OpenBB

OpenBB is **not** in the core `dependencies` because it is heavy and optional.
Install the extra when you want it:

```bash
pip install -e ".[openbb]"
# or directly:
pip install openbb
```

Some OpenBB data providers need their own API keys; configure those through
OpenBB's own credential system. None are required for the test suite (it mocks
the client).

## Usage

```python
from services.data_service.openbb_data import (
    get_historical_data,
    get_macro_data_placeholder,
    get_asset_context_placeholder,
)

# Supplementary research history (NOT for execution — use MT5 for that):
df = get_historical_data(
    "EURUSD", start="2024-01-01", end="2024-06-01",
    asset_class="currency", interval="1d",
)
# df is in the canonical candle schema with source="openbb"

macro = get_macro_data_placeholder("CPI", country="US")   # stable stub
ctx = get_asset_context_placeholder("XAUUSD")             # stable stub
```

`asset_class` routes to the matching OpenBB endpoint
(`currency` / `equity` / `crypto` / `index`). For tests or offline use you can
inject a stand-in client: `get_historical_data(..., client=fake_obb)`.

## Output schema

`normalise_openbb_data()` maps an OpenBB result (an `OBBject`, a DataFrame, or a
list of records) into the project's canonical candle schema
(`services/data_service/storage.py` → `REQUIRED_COLUMNS`):

```text
timestamp, open, high, low, close,
tick_volume, spread, real_volume,
symbol, timeframe, source
```

OpenBB has no broker microstructure, so `tick_volume` and `spread` are `0` and
`real_volume` carries OpenBB's `volume` (`0` if absent). `source` is `"openbb"`.

## Constraints (by design)

- **Optional.** Never required by core code or tests.
- **Research, not execution.** Does not touch the order path; MT5 stays primary
  for execution-aligned forex/gold data.
- **Pure normalisation.** `normalise_openbb_data()` needs no OpenBB install and
  is fully unit-tested with mocks (`tests/test_openbb_interface.py`).
