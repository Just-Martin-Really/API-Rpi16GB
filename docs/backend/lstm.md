# LSTM Temperature Forecast and Control Loop

Long Short-Term Memory neural network trained on the temperature time series produced by `sensor01`. Two entry points live in `lstm/`:

- `forecast.py`: one-shot smoke test. Loads the trained model, pulls the latest window, prints and plots a forecast.
- `control_loop.py`: long-running daemon. On a fixed interval it pulls the latest window, predicts ahead, and posts heater or cooler commands tagged `issued_by='machine'` so the actuator_commands table records which commands came from the model rather than from a dashboard user.

The model itself is shared: a 2-layer LSTM with dropout, trained 1-step-ahead on overlapping 240-minute windows, forecasting recursively. Maps to chapter 5 of the project script (`kaps/API_Kap5.pdf`), slides 5-3 through 5-35.

## What it does end to end

1. `train.py` loads a temperature time series, fits a `StandardScaler`, builds overlapping `(240, 1)` windows, trains the network, saves `data/model.keras` and `data/scaler.npz`.
2. `forecast.py` loads both artifacts, pulls the latest 240 values, and prints the forecast plus a terminal plot. Use after training to eyeball model quality.
3. `control_loop.py` runs forever: pull window, forecast `LOOKAHEAD` minutes, decide heater or cooler state, POST changed commands to the backend, sleep, repeat.

## Where things run

| Component | Where | How |
|---|---|---|
| Training (`train.py`) | Workstation (Mac) | Local Python 3.12 venv. Produces `data/model.keras` + `data/scaler.npz`. |
| Forecast smoke test (`forecast.py`, `evaluate.py`) | Workstation (Mac) | Same venv. Useful after retraining. |
| Control loop (`control_loop.py`) | 16GB Pi | Docker service in the backend compose stack. |

Artifacts move from workstation to Pi via git: `data/model.keras` and `data/scaler.npz` are committed (small, ~640KB total) and `COPY`'d into the `lstm` image at build time. Retrain locally, commit the new artifacts, `git pull` on the Pi, `docker compose build lstm`, restart the service.

## Local development (workstation)

```sh
cd API-Rpi16GB/lstm
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# fill in lstm/.env (see Configuration below)
python train.py
python forecast.py
```

`python forecast.py --minutes 240 --no-plot` is the headless variant for CI or scripts.

## Data sources

`lstm/data_source.py` exposes `load_temperatures()` and switches between three loaders via the `DATA_SOURCE` env var (or `SOURCE` constant in the file).

| Source | Use case |
|---|---|
| `api` (default) | Pulls live data from the backend via a Keycloak access token. Authenticates as the `lstm-client` confidential client using the client-credentials flow. |
| `sim` | Two-sine-wave synthetic series from slide 5-31. Zero setup, useful for offline development. |
| `csv` | Reads `data/temps.csv`. Useful for one-off datasets. |

### `DATA_SOURCE=api` (default)

Authenticates as the `lstm-client` Keycloak client using the client-credentials flow, calls `GET /api/v1/sensor-data` with the resulting RS256 access token in the `Authorization` header, filters rows where `unit == "C"`, sorts by `recorded_at`, returns the last `API_DAYS` days.

Knob: `API_DAYS` (default 7). The backend archives rows older than 7 days into `sensor_data_archive`, so the effective training window is capped at 7 days regardless of `API_DAYS`.

### `DATA_SOURCE=sim`

Two superimposed sine waves plus Gaussian noise, following slide 5-31:

- daily wave: 20°C baseline with 1.8°C amplitude on a 24-hour period.
- short wave: 0.5°C amplitude on a 3-hour period.
- noise: `N(0, 0.08)` per minute.

Constants in `data_source.py`: `SIM_MINUTES` (default 10000), `SIM_SEED` (default 42).

### `DATA_SOURCE=csv`

Reads a chronologically ordered CSV at `CSV_PATH` and takes the `CSV_COLUMN` column.

