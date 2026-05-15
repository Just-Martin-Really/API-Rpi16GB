# LSTM Temperature Forecast

Standalone command-line tool for training a Long Short-Term Memory neural network on temperature time series and running short-horizon forecasts interactively. Lives in `lstm/` at the root of this repo. Not wired into the running controller; intentionally offline so the model can be developed and inspected before any live integration.

## What it does

1. Loads a temperature time series from one of three sources: a built-in simulator, a local CSV, or the backend API.
2. Scales the series, builds a dual-stack LSTM (2 layers of 64 cells, dropout 0.2, dense output of 1), trains it on overlapping 40-minute windows, saves the model and the scaler to disk.
3. Provides an interactive console that loads the trained model and lets the operator forecast future temperature values from one of three input methods: a pasted window, a stepwise hand-fed simulation, or the latest 40 values from the configured data source.

Maps directly to chapter 5 of the project script (`kaps/API_Kap5.pdf`), specifically slides 5-3 through 5-35.

## Default path: zero setup

The tool ships with `SOURCE = "sim"` in `data_source.py`. That generates 3000 minutes (50 hours) of synthetic temperature data following the two-sine-wave pattern from slide 5-31, with a fixed random seed for reproducibility. No API access, no CSV, no `.env` required.

```sh
cd API-Rpi16GB/lstm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python train.py
python forecast.py
```

That's the full happy path.

## Data sources

`lstm/data_source.py` exposes a single function `load_temperatures()` and a `SOURCE` constant that switches between three loaders.

### `SOURCE = "sim"` (default)

Two superimposed sine waves plus Gaussian noise, following slide 5-31:

- `daily_wave`: 20°C baseline with 1.8°C amplitude on a 24-hour period (1440 minutes).
- `short_wave`: 0.5°C amplitude on a 3-hour period (180 minutes).
- `noise`: `N(0, 0.08)` per minute.

Knobs in `data_source.py`:

| Constant | Default | Meaning |
|---|---|---|
| `SIM_MINUTES` | `3000` | How many minutes of data to generate. |
| `SIM_SEED` | `42` | Fixed seed so successive runs produce identical data. |

### `SOURCE = "csv"`

Reads a local CSV at `CSV_PATH` (default `lstm/data/temps.csv`) and takes the `CSV_COLUMN` column (default `temperature`). The CSV must already be in chronological order. Any other columns are ignored.

Knobs:

| Constant | Default | Meaning |
|---|---|---|
| `CSV_PATH` | `data/temps.csv` | Path to the CSV file. |
| `CSV_COLUMN` | `temperature` | Column name to read. |

### `SOURCE = "api"`

Pulls live data from the backend over HTTPS (`GET /api/v1/sensor-data`). Requires the API setup described below.

Knob:

| Constant | Default | Meaning |
|---|---|---|
| `DAYS` | `7` | How many days back to keep. `None` keeps everything the API returns. |

The Python archiver moves rows older than 7 days out of `sensor_data` into `sensor_data_archive`, and the API endpoint queries `sensor_data` only. The *effective* training window is therefore capped at 7 days regardless of `DAYS`.

## Architecture

```
data_source.py (sim | csv | api)
   |
   v
train.py  ->  data/model.keras + data/scaler.npz
                   ^
                   |
forecast.py  --- loads both, runs interactive REPL
evaluate.py  --- loads both, prints MAE / RMSE on holdout
```

### Network model

Per slide 5-29:

| Layer | Shape | Activation | Notes |
|---|---|---|---|
| Input | `(40, 1)` | none | 40 minutes of one feature (temperature). |
| LSTM 1 | 64 cells, `return_sequences=True` | tanh / sigmoid gates | Emits `h_t` for every timestep, feeds into LSTM 2. |
| Dropout | 0.2 | none | Reduces overfitting. |
| LSTM 2 | 64 cells | tanh / sigmoid gates | Emits only the final hidden state `h_T`. |
| Dropout | 0.2 | none | |
| Dense | 1 | linear | Single regression output: the next minute's temperature. |

