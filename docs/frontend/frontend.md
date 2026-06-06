# Frontend Dashboard

Browser-based dashboard for the IoT sensor system. Static files (HTML, CSS, JS, favicon) are served directly by the `nginx` reverse proxy container from a bind mount of `docker/dashboard/`. Authenticates via Keycloak OIDC and visualises temperature and humidity readings from the Zig backend API.

## What is done

| Item | Status |
|---|---|
| `docker/dashboard/` directory | done |
| `index.html` — login state, logout button, chart, time picker | done |
| KPI cards (temperature, humidity, heater + cooler buttons, combined system + monitoring card) | done |
| Actuator toggle buttons — heater01 and cooler01, state-aware label, 5-second reconciliation poll | done |
| Readings table — last 10 sensor rows, newest first | done |
| Footer — lab name, Grafana and Prometheus links, copyright | done |
| `favicon.svg` (+ legacy `favicon.ico` reference) | done |
| `style/style.css` | done |
| `script/frontend.js` — Keycloak init, login flow, fetch, chart, live mode, 401/403 handling | done |

## Backend contract

The dashboard fetches `GET https://www.lab.local/api/v1/sensor-data?from=<iso>&to=<iso>` with an `Authorization: Bearer <token>` header. This endpoint lives in the Zig backend (`src/handlers/sensor.zig`, routed in `src/router.zig`). nginx routes `/api/` to the Zig backend service.

The endpoint requires:
- Audience: `dashboard-client`
- Realm role: `dashboard-user`
- Query window via `from` / `to` ISO 8601 timestamps (either side may be omitted)

Response is a JSON array of `{ id, sensor_id, value, unit, recorded_at }` rows. The frontend's `normalizePayload()` groups two rows per timestamp (temperature + humidity) back together client-side.

## File layout

```
docker/dashboard/
  index.html              Single-page app shell
  favicon.svg
  favicon.ico             (0-byte legacy file, referenced as alternate icon)
  style/
    style.css             All custom styles (CSS variables, navbar, chart wrapper, …)
  script/
    frontend.js           Dashboard logic (Keycloak + real API)
```

## Constants in frontend.js

| Constant | Value | Purpose |
|---|---|---|
| `KEYCLOAK_CONFIG.url` | `https://www.lab.local/auth` | Keycloak base URL |
| `KEYCLOAK_CONFIG.realm` | `iot` | Realm name |
| `KEYCLOAK_CONFIG.clientId` | `dashboard-client` | OIDC public client |
| `API_BASE` | `https://www.lab.local` | Backend base URL (through nginx) |
| `SENSOR_DATA_ENDPOINT` | `${API_BASE}/api/v1/sensor-data` | Sensor data endpoint |
| `ACTUATOR_COMMAND_ENDPOINT` | `${API_BASE}/api/v1/actuator-command` | POST target for heater / cooler toggle |
| `ACTUATOR_STATES_ENDPOINT` | `${API_BASE}/api/v1/actuator-states` | GET target for current on/off state of each actuator |
| `LIVE_POLL_INTERVAL_MS` | `60000` | How often the live poller refetches sensor data (ms) |
| `ACTUATOR_POLL_INTERVAL_MS` | `5000` | How often the actuator state poller reconciles button labels (ms) |
| `TOKEN_MIN_VALIDITY_SECONDS` | `70` | Minimum remaining token lifetime before a proactive refresh |
| `TOKEN_REFRESH_INTERVAL_MS` | `60000` | How often the background refresh check runs (ms) |

All constants are at the top of `frontend.js` and can be changed without touching any logic.

## Layout

The page is divided into three visual rows:

1. **KPI row** — five cards across the top:
   - Temperature (latest value, °C)
   - Humidity (latest value, %)
   - Heater (state pill + toggle button → `heater01`, wire commands `HEAT_ON` / `HEAT_OFF`)
   - Cooler (state pill + toggle button → `cooler01`, wire commands `FAN_ON` / `FAN_OFF`)
   - Combined system + monitoring card (status badge, last-update timestamp, Grafana and Prometheus shortcuts)

2. **Filter + chart row** — date-range controls on the left, Chart.js
   time-series chart on the right with a LIVE badge when live mode is active.

3. **Readings table** — last 10 sensor rows newest-first, with a row-count
   badge in the header.

A footer at the bottom links to Grafana and Prometheus.

## DOM element IDs (index.html ↔ frontend.js contract)