## Configuration

Production values are set in the `lstm` service block of `docker/docker-compose.yml`. The values below are for local development on a workstation; copy `lstm/.env.example` to `lstm/.env` and fill in.

```
# ── API access ────────────────────────────────────────────────────────────────
API_BASE_URL=https://www.lab.local
API_CA_CERT=./ca.crt
KEYCLOAK_TOKEN_URL=https://www.lab.local/auth/realms/iot/protocol/openid-connect/token
LSTM_CLIENT_ID=lstm-client
LSTM_CLIENT_SECRET_FILE=./keycloak_lstm_secret.txt

# ── Data source ───────────────────────────────────────────────────────────────
DATA_SOURCE=api
API_DAYS=7

# ── Control loop ──────────────────────────────────────────────────────────────
TARGET_LOW=19.0
TARGET_HIGH=23.0
LOOKAHEAD=30
LOOP_SECONDS=60
ACTUATOR_HEATER_ID=heater01
ACTUATOR_COOLER_ID=cooler01
```

### Keycloak client credentials

The control loop authenticates as the `lstm-client` confidential client in the `iot` realm. The realm is provisioned out of `docker/keycloak/iot-realm.json`; `lstm-client` is granted the `lstm-control` realm role, which the backend checks before accepting a `POST /api/v1/actuator-command` call.

The client secret is hardcoded in `iot-realm.json` (see [`docker/keycloak/keycloak_secrets.md`](../../docker/keycloak/keycloak_secrets.md)) and the same verbatim value lives in the secret file:

```sh
echo "sc_lstm_client" > docker/secrets/keycloak_lstm_secret.txt
```

The file is mounted into the `lstm` container at `/run/secrets/keycloak_lstm_secret`. For local-workstation development put the same value into the file referenced by `LSTM_CLIENT_SECRET_FILE` in `lstm/.env`.

`api_client.ApiClient` reads the secret, requests a token from `http://keycloak:8080/auth/realms/iot/protocol/openid-connect/token` via the client-credentials grant, caches it, and refreshes `TOKEN_REFRESH_MARGIN_SECONDS` (30 s) before its declared `expires_in`. A 401 from the backend forces a one-shot refresh and a retry, covering token rotations and brief Keycloak restarts.

### CA cert

`API_CA_CERT` points at the lab CA certificate that signed the nginx server cert. In the Pi container it is mounted at `/run/secrets/ca_cert` via the existing `ca_cert` compose secret. For local development a copy lives at `lstm/ca.crt` and is gitignored on principle.

## Network model

| Layer | Shape | Activation | Notes |
|---|---|---|---|
| Input | `(240, 1)` | none | 240 minutes of temperature. |
| LSTM 1 | 64 cells, `return_sequences=True` | tanh / sigmoid gates | Feeds the full sequence into LSTM 2. |
| Dropout | 0.2 | none | |
| LSTM 2 | 64 cells | tanh / sigmoid gates | Emits only the final hidden state. |
| Dropout | 0.2 | none | |
| Dense | 1 | linear | Next-minute temperature. |

Optimizer: Adam, lr 0.001. Loss: MSE. Callbacks: `EarlyStopping(patience=10, restore_best_weights=True)`, `ReduceLROnPlateau(patience=5, factor=0.5, min_lr=1e-6)`.

The forecast script extends the 1-step output recursively (slide 5-35): predict, append to the window, slide forward, predict again. `ALPHA` mixes the raw prediction with the last input value to suppress single-step jumps:

```
next = (1 - ALPHA) * model_prediction + ALPHA * last_window_value
```

## Folder layout