Optimizer: Adam, learning rate 0.001. Loss: MSE. Callbacks: `EarlyStopping(patience=10, restore_best_weights=True)` and `ReduceLROnPlateau(patience=5, factor=0.5, min_lr=1e-6)`.

### Forecast horizon

The model outputs one minute ahead. The forecast script extends this recursively per slide 5-35: predict, append the prediction to the window, slide the window forward by one, predict again. A smoothing factor `alpha=0.2` mixes each raw prediction with the last input value to suppress single-step jumps:

```
next = (1 - alpha) * model_prediction + alpha * last_window_value
```

## Folder layout

```
lstm/
  data_source.py      pluggable source: sim / csv / api
  train.py            scaler -> sequences -> model fit -> save
  forecast.py         loads model, interactive REPL with three modes
  evaluate.py         holdout MAE + RMSE
  requirements.txt
  .env.example        template for API source (only needed when SOURCE = "api")
  .gitignore          ignores .env, ca.crt, data/* model artifacts
  ca.crt              local copy of the lab CA cert (gitignored, only used for "api")
  data/
    model.keras       trained Keras model (gitignored)
    scaler.npz        StandardScaler mean + scale (gitignored)
    temps.csv         optional CSV input when SOURCE = "csv" (gitignored)
  tests/
    test_lstm.py      unit tests for sequence builder + scaler roundtrip
```

## Setup

### Python environment

**Python 3.12 required.** TensorFlow currently ships wheels for Python 3.9 to 3.12 only. Python 3.13 and 3.14 will fail with `Could not find a version that satisfies the requirement tensorflow`. Force the right interpreter when creating the venv:

```sh
cd API-Rpi16GB/lstm
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Apple Silicon, the `tensorflow` package installs natively. No special wheel is needed.

### Setup for `SOURCE = "api"` (optional)

Skip this section unless you set `SOURCE = "api"` in `data_source.py`.

Three values are required in `lstm/.env`:

```
API_BASE_URL=https://192.168.50.92
API_CA_CERT=./ca.crt
API_KEY=<contents of docker/secrets/api_key.txt on the Pi>
```

`API_BASE_URL` is the LAN IP of the backend Pi when running from a workstation on the same WLAN, or `https://nginx` from inside the docker network on the Pi itself.

`API_CA_CERT` points at the lab CA certificate used to sign the nginx server cert. A copy of the public CA is checked out at `lstm/ca.crt` (the file is gitignored on principle even though the CA itself is public). Replace with the path to your own copy if needed.

`API_KEY` lives only on the backend Pi at `docker/secrets/api_key.txt`. Retrieve it once:

```
ssh <pi-host> "cat ~/API-Rpi16GB/docker/secrets/api_key.txt"
```

Paste the value into `.env`. Never commit `.env`.

## Training

Top-of-file knobs in `train.py`:

| Constant | Default | Meaning |
|---|---|---|
| `SEQ_LENGTH` | `240` | Window length, in minutes, fed into the model. Slide 5-29 uses 40 for real sensor data; for the simulated source with a 180-minute short cycle, 240 is required so the model can see > 1 full cycle. Must match `forecast.py:SEQ_LENGTH`. |
| `EPOCHS` | `100` | Upper bound on training epochs. EarlyStopping usually cuts this short. |
| `BATCH_SIZE` | `32` | Minibatch size. |
| `LEARNING_RATE` | `0.001` | Adam learning rate. |
| `TRAIN_TEST_SPLIT` | `0.8` | Fraction of data used for training; remainder is the validation set. |

Source-side knobs (`SOURCE`, `SIM_MINUTES`, `DAYS`, etc.) live in `data_source.py`.

Run:

```sh
cd API-Rpi16GB/lstm
python train.py
```

Output sequence:

