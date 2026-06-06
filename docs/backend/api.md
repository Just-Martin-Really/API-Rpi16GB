# API Reference

Base URL: `https://www.lab.local`

All endpoints return `application/json`. All `/api/v1/` endpoints require a valid RS256 access token from Keycloak in the `Authorization: Bearer <token>` header. Each endpoint additionally requires a specific token audience and realm role; the matrix is at the bottom of this page.

The backend itself does not issue tokens. Clients obtain access tokens directly from Keycloak via the OAuth2 / OIDC flows configured for the `iot` realm:

- Browser dashboard: Authorization Code Flow against the public client `dashboard-client`.
- Controller (Python service): Client Credentials Flow as the confidential client `controller-client`.
- LSTM service: Client Credentials Flow as the confidential client `lstm-client`.

---

## GET /health

Returns the server liveness status. No auth required.

**Response 200**
```json
{"status": "ok"}
```

---

## GET /api/v1/sensor-data

Returns sensor readings, newest first. Accepts optional `from` and `to` query parameters (ISO-8601 timestamps) to filter `recorded_at`. Without parameters, returns all rows.

**Required audience:** `dashboard-client`
**Required role:** `dashboard-user`

**Examples**

```
GET /api/v1/sensor-data
GET /api/v1/sensor-data?from=2026-05-20T00:00:00Z
GET /api/v1/sensor-data?from=2026-05-20T00:00:00Z&to=2026-05-21T00:00:00Z
```

**Response 200**
```json
[
  {
    "id": 1,
    "sensor_id": "sensor01",
    "value": 23.4,
    "unit": "°C",
    "recorded_at": "2026-04-24T10:00:00Z"
  }
]
```

**Response 400** when `from` or `to` is malformed:
```json
{"error": "invalid from/to"}
```

---

## POST /api/v1/sensor-data

Inserts a new sensor reading. Used by the controller service.

**Required audience:** `controller-client`
**Required role:** `controller-ingest`

