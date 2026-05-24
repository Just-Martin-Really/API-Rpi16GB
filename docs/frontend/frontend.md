# Frontend Dashboard

Browser-based dashboard for the IoT sensor system. Served as static files by the `webserver` container (nginx + Node.js). Authenticates via Keycloak OIDC and visualises temperature and humidity readings from the backend API.

## What is done

| Item | Status |
|---|---|
| `docker/webserver/public/` directory | done |
| `index.html` — login state, logout button, chart, time picker | done |
| `favicon.ico` | done |
| `style/style.css` | done |
| `script/frontend.js` — Keycloak init, login flow, fetch, chart, live mode, 401/403 handling | done |
| `script/preview.js` — static mock for UI development (no server needed) | done |

## What is still open

`GET /api/sensordata` in `server.js` is stubbed. The route exists but the handler body is empty. It must:

1. Parse and validate `from` / `to` ISO query params.
2. Connect as `db_read_user` (not `db_write_user` — least privilege).
3. Run `SELECT recorded_at, sensor_id, value, unit FROM sensor_data WHERE recorded_at BETWEEN $1 AND $2 ORDER BY recorded_at`.
4. Return either the raw rows or a grouped format (`[{ timestamp, temperature, humidity }]`) based on the `?format=grouped` query param.
5. Validate the bearer JWT before touching the DB (`authenticateToken` middleware, which is also missing).

Until that endpoint exists, the dashboard cannot load real data. The frontend itself requires no further changes.

## File layout

```
docker/webserver/public/
  index.html              Single-page app shell
  favicon.ico
  style/
    style.css             All custom styles (CSS variables, navbar, chart wrapper, …)
  script/
    frontend.js           Production dashboard (Keycloak + real API)
    preview.js            Dev-only mock (no login, no API, browser-generated data)
```

## Switching between preview and production mode

`index.html` currently loads the mock:

```html
<!-- production (comment out for local UI work) -->
<!-- <script src="https://www.lab.local/auth/js/keycloak.js"></script> -->
<!-- <script src="script/frontend.js"></script> -->

<!-- preview (remove before going live) -->
<script src="script/preview.js"></script>
```

To activate the real dashboard, swap the comments:

```html
<script src="https://www.lab.local/auth/js/keycloak.js"></script>
<script src="script/frontend.js"></script>
```

`preview.js` must be removed or its `<script>` tag deleted. Both scripts register a Chart.js instance on the same canvas — loading both breaks the page.

## Constants in frontend.js

| Constant | Value | Purpose |
|---|---|---|
| `KEYCLOAK_CONFIG.url` | `https://www.lab.local/auth` | Keycloak base URL |
| `KEYCLOAK_CONFIG.realm` | `iot` | Realm name |
| `KEYCLOAK_CONFIG.clientId` | `dashboard-client` | OIDC public client |
| `API_BASE` | `https://www.lab.local` | Backend base URL (through nginx) |
| `SENSOR_DATA_ENDPOINT` | `${API_BASE}/api/sensordata` | Sensor data endpoint |
| `LIVE_POLL_INTERVAL_MS` | `60000` | How often the live poller refetches (ms) |
| `TOKEN_MIN_VALIDITY_SECONDS` | `70` | Minimum remaining token lifetime before a proactive refresh |
| `TOKEN_REFRESH_INTERVAL_MS` | `60000` | How often the background refresh check runs (ms) |

All constants are at the top of `frontend.js` and can be changed without touching any logic.

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