```
lstm/
  api_client.py       Keycloak client-credentials + /api/v1 client used by data_source and control_loop
  data_source.py      pluggable source: api / sim / csv
  train.py            scaler -> sequences -> model fit -> save artifacts
  forecast.py         one-shot: load model, pull window, predict, plot
  decide.py           pure decision function: forecast -> desired heater/cooler state
  control_loop.py     long-running daemon: pull, predict, decide, POST as 'machine'
  evaluate.py         holdout MAE + RMSE
  requirements.txt
  .env.example        template for .env (gitignored)
  .gitignore          ignores .env, ca.crt, keycloak_lstm_secret.txt, .venv, temps.csv, tflite artifact
  data/
    model.keras       trained model (committed; baked into the lstm image)
    scaler.npz        scaler mean + scale (committed; baked into the lstm image)
    temps.csv         optional CSV input (gitignored)
  tests/
    test_lstm.py      unit tests for sequence builder + scaler roundtrip
    test_decide.py    unit tests for the decision function
```

## Control loop

The control loop never talks to the controller directly. The two are decoupled by `actuator_commands` in postgres, the same table the dashboard uses:

```
sensor01 ──── MQTT ────► mosquitto ──── webserver ────► sensor_data
                                                            │
                                                            │ GET /api/v1/sensor-data
                                                            │ (RS256 bearer, as lstm-client)
                                                            ▼
                                            ┌──────────────────────────────┐
                                            │   lstm/control_loop.py       │
                                            │                              │
                                            │   1. pull last 240 minutes   │
                                            │   2. model.predict(window)   │
                                            │   3. decide(forecast)        │
                                            │   4. diff(last_state)        │
                                            └──────────────────────────────┘
                                                            │
                                                            │ POST /api/v1/actuator-command
                                                            │ {issued_by: "machine"}
                                                            ▼
                                                   actuator_commands
                                                            │
                                                            │ poll every 2s
                                                            ▼
                                            controller.py ──── MQTT ────► Pico (heater/cooler)
```

The LSTM is one of several command sources. Dashboard buttons and operator scripts (`scripts/heater.sh`, `scripts/cooler.sh`) write to the same table with `issued_by='user'`. `controller.py` drains them all identically, so the LSTM does not need any controller-side changes to take effect; it just inserts rows. `issued_by` is purely for auditing which source produced which command.

One iteration of the loop:

1. Pull the latest `SEQ_LENGTH=240` temperature values via `data_source.load_temperatures()`.
2. Forecast `LOOKAHEAD` minutes ahead.
3. Compute the desired state with `decide.desired_state(forecast, TARGET_LOW, TARGET_HIGH, LOOKAHEAD)`:
   - if `min(forecast[:LOOKAHEAD]) < TARGET_LOW`, the heater turns on and the cooler turns off;
   - else if `max(forecast[:LOOKAHEAD]) > TARGET_HIGH`, the cooler turns on and the heater turns off;
   - otherwise both off.
4. Diff against the last sent state. Only changes are emitted; the queue is not spammed with identical commands.
5. For each changed (role, command), POST `/api/v1/actuator-command` with `issued_by='machine'`.
6. Sleep `LOOP_SECONDS` and loop.

### Run

```sh
cd API-Rpi16GB/lstm
source .venv/bin/activate
python control_loop.py
```

Stop with Ctrl-C.

### Dev and verification

Three layers of confidence-building, cheapest to most expensive:

1. **Unit tests** for the decision function. No TF, no API, deterministic.

   ```sh
   pytest tests/test_decide.py
   ```

2. **One iteration, no side effects** with `--once --dry-run`. Loads the model, pulls the latest window, prints the forecast band and the would-be POSTs. Doesn't touch the DB. Run from any machine that can reach the API.

   ```sh
   python control_loop.py --once --dry-run
   ```

3. **One iteration, real POSTs** with `--once`. Same as above but actually inserts rows into `actuator_commands`. Inspect with `psql`:

   ```sh
   docker compose exec postgres psql -U postgres -d sensor \
     -c "SELECT id, actuator_id, command, issued_by, issued_at FROM actuator_commands ORDER BY id DESC LIMIT 5;"
   ```