**Request body**
```json
{
  "sensor_id": "sensor01",
  "value": 23.4,
  "unit": "°C"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `sensor_id` | string | yes | max 64 chars |
| `value` | number | yes | double precision |
| `unit` | string | yes | max 16 chars |

**Response 201**
```json
{"created": true}
```

**Response 400** when fields are missing or invalid:
```json
{"error": "invalid json"}
```

---

## POST /api/v1/actuator-command

Queues a command for an actuator. The controller service picks it up within ~2 seconds and publishes it to the actuator's MQTT topic (`<actuator_id>/data`).

**Required audience:** `lstm-client` or `dashboard-client`
**Required role:** `lstm-control` or `dashboard-user`

The LSTM control loop uses this endpoint in its closed-loop forecast workflow; the dashboard uses it for manual operator overrides via the heater and cooler buttons. The `issued_by` field distinguishes the source in the audit trail.

**Request body**
```json
{
  "actuator_id": "actuator01",
  "command": "FAN_ON"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `actuator_id` | string | yes | max 64 chars, must match `^[A-Za-z0-9_-]+$` to be dispatched by the controller |
| `command` | string | yes | max 64 chars, must match `^[A-Z0-9_]+$` to be dispatched; current commands: `FAN_ON`, `FAN_OFF`, `HEAT_ON`, `HEAT_OFF`, `on`, `off` |

`issued_by` is derived from the verified audience — `lstm-client` records `"machine"`, `dashboard-client` records `"user"`. Any `issued_by` field in the request body is ignored, so a dashboard token cannot pollute the audit trail by claiming machine origin.

Rows whose `actuator_id` or `command` fail the regex are accepted by the API but skipped (and marked sent) by the controller to prevent MQTT topic injection from DB content.

**Response 201**
```json
{"queued": true}
```

**Response 400** when fields are missing or invalid:
```json
{"error": "invalid json"}
```

---

## POST /api/v1/sensor-request

Queues a request for a sensor to publish a fresh reading. The controller service picks it up within ~2 seconds and publishes it to the sensor's MQTT topic (`<sensor_id>/request`). The Pico, on receiving `READ_NOW`, reads the DHT22 and publishes the values on `<sensor_id>/data` as usual.

Used by the dashboard or a watchdog to force a fresh reading when periodic data has not arrived within its expected cadence. The request itself does not wait for the reading; the caller polls `GET /api/v1/sensor-data` for the new row, or watches the broker.

**Required audience:** `dashboard-client`
**Required role:** `dashboard-user`

**Request body**
```json
{
  "sensor_id": "sensor01",
  "command": "READ_NOW"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `sensor_id` | string | yes | max 64 chars, must match `^[A-Za-z0-9_-]+$` to be dispatched by the controller |
| `command` | string | yes | max 64 chars, must match `^[A-Z0-9_]+$` to be dispatched; current commands: `READ_NOW` |

Rows whose `sensor_id` or `command` fail the regex are accepted by the API but skipped (and marked sent) by the controller to prevent MQTT topic injection from DB content.

**Response 201**
```json
{"queued": true}
```

**Response 400** when fields are missing or invalid:
```json
{"error": "invalid json"}
```

See [Sensor Request Flow](sensor-request-flow.md) for the end-to-end path and the emergency-shutdown pattern this endpoint enables.

---

## GET /api/v1/actuator-commands

Returns up to 100 unsent rows from `actuator_commands`, oldest first. Polled by the controller every 2 seconds; each returned row is published to MQTT and then acknowledged via `POST /api/v1/actuator-commands/sent`.

**Required audience:** `controller-client`
**Required role:** `controller-ingest`

**Response 200**
```json
{
  "commands": [
    {"id": 42, "actuator_id": "heater01", "command": "HEAT_ON"}
  ]
}
```

The list is empty when no rows have `sent_at IS NULL`.

---

## POST /api/v1/actuator-commands/sent

Marks a previously fetched row as dispatched (`sent_at = NOW()`). Idempotent: re-acking an already-sent row returns `updated: 0`.

**Required audience:** `controller-client`
**Required role:** `controller-ingest`

**Request body**
```json
{"id": 42}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | integer | yes | positive; primary key of the `actuator_commands` row |

**Response 200**
```json
{"updated": 1}
```

**Response 400** when `id` is missing, non-numeric, or non-positive:
```json
{"error": "id must be a positive integer"}
```

---

## GET /api/v1/actuator-states

Returns the latest dispatched command per actuator so a UI can render the current on/off state of each relay without polling MQTT directly. Rows whose `sent_at IS NULL` are excluded — those are queued but not yet on the wire, so they do not represent the live bus state.

**Required audience:** `dashboard-client` or `lstm-client`
**Required role:** `dashboard-user` or `lstm-control`

**Response 200**
```json
{
  "actuators": [
    {"actuator_id": "cooler01", "command": "FAN_OFF", "sent_at": "2026-06-06 12:00:42.123+00"},
    {"actuator_id": "heater01", "command": "HEAT_ON", "sent_at": "2026-06-06 12:01:11.987+00"}
  ]
}
```

The list is empty if no actuator has ever had a dispatched command. The shape of `sent_at` is the Postgres text representation of `TIMESTAMPTZ`.

The query uses `SELECT DISTINCT ON (actuator_id) ... ORDER BY actuator_id, sent_at DESC`, so each actuator appears at most once with its most recent dispatched command.

---

## GET /api/v1/sensor-requests

Returns up to 100 unsent rows from `sensor_requests`, oldest first. Polled by the controller every 2 seconds; each returned row is published to MQTT and then acknowledged via `POST /api/v1/sensor-requests/sent`.

**Required audience:** `controller-client`
**Required role:** `controller-ingest`

**Response 200**
```json
{
  "requests": [
    {"id": 17, "sensor_id": "sensor01", "command": "READ_NOW"}
  ]
}
```

---

## POST /api/v1/sensor-requests/sent

Marks a previously fetched sensor-request row as dispatched. Idempotent.

**Required audience:** `controller-client`
**Required role:** `controller-ingest`

**Request body**
```json
{"id": 17}
```

**Response 200**
```json
{"updated": 1}
```

---

## Authentication errors

| Status | When |
|--------|------|
| 401 Unauthorized | `Authorization` header is missing or does not start with `Bearer `. |
| 403 Forbidden | Signature does not verify against any cached JWKS key (after one refresh attempt), token is expired, issuer does not match `KEYCLOAK_ISSUER`, audience does not match the route's required `aud` / `azp`, or the required realm role is absent from `realm_access.roles`. |

The body for both is `{"error":"forbidden"}` (or `{"error":"missing authorization header"}` for 401). Token contents are never echoed back or logged.

---

## Route policy matrix

| Method | Path | Required audience | Required role | DB pool |
|---|---|---|---|---|
| GET  | `/api/v1/sensor-data`            | `dashboard-client` **or** `lstm-client` | `dashboard-user` **or** `lstm-control` | `iot_read_user`  |
| POST | `/api/v1/sensor-data`            | `controller-client` | `controller-ingest` | `iot_write_user` |
| POST | `/api/v1/actuator-command`       | `lstm-client` **or** `dashboard-client` | `lstm-control` **or** `dashboard-user` | `iot_write_user` |
| GET  | `/api/v1/actuator-commands`      | `controller-client` | `controller-ingest` | `iot_write_user` |
| POST | `/api/v1/actuator-commands/sent` | `controller-client` | `controller-ingest` | `iot_write_user` |
| GET  | `/api/v1/actuator-states`        | `dashboard-client` **or** `lstm-client` | `dashboard-user` **or** `lstm-control` | `iot_write_user` |
| POST | `/api/v1/sensor-request`         | `dashboard-client`  | `dashboard-user`    | `iot_write_user` |
| GET  | `/api/v1/sensor-requests`        | `controller-client` | `controller-ingest` | `iot_write_user` |
| POST | `/api/v1/sensor-requests/sent`   | `controller-client` | `controller-ingest` | `iot_write_user` |

The backend matches the audience against the token's `aud` claim if it is a string or array, falling back to `azp` (Keycloak's authorized-party claim, which always carries the client_id). The realm role is read from `realm_access.roles`.

