# End-to-end test matrix

Test plan for the `integration/phase-6` → `main` sign-off PR. Every
scenario must pass against the running stack on the 16GB Pi before the
PR is merged. Run order is top-down: positive paths first, negative
paths after, token-lifecycle last.

The scenarios mirror the punch list in `INTEGRATION-TODOS.md`
(section "End-to-end test matrix"). When this file lands, that bullet
in `INTEGRATION-TODOS.md` can point here instead of repeating itself.

## Prerequisites

- Stack up on the 16GB Pi: `cd docker && docker compose ps` shows every
  service `healthy` (postgres, mosquitto, nginx, backend, keycloak,
  keycloak-db, controller, lstm, grafana, prometheus, archiver,
  seeder profile available).
- DNS: client machine resolves `www.lab.local` to the Pi
  (`192.168.50.92` via the 2GB router on Production WLAN, or
  `192.168.2.2` via the direct-ethernet dev link from the Mac).
- CA trust: `lab-ca.crt` from `docker/secrets/ca_cert.txt` installed
  in the client's trust store.
- DB pre-seeded with at least 240 minutes of sensor rows so the LSTM
  control loop has its window:
  `docker compose --profile tools run --rm seeder`.
- `iotuser01` / `Test1234!` exists in the `iot` realm (default from
  the imported `iot-realm.json`).
- A short-exp test realm clone or an `iotuser01` token with a forced
  short `exp` for scenario 6 (see "Token expiry" below).

## Scenarios

### 1. Dashboard login + sensor read (browser, dashboard-client)

**Goal:** verify the OIDC authorization-code + PKCE flow against
Keycloak through nginx, then a JWT-authenticated `GET
/api/v1/sensor-data` from the dashboard.

**Steps:**

1. Open `https://www.lab.local/` in a browser with the CA trusted.
2. Click login. Expect a redirect to
   `https://www.lab.local/auth/realms/iot/protocol/openid-connect/auth?...`.
3. Submit `iotuser01` / `Test1234!`. Expect a redirect back to the
   dashboard with the access token in the SPA's memory store
   (`keycloak-js@26.2.4` vendored at `docker/dashboard/script/keycloak.js`).
4. Confirm the dashboard renders the latest sensor reading without a
   visible error toast.
5. Inspect the network tab: the `GET /api/v1/sensor-data` request
   must carry `Authorization: Bearer eyJ...` and return `200` with a
   JSON array.

**Pass criteria:** login completes, dashboard shows live data,
network tab shows `200` on the API call with a Bearer token.

### 2. Controller ingest (service-to-service, controller-client)

**Goal:** verify the controller's client-credentials token works
against `POST /api/v1/sensor-data`.

**Steps:**

1. From inside the compose network (e.g. `docker compose exec
   controller sh`), request a token from Keycloak via the same path
   the controller uses in production:
   ```
   curl -sS --cacert /run/secrets/ca_cert \
     -d grant_type=client_credentials \
     -d client_id=controller-client \
     -d client_secret=sc_controller_client \
     https://www.lab.local/auth/realms/iot/protocol/openid-connect/token
   ```
2. Capture the `access_token` field.
3. POST a synthetic reading with the captured token:
   ```
   curl -sS --cacert /run/secrets/ca_cert \
     -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"sensor_id":"sensor01","value":21.5,"unit":"C"}' \
     https://www.lab.local/api/v1/sensor-data
   ```

**Pass criteria:** the POST returns `200` (or `201`). A new row appears
in the `sensor_data` table for `sensor01` within one query
(`docker compose exec postgres psql -U iot_read_user -d iot
-c 'select * from sensor_data order by id desc limit 1;'`).

### 3. LSTM actuator command (service-to-service, lstm-client)

**Goal:** verify the LSTM's client-credentials token works against
`POST /api/v1/actuator-command` with the wire-command vocabulary the
Pico expects.

**Steps:**

1. From inside the compose network, fetch a token for `lstm-client`
   (same call as scenario 2 but with `client_id=lstm-client` and
   `client_secret=sc_lstm_client`).
2. POST one of the four valid wire commands (`HEAT_ON`, `HEAT_OFF`,
   `FAN_ON`, `FAN_OFF`). The backend derives `issued_by` from the
   verified audience, so the request body has no `issued_by` field:
   ```
   curl -sS --cacert /run/secrets/ca_cert \
     -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"actuator_id":"heater01","command":"HEAT_OFF"}' \
     https://www.lab.local/api/v1/actuator-command
   ```

**Pass criteria:** POST returns `200`/`201`, a new row appears in
`actuator_commands` with `issued_by='machine'` (from the lstm-client
token), the controller picks it up on the next 2-second drain poll,
mosquitto delivers it to the Pico, and the Pico relay clicks. Audible
confirmation is enough.

### 4. Missing token → 401

**Goal:** verify the auth chain rejects unauthenticated calls.

**Steps:**

1. Repeat scenarios 2 and 3 above, but drop the `Authorization`
   header entirely.
2. Repeat scenario 1's API call (`GET /api/v1/sensor-data`) the same
   way.

**Pass criteria:** each call returns `401 Unauthorized` with body
`{"error":"missing authorization header"}`. The nginx access log
shows the request reached the backend (not the WAF), and no row is
written to any DB table.

### 5. Wrong audience or role → 403

**Goal:** verify cross-client tokens are rejected by the audience
and role check.

**Steps:**

1. Fetch a `dashboard-client` token (use Keycloak's password grant
   for `iotuser01` to keep this self-contained).
2. POST that token to `/api/v1/sensor-data` (controller route).
   Expected: `403`.
3. POST that token to `/api/v1/actuator-command` (LSTM route).
   Expected: `403`.
4. Fetch a `controller-client` token and POST it to
   `/api/v1/actuator-command`. Expected: `403` (wrong audience).
5. Special case: `GET /api/v1/sensor-data` accepts either
   `dashboard-client` or `lstm-client` tokens (the one multi-policy
   route in the backend, see `docs/backend/api.md`). Both should
   return `200`; a `controller-client` token to the same GET should
   return `403`.

**Pass criteria:** every wrong-audience case returns `403
{"error":"forbidden"}`. The dual-policy GET behaves as documented.

### 6. Token expiry + refresh

**Goal:** verify the controller and LSTM ApiClients recover from a
short-lived token via the 401-then-refresh-then-retry path.

**Steps:**

Either temporarily shrink the access-token lifespan on the `iot`
realm to `30s` via the Keycloak admin UI (Realm settings → Tokens →
Access Token Lifespan), or fetch a short-exp token by hand and pin
it into the service:

1. Watch `lstm` or `controller` logs (`docker compose logs -f
   --tail=0 lstm`).
2. Trigger a loop iteration that runs past the 30s expiry. The
   ApiClient should see a `401`, request a fresh token, and retry
   the original call once.
3. Confirm in logs: one `401` line, one token-refresh line, one
   `200` line. No retry loop, no crash, no double-send to the
   actuator queue.

**Pass criteria:** the loop continues without operator intervention.
Metrics `lstm_iterations_total{outcome="success"}` keeps climbing.

## Sign-off

All six scenarios must pass before the `integration/phase-6` → `main`
PR is merged. Record the run date and the integration tip SHA in the
PR description so the result is reproducible.