`forecast.py` is the model-side smoke test. After retraining, run it once and look at the plot to confirm the forecast looks plausible before letting the daemon loop.

## Training

Top-of-file knobs in `train.py`:

| Constant | Default | Meaning |
|---|---|---|
| `SEQ_LENGTH` | `240` | Window length in minutes. Must match `forecast.py:SEQ_LENGTH`. |
| `EPOCHS` | `100` | Upper bound. EarlyStopping usually cuts this short. |
| `BATCH_SIZE` | `32` | Minibatch size. |
| `LEARNING_RATE` | `0.001` | Adam learning rate. |
| `TRAIN_TEST_SPLIT` | `0.8` | Chronological 80/20. |

Run:

```sh
python train.py
```

Output sequence:

1. Loads the temperature series from the configured source.
2. Fits a `StandardScaler` on the values.
3. Builds overlapping `(240, 1)` windows with targets one minute ahead.
4. Splits 80/20 in chronological order.
5. Prints `model.summary()` and trains.
6. Writes `data/model.keras` and `data/scaler.npz`.

Typical wall-clock time on Apple Silicon for the default 10000-minute simulated dataset: about 1 to 2 minutes.

## Evaluation

```sh
python evaluate.py
```

Loads the configured source, takes the last 240 values of the training portion as a window, forecasts 30 minutes, compares against the holdout. Prints MAE, RMSE, and a per-minute table. Single-window evaluation, informational only.

## Tests

```sh
pytest tests/
```

Two test files:

- `test_lstm.py`: window builder shapes and alignment, scaler round-trip.
- `test_decide.py`: target-band logic, boundary conditions, dedupe behavior, invalid input handling.

Both run without TensorFlow imports, so they execute in milliseconds.

## Python environment

**Python 3.12 required.** TensorFlow currently ships wheels for Python 3.9 through 3.12 only. Python 3.13 or 3.14 will fail with `Could not find a version that satisfies the requirement tensorflow`. Force the right interpreter when creating the venv:

```sh
cd API-Rpi16GB/lstm
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Apple Silicon, `tensorflow` installs natively.

## Tuning notes

Defaults reflect what produced useful output on the simulated source.

### `SEQ_LENGTH = 240`

Slide 5-29 uses 40, which works for real sensor data with strong short-term autocorrelation. The simulated source has a 180-minute short cycle, so a 40-minute window never contains a full cycle and the model collapses to "current value plus tiny delta". 240 covers more than one full short cycle and lets the model learn the periodicity.

### `ALPHA = 0.0` in `forecast.py`

Slide 5-35 suggests `alpha = 0.2`. With recursive feedback that smoothing compounds and damps the trajectory toward a flat line within a few steps. `ALPHA = 0` gives the model room to express the learned dynamics. Bump to 0.05 if you see single-step spikes.

### Default forecast horizon = 240 minutes in `forecast.py`

Slide 5-5 motivates a 30-minute control horizon, which is what `LOOKAHEAD` uses in the control loop. 240 minutes is only the default for the smoke-test forecast plot, where a longer horizon makes the chart more informative.

### Recursive prediction degrades over long horizons

The model is trained 1-step ahead and forecasts by feeding predictions back. Errors accumulate. Sharp peak/trough tracking past 240 minutes is not realistic. For tighter long horizons, switch to direct multi-step output. Deviates from the slide design but produces sharper results.

## Limits and known issues

- **Effective API training window is 7 days** because of the archiver.
- **Image is heavy.** Full TensorFlow on `python:3.12-slim` lands around 1.3GB. Acceptable on the 16GB Pi but the first build takes 5-10 minutes.
- **Single-window `evaluate.py`** is informational, not a rolling backtest.
- **No automatic retraining**. Operator runs `python train.py` manually on a workstation, commits the new artifacts, and rebuilds the Pi image.

## Production deployment on the 16GB Pi

The control loop runs as a Docker service alongside `postgres`, `backend`, `nginx`, `controller`, `mosquitto`, `archiver`, `webserver`. It pulls sensor data from the backend over the internal compose network and posts actuator commands the same way the dashboard does.

### Service definition

`docker/lstm/Dockerfile`:

```Dockerfile
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY lstm/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY lstm/ ./
CMD ["python", "control_loop.py"]
```

`docker/docker-compose.yml` (excerpt):

```yaml
lstm:
  build:
    context: ..
    dockerfile: docker/lstm/Dockerfile
  restart: unless-stopped
  environment:
    API_BASE_URL: https://www.lab.local
    API_CA_CERT: /run/secrets/ca_cert
    KEYCLOAK_TOKEN_URL: http://keycloak:8080/auth/realms/iot/protocol/openid-connect/token
    LSTM_CLIENT_ID: lstm-client
    LSTM_CLIENT_SECRET_FILE: /run/secrets/keycloak_lstm_secret
    DATA_SOURCE: api
    TARGET_LOW: "19.0"
    TARGET_HIGH: "23.0"
    LOOKAHEAD: "30"
    LOOP_SECONDS: "60"
    ACTUATOR_HEATER_ID: heater01
    ACTUATOR_COOLER_ID: cooler01
  secrets:
    - ca_cert
    - keycloak_lstm_secret
  networks:
    - app-net
  depends_on:
    nginx:
      condition: service_healthy