`GET /api/v1/sensor-data` is the one route with two accepted policies: the dashboard reads the same series the LSTM control loop feeds its model with, so a token signed for either `dashboard-client` + `dashboard-user` or `lstm-client` + `lstm-control` is admitted. The router walks the policy list and accepts the first match; every other route keeps a single (audience, role) pair.

---

## Token verification

The backend boots with two environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `KEYCLOAK_JWKS_URL` | `http://keycloak:8080/auth/realms/iot/protocol/openid-connect/certs` | JWKS endpoint. Internal compose-network URL; no TLS needed. The `/auth/` prefix matches `KC_HTTP_RELATIVE_PATH=/auth` on the keycloak service. |
| `KEYCLOAK_ISSUER` | `https://www.lab.local/auth/realms/iot` | Required `iss` claim in every accepted token. |

On startup the verifier best-effort fetches JWKS once so the first request does not pay the round-trip. Subsequent unknown `kid`s trigger one refresh-then-retry; if the key still cannot be resolved the request is rejected with 403.

---

## Database Schema

```sql
CREATE TABLE sensor_data (
    id          BIGSERIAL        PRIMARY KEY,
    sensor_id   VARCHAR(64)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        VARCHAR(16)      NOT NULL,
    recorded_at TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE TABLE actuator_commands (
    id          BIGSERIAL    PRIMARY KEY,
    actuator_id VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_by   VARCHAR(16)  NOT NULL DEFAULT 'user',  -- 'user' | 'machine'
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ           -- NULL until the controller dispatches it
);

CREATE TABLE sensor_requests (
    id          BIGSERIAL    PRIMARY KEY,
    sensor_id   VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ           -- NULL until the controller dispatches it
);
```

The `dashboard_users` table from earlier phases is gone; user identity now lives entirely in Keycloak. `docker/postgres/migrate.sql` includes `DROP TABLE IF EXISTS dashboard_users` for the live Pi.
