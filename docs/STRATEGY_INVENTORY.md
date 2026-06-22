# Strategy Inventory — Format & Process

The strategy inventory is a **static catalogue** of every strategy/project in
[`je-suis-tm/quant-trading`](https://github.com/je-suis-tm/quant-trading),
classified for suitability with this project's MT5 forex/gold/crypto execution
model.

It is produced by a **static scan only**: the scanner lists directories and
reads text (e.g. READMEs) but **never imports or executes any third-party
code**. Discovered `.py`/`.ipynb` files are recorded as data, not run.

## Files & code

- `strategies/inventory/quant_trading_inventory.json` — the inventory artifact.
- `services/strategy_service/inventory_scanner.py` — `scan_repository()`,
  `build_items_from_names()`, `write_inventory()`, `load_inventory()`.
- `services/strategy_service/strategy_classifier.py` — the name-based
  classification knowledge base (`classify()`).

## JSON schema

```jsonc
{
  "repo_url": "https://github.com/je-suis-tm/quant-trading",
  "count": 17,
  "items": [
    {
      "name": "MACD Oscillator",                 // strategy/project name
      "source_file_or_folder": "MACD Oscillator", // path relative to repo root
      "repo_url": "https://github.com/je-suis-tm/quant-trading",
      "local_path": "external/quant-trading/MACD Oscillator", // local checkout path
      "category": "technical_indicator",          // descriptive grouping
      "description": "…",                          // README first line, else KB text
      "required_data": ["OHLC candles"],           // inputs the strategy needs
      "supported_asset_classes": ["forex", "metal", "crypto"],
      "mt5_applicability": "direct",               // see values below
      "reason_for_applicability": "…",             // why that applicability
      "porting_status": "not_started"              // see values below
    }
  ]
}
```

### `mt5_applicability`
| value            | meaning                                                        |
| ---------------- | -------------------------------------------------------------- |
| `direct`         | runs as-is on OHLC of our instruments                          |
| `adaptable`      | feasible with an adapter / extra inputs                        |
| `research_only`  | useful for analysis, **not** for execution                     |
| `not_applicable` | cannot run on our instruments at all (e.g. needs options data) |

Unknown/unclassified items default to **`research_only`** — the safe choice, so
nothing is ever auto-marked executable without review.

### `porting_status`
`not_started` → `adapter_created` → `tested` → `approved` (or `rejected`).
The inventory ships with everything `not_started`; status advances as adapters
are built and validated in later milestones.

### `supported_asset_classes`
A subset of: `forex`, `metal`, `crypto`, `equity`, `commodity`, `index`,
`options`.

## Regenerating

The committed JSON is a **curated baseline** built from known repo contents
(`build_items_from_names`). To reconcile exact folder names/paths against a real
checkout (cloned via `scripts/clone_external_repos.py`):

```python
from pathlib import Path
from services.strategy_service.inventory_scanner import scan_repository, write_inventory

items = scan_repository(Path("external/quant-trading"))
write_inventory("strategies/inventory/quant_trading_inventory.json", items)
```

`"Ore Money Project"` is in the classifier but omitted from the baseline JSON
(its presence in the repo is unconfirmed); a real scan will include it if found.