```

Three compose-level details make this work:

- The `keycloak_lstm_secret` secret is declared at the top of `docker-compose.yml` pointing at `./secrets/keycloak_lstm_secret.txt`. The file is gitignored; populate it on the Pi after bringing Keycloak up by copying the secret out of the admin console.
- The `nginx` service has a network alias `www.lab.local` so the TLS hostname matches the cert CN. The `lstm` service hits `https://www.lab.local`, Docker DNS resolves it to the nginx container, and certificate verification against `ca_cert` passes. The token endpoint goes via `http://keycloak:8080` over the internal compose network (no TLS); the access token then travels over TLS to the backend.
- The `nginx` service has a TCP healthcheck (`nc -z 127.0.0.1 8443`), and `lstm` waits for `condition: service_healthy` before starting. This avoids spurious failures on `docker compose up` from racing nginx's TLS init. The daemon's per-iteration `try/except` is still there as a backstop for transient network blips later, but cold-start races are no longer one of them.

### First-time setup on the Pi

1. Train on a workstation: `cd lstm && python train.py`. This writes `data/model.keras` and `data/scaler.npz`.
2. Commit and push: `git add lstm/data/model.keras lstm/data/scaler.npz && git commit -m "chore(lstm): retrain artifacts" && git push`.
3. On the Pi: `git pull`.
4. Set the Keycloak client secret. After Keycloak comes up and imports `iot-realm.json`, open the admin console (`https://www.lab.local/auth/admin/`), open the `lstm-client` client, copy the generated secret from the Credentials tab, and put it in the secret file:

   ```sh
   echo "<keycloak lstm-client secret>" > docker/secrets/keycloak_lstm_secret.txt
   ```

5. Build the image: `cd docker && docker compose build lstm`. First build is slow because the TensorFlow wheel is large.
6. Smoke test, dry run: `docker compose run --rm lstm python control_loop.py --once --dry-run`. Logs should show a forecast band and either `DRY-RUN would POST` or `no change, nothing to send`.
7. Smoke test, real POST: `docker compose run --rm lstm python control_loop.py --once`. Verify the row landed:

   ```sh
   docker compose exec postgres psql -U postgres -d sensor -c "SELECT id, actuator_id, command, issued_by, issued_at FROM actuator_commands ORDER BY id DESC LIMIT 5;"
   ```

8. Bring up the daemon: `docker compose up -d lstm`. Watch with `docker compose logs -f lstm`.

### Day-to-day operations

All commands run from `~/API-Rpi16GB/docker` on the 16GB Pi.

**Start, stop, restart the daemon.**