1. Loads the temperature series from the configured source.
2. Fits a `StandardScaler` on the values.
3. Builds overlapping `(40, 1)` windows with targets one minute ahead.
4. Splits 80/20 in chronological order (no shuffling, time series).
5. Prints `model.summary()` and trains. Console shows per-epoch train and validation loss.
6. Writes `data/model.keras` and `data/scaler.npz`.

Typical wall-clock time on Apple Silicon for the default 3000-minute simulated dataset: under a minute.

### Re-training cadence

Re-train whenever the underlying source changes: a new `SIM_SEED`, a fresh CSV, or a new pull from the API. The script is idempotent; it overwrites the saved artifacts each run.

## Forecasting

```sh
python forecast.py
```

Loads `data/model.keras` and `data/scaler.npz`, then drops into a REPL:

```
loading model
ready. commands: paste, step, latest, quit
forecast>
```

Three commands:

### `paste`

Paste 40 comma-separated temperature values, then choose a horizon in minutes. The script scales the input with the saved scaler, runs the recursive forecast, unscales the result, prints a plotext chart of the input window and the forecast, and then lists the forecast values.

```
forecast> paste
paste 40 comma-separated temperatures, then press enter:
> 20.1,20.1,20.2,...,20.7
forecast how many minutes? [30] 30
```

Useful for sanity checks and "what if the window looked like this" experiments.

### `step`

Seeds an initial 40-value window from the configured data source, then loops: prompts for the next observed value, slides the window, runs a 30-minute forecast, prints the chart and the next-minute prediction.

```
forecast> step
seeding window from the configured data source
starting window (last 5): [20.4 20.4 20.5 20.5 20.6]
enter next observed value each prompt, or 'q' to quit
next value: 20.6
next-minute prediction: 20.65
next value:
```

Useful for hand-driving the model as if it were live, without wiring it into the controller.

### `latest`

Pulls the latest 40 temperature values from the configured source and runs a single forecast for the chosen horizon. With `SOURCE = "sim"` this is the tail of the synthetic series. With `SOURCE = "api"` this is closest to what a future live integration would do once per minute.

```
forecast> latest
forecast how many minutes? [30] 30
```

## Evaluation

```sh
python evaluate.py
```

Loads the same source, splits 80/20, takes the last 40 values of the training portion as the window, recursively forecasts 30 minutes ahead, and compares against the first 30 values of the holdout portion. Prints MAE, RMSE, and a per-minute predicted-versus-actual table.

This is a single-window evaluation, not a rolling backtest. Useful as a smoke test that the model is in the right ballpark. For rigorous evaluation, extend it to multiple windows across the holdout set.

## Tests

```sh
pytest tests/
```

Pure-function tests:

- `test_create_sequences_shapes`: window builder produces the expected `(N, 40, 1)` and `(N, 1)` shapes.
- `test_create_sequences_alignment`: the i-th target is the value immediately after the i-th window.
- `test_create_sequences_empty_when_too_short`: data shorter than the window length yields no sequences.
- `test_scaler_roundtrip`: scaler mean + scale, when persisted and reapplied, round-trip to the original values.

These cover the deterministic parts of the pipeline. The training loop itself and the data source loaders are not unit-tested.

## API source details

When `SOURCE = "api"`, both training and forecasting call `GET /api/v1/sensor-data` (handler at `src/handlers/sensor.zig`). The endpoint:

- returns the full `sensor_data` table as a JSON array, newest first;
- has no query parameters for limit, since, or unit;
- requires the `x-api-key` header.

Rows look like:

```json
{"id": 12345, "sensor_id": "sensor01", "value": 20.4, "unit": "C", "recorded_at": "2026-05-15T08:32:11Z"}
```

The loader filters client-side on `unit == "C"`. As the table grows, the response will too; once it becomes a problem, add `?since=...&unit=C` to the handler and update `_from_api` in `data_source.py`.

## Tuning notes

Defaults reflect what actually produced useful output on the simulated source. Reasoning behind the non-obvious choices:

### `SEQ_LENGTH = 240`