| ID | Element | Used for |
|---|---|---|
| `loading-state` | spinner div | Shown during Keycloak init, hidden on success |
| `app-container` | main div | Hidden during init, shown after login |
| `username` | `<span>` | Filled with `preferred_username` from the token |
| `logout-btn` | `<button>` | Triggers `keycloak.logout()` |
| `refresh-btn` | `<button>` | Triggers `loadData()` |
| `date-from` | `<input datetime-local>` | Start of the query window |
| `date-to` | `<input datetime-local>` | End of the query window (empty = live mode) |
| `live-badge` | badge div | Shown when live polling is active |
| `error-message` | error div | Container for API / auth error messages |
| `error-text` | `<span>` inside above | The human-readable error string |
| `iotChart` | `<canvas>` | Chart.js render target |
| `kpi-temp` | `<span>` | Latest temperature value (KPI card) |
| `kpi-hum` | `<span>` | Latest humidity value (KPI card) |
| `status-text` | `<div>` | System status description (KPI status card) |
| `status-time` | `<div>` | Timestamp of last status update |
| `status-badge` | `<span>` | Online / Offline pill badge |
| `readings-tbody` | `<tbody>` | Last 10 readings table body |
| `table-count` | `<span>` | Row count badge next to the table header |
| `.actuator-card[data-actuator="<id>"]` | `<div>` | One card per actuator; the `data-actuator` attribute carries the actuator id |
| `.actuator-state` (inside an actuator card) | `<span>` | Pill that reads `EIN` / `AUS` / `?` based on the last dispatched command |
| `.actuator-toggle` (inside an actuator card) | `<button>` | Sends the opposite of the current state; uses `data-on-cmd` / `data-off-cmd` for the wire vocabulary |
| `.actuator-icon` (inside an actuator card) | `<i>` | Picks up the `actuator-icon-active` class when the relay is on |

## Authentication flow

```
Browser → Keycloak login page
       ← authorization code (PKCE S256)
Browser → Keycloak token endpoint
       ← access token + refresh token
frontend.js checks realm role 'dashboard-user'
  → missing role: fatal error, no data loaded
  → role present: hide spinner, show app, start polling
```

Token auto-refresh runs every `TOKEN_REFRESH_INTERVAL_MS`. If `keycloak.updateToken()` fails (refresh token expired), the user is redirected to login again.

## API response formats

The frontend's `normalizePayload()` function accepts two shapes:

**Grouped** (preferred, returned when `?format=grouped`):
```json
[
  { "timestamp": "2025-05-21T18:00:00Z", "temperature": 21.3, "humidity": 47.1 },
  ...
]
```

**Raw** (fallback, flat DB rows):
```json
[
  { "recorded_at": "2025-05-21T18:00:00Z", "sensor_id": "sensor01_temperature", "value": 21.3, "unit": "C" },
  { "recorded_at": "2025-05-21T18:00:00Z", "sensor_id": "sensor01_humidity",    "value": 47.1, "unit": "%" },
  ...
]
```

The server can also wrap either shape in an envelope:
```json
{ "data": [ ... ] }
```
or
```json
{ "rows": [ ... ] }
```

`normalizePayload()` unwraps all of these automatically.

## Error handling

| HTTP status | Behaviour |
|---|---|
| Network error | `showError("Netzwerkfehler…")` |
| `401` | `keycloak.login()` — forces a fresh authentication round-trip |
| `403` | `showError(…)` — tells the user they lack the required role |
| Other non-2xx | `showError("Server-Fehler ${status}…")` |

## Chart

Two datasets on a shared time axis (`chartjs-adapter-date-fns`):

| Dataset | Colour | Y-axis | Position |
|---|---|---|---|
| Temperatur (°C) | `#0d6efd` (Bootstrap blue) | `yTemp` | left |
| Luftfeuchte (%) | `#fd7e14` (Bootstrap orange) | `yHum` | right, 0–100 fixed |

CSS variables in `style.css` mirror these colours for any non-chart uses:
```css
--color-temp:     #0d6efd;
--color-humidity: #fd7e14;
```

## Live mode

Live mode activates when the `date-to` field is empty. The dashboard:

1. Anchors `liveSinceIso` to the `date-from` value at activation time.
2. Calls `fetchAndRender(liveSinceIso, null)` once immediately.
3. Starts an interval that repeats the same call every `LIVE_POLL_INTERVAL_MS` (60 s).

Live mode stops when the user sets a `date-to` value or logs out.

## Actuator buttons

The heater and cooler cards in the KPI row let an operator override the LSTM's closed-loop control directly from the browser. Each card has the same anatomy: a state pill that reads `EIN` / `AUS` / `?`, an icon that picks up a pulse animation while the relay is on, and a single toggle button whose label flips between `Einschalten` and `Ausschalten` based on the known state.

The `data-actuator`, `data-on-cmd`, and `data-off-cmd` attributes on each button carry everything the click handler needs, so adding a third actuator means dropping in another card with the same template — no JS changes.

**Reconciliation loop**

```
initApp           → fetchActuatorStates() once, then start poller
ACTUATOR_POLL     → fetchActuatorStates() every 5 s
button click      → POST /api/v1/actuator-command (issued_by="user")
                  → render optimistically (button reflects new state immediately)
                  → next poll picks up sent_at and confirms / corrects
```

`fetchActuatorStates()` calls `GET /api/v1/actuator-states`, which returns the latest *dispatched* command per actuator. The dashboard maps each known wire command (`HEAT_ON`, `HEAT_OFF`, `FAN_ON`, `FAN_OFF`) to a boolean via the `WIRE_COMMAND_TO_ON` table; any unknown command falls back to `?` until the next poll. Actuators that have never had a command issued show up as `?` and are still clickable — the first click sends `*_ON`.

**Optimistic update + reconciliation**

A click POSTs the new command but the controller has not yet republished it to MQTT, so a state poll fired right after would still return the previous command. To keep the UI snappy the click handler updates `actuatorState[actuator_id]` and re-renders the card immediately, and the next 5-second poll either confirms (the controller has acked, `sent_at` is now non-null) or quietly corrects the UI if the row was rejected by the controller's regex guard (see `docker/controller/controller.py`).