```sh
docker compose up -d lstm        # start (or recreate if config changed)
docker compose stop lstm         # stop, container stays
docker compose start lstm        # start a stopped container
docker compose restart lstm      # quick restart
```

**Watch logs.** Each iteration prints a `forecast …` line and either a `sent: …` / `DRY-RUN …` line or `no change, nothing to send`.

```sh
docker compose logs -f lstm           # tail live
docker compose logs --tail 100 lstm   # last 100 lines, no follow
```

**Run one iteration on demand** (handy for sanity-checking after a code or model change):

```sh
docker compose run --rm lstm python control_loop.py --once --dry-run   # no DB writes
docker compose run --rm lstm python control_loop.py --once             # real POST
```

**See what commands the LSTM has issued.** Filter by `issued_by='machine'` to ignore manual dashboard commands.

```sh
docker compose exec postgres psql -U postgres -d sensor -c "SELECT id, actuator_id, command, issued_at FROM actuator_commands WHERE issued_by='machine' ORDER BY id DESC LIMIT 20;"
```

**See what data is in the forecast window.** The control loop pulls the latest 240 rows where `unit='C'`, mixing all sensor IDs. Use this to check the real-sensor vs synthetic-data ratio.

```sh
docker compose exec postgres psql -U postgres -d sensor -c "SELECT sensor_id, COUNT(*) FROM (SELECT sensor_id FROM sensor_data WHERE unit='C' ORDER BY recorded_at DESC LIMIT 240) s GROUP BY sensor_id;"
```

**Wipe seeded demo rows.** Demo rows from the seeder are tagged `sensor_id LIKE 'demo-%'` and are safe to delete once the real Pico has produced enough history (~240 minutes at 1 row/min).

```sh
docker compose exec postgres psql -U postgres -d sensor -c "DELETE FROM sensor_data WHERE sensor_id LIKE 'demo-%'; DELETE FROM sensor_data_archive WHERE sensor_id LIKE 'demo-%';"
```

**Reseed synthetic data** if you blew away the real data or want to test with a known signal. Run the seeder via the `tools` compose profile.

```sh
docker compose --profile tools run --rm seeder --rows 10080 --days 7
```

Density matters: the model was trained on 1-minute-spaced samples, so seed at roughly 1 row per minute (10080 rows / 7 days). Denser seeding compresses the 240-row window into a shorter wall-clock span and the forecast degrades.

### Retrain and redeploy

```sh
# on workstation
cd lstm && python train.py
git add data/model.keras data/scaler.npz
git commit -m "chore(lstm): retrain on N days of data"
git push

# on Pi
git pull
cd docker
docker compose build lstm
docker compose up -d lstm
```

### Resource notes

The `python:3.12-slim` base plus TensorFlow lands around 1.3GB on disk. RAM at runtime is roughly 300-500MB. The 16GB Pi has the headroom; the build itself is the slow part (5-10 minutes on the Pi).

## Reference

Mapping from the project script to this implementation:

| Slide | Concept | Where it lives |
|---|---|---|
| 5-3, 5-4 | Project milestone, controller buffer, warm start | `control_loop.py` (dedupe by last_sent state) |
| 5-15 to 5-21 | LSTM cell math, gates, dual stack | `train.py:build_model` |
| 5-29, 5-30 | Keras model definition, Adam, MSE, dropout | `train.py:build_model`, `train.py:main` |
| 5-31 | Two-sine-wave temperature simulation | `data_source.py:_simulate` |
| 5-32, 5-33 | StandardScaler, `create_sequences`, 80/20 split | `train.py:create_sequences`, `train.py:main` |
| 5-34 | `model.fit`, EarlyStopping, ReduceLROnPlateau | `train.py:main` |
| 5-35 | Recursive forecast with alpha smoothing | `forecast.py:forecast` |
| 5-37 to 5-39 | Relay wiring, actor control via MQTT | `controller.py` (drain) + `control_loop.py` (decide) |
