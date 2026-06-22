# Kronos setup (optional)

[Kronos](https://github.com/shiyu-coder/Kronos) is a pretrained candlestick
foundation model. This project integrates it **optionally**, behind the
`KronosPredictor` interface (`services/kronos_service/base.py`). The system runs
fine without it: if the package isn't installed it falls back to the
deterministic mock (`MockKronos`) or a disabled predictor — no hard dependency,
no GPU required.

You only need this if you want **real** Kronos forecasts instead of the mock.

## Modes

`load_kronos(mode=...)` (`services/kronos_service/real_kronos.py`) selects the
predictor:

| mode       | behaviour                                                            |
|------------|---------------------------------------------------------------------|
| `auto`     | real model if importable, else the mock (default everywhere)        |
| `real`     | force the real model; raises `KronosUnavailableError` if missing    |
| `mock`     | deterministic `MockKronos` (no deps, used by tests)                 |
| `disabled` | `DisabledKronos` — predicting raises `KronosDisabledError`          |

Set a default via the `KRONOS_MODE` convention in your `.env` if you wire it in;
otherwise the code defaults to `auto`.

## Installing the real model

The real path needs the Kronos repo's `model` package plus PyTorch and the
Hugging Face weights. This is **not** added to `pyproject.toml` because it is
heavy and optional.

1. Clone the repo (it is reference-only and git-ignored under `external/`):

   ```bash
   git clone https://github.com/shiyu-coder/Kronos external/Kronos
   ```

2. Install its dependencies into your environment (CPU is fine — slower):

   ```bash
   pip install torch huggingface_hub einops pandas
   # then make the repo's `model` package importable, e.g.:
   pip install -e external/Kronos        # if it ships a package, or
   export PYTHONPATH="$PWD/external/Kronos:$PYTHONPATH"
   ```

3. The first prediction downloads the pretrained weights from Hugging Face:
   - tokenizer: `NeoQuasar/Kronos-Tokenizer-base`
   - model: `NeoQuasar/Kronos-small` (also `Kronos-mini`, `Kronos-base`)

No fine-tuning is performed — we use the pretrained checkpoints only.

## Usage

```python
from services.kronos_service.real_kronos import load_kronos

predictor = load_kronos(mode="auto")          # real if installed, else mock
pred = predictor.predict(candles, symbol="EURUSD", timeframe="M15",
                         lookback=400, pred_len=12)
print(pred.model_dump())
```

Input/output contract:

```json
// request
{ "symbol": "EURUSD", "timeframe": "M15", "lookback": 400, "pred_len": 12 }

// response (real model)
{
  "symbol": "EURUSD", "timeframe": "M15",
  "predicted_return": 0.0012, "predicted_high": 1.0875, "predicted_low": 1.0842,
  "predicted_close": 1.0868, "predicted_volatility": 0.0009,
  "confidence_proxy": 0.61, "model_name": "kronos-small", "is_mock": false
}
```

Quick check from the CLI:

```bash
python scripts/test_kronos_prediction.py --symbol EURUSD --timeframe M15 \
    --lookback 400 --pred-len 12 --mode auto
```

## Constraints (by design)

- **Recent candles only.** `lookback` is clamped to the model's `max_context`
  (512 for Kronos-small/base). We never feed years of raw history to the model.
- **CPU works.** `device="cpu"` is the default; a GPU only speeds things up.
- **Pretrained only.** No fine-tuning in this milestone.
- Predictions are stored as JSONL under `data/predictions/<symbol>/<timeframe>.jsonl`
  (git-ignored).
```