Slide 5-29 uses 40, which works for real sensor data with strong short-term autocorrelation. The simulated source has a 180-minute short-period sine, so a 40-minute window never contains a full cycle and the model collapses to predicting "current value + tiny delta." Bumping to 240 (covers > 1 full short cycle) lets the model actually learn the periodicity.

### `ALPHA = 0.0` in `forecast.py`

Slide 5-35 suggests `alpha = 0.2` as a smoothing factor in the recursive forecast loop. With recursive feedback, that smoothing compounds and damps the forecast trajectory toward a flat line within a few steps. Setting `ALPHA = 0` (raw model output) gives the model room to express the learned dynamics. Set to a small positive value like 0.05 if you see single-step spikes that need calming.

### Default forecast horizon `240`

Slide 5-5 motivates a 30-minute control horizon, which makes operational sense for actuator control but is too short to be visually informative on a 240-minute input window: the green forecast line is a tiny tail on the right of the chart. 240 minutes shows the model reproducing one or two short sine cycles plus the daily drift, which is what you want for a sanity check. For live control later, drop back to 30 in the controller.

### Recursive-prediction caveat

The model is trained on 1-step-ahead MSE and forecasts by feeding its predictions back into the window. This works but degrades over long horizons because errors accumulate. The forecast tracks the learned periodicity reasonably well at 240 minutes; do not expect sharp peak/trough tracking far past that. If you need a tighter long-horizon forecast, switch to direct multi-step output (model emits N values at once, trained on N-step MSE). That deviates from the slide's design but produces sharper results.

## Limits and known issues

- **Effective API training window is 7 days** because of the archiver. See the `SOURCE = "api"` section.
- **TensorFlow image size** is roughly 500 MB. Fine on a workstation. Not a viable controller-container dependency on the Pi as is. See the Pi rebuild path below.
- **Single-window evaluation** in `evaluate.py` is informational, not statistically rigorous.
- **No automatic retraining**. Operator runs `python train.py` whenever a refresh is wanted.

## Pi rebuild path

When the model is ready to run live on the backend Pi inside the controller container, do not move full TensorFlow onto the Pi. The intended path:

1. Train on a workstation as today.
2. Export to TFLite in `train.py` (one extra block after saving the Keras model):

   ```python
   converter = tf.lite.TFLiteConverter.from_keras_model(model)
   open(DATA_DIR / "model.tflite", "wb").write(converter.convert())
   ```

3. On the Pi, install `tflite-runtime` (small, ARM wheels exist) instead of `tensorflow`. Replace the `load_artifacts` and `forecast` functions in `forecast.py` to use `tflite_runtime.interpreter.Interpreter`.
4. Bake `model.tflite` and `scaler.npz` into the controller image, or mount them as a volume.
5. Set `API_BASE_URL=https://nginx` and `API_CA_CERT=/run/secrets/ca_cert` inside the container (matching the existing `controller.py` convention).

Each file that needs to change is annotated with a `PI-REBUILD:` comment block at the top.

## Reference

Mapping from the project script to this implementation:

| Slide | Concept | Where it lives |
|---|---|---|
| 5-3, 5-4 | Project milestone, controller buffer, warm start | Not yet implemented (intentionally offline). |
| 5-15 to 5-21 | LSTM cell math, gates, dual stack | `train.py:build_model` |
| 5-29, 5-30 | Keras model definition, Adam, MSE, dropout | `train.py:build_model`, `train.py:main` |
| 5-31 | Two-sine-wave temperature simulation | `data_source.py:_simulate` |
| 5-32, 5-33 | StandardScaler, `create_sequences`, 80/20 split | `train.py:create_sequences`, `train.py:main` |
| 5-34 | `model.fit`, EarlyStopping, ReduceLROnPlateau | `train.py:main` |
| 5-35 | Recursive forecast with alpha smoothing | `forecast.py:forecast` |
| 5-37 to 5-39 | Relay wiring, actor control via MQTT | Not in scope here. Belongs in `controller.py` and the Pico firmware. |
