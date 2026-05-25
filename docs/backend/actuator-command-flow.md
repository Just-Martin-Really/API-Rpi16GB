# Actuator Command Flow

Describes how an actuator command travels from a caller (dashboard, operator script) to the Pico.

## Overview

```
caller ──► nginx ──► backend ──► postgres (actuator_commands)
                                       ▲
                                       │ poll every 2s
                                       │
controller ──► nginx ──► backend ──────┘
   │
   └──► mosquitto ──► Pico   (publishes <actuator_id>/data, QoS 1)
```

The flow is decoupled by the `actuator_commands` table. The caller inserts a row and returns immediately. The controller drains pending rows asynchronously and publishes them over MQTT. The controller never opens a direct connection to PostgreSQL; all database access goes through the Zig backend via nginx, mirroring the sensor ingest path.

## Public endpoint

`POST /api/v1/actuator-command` — see [API Reference](api.md#post-apiv1actuator-command). Authenticated with a Keycloak access token whose audience is `lstm-client` and whose realm role is `lstm-control`. Inserts one row into `actuator_commands` with `sent_at = NULL`.

## Controller drain endpoints

Both routes are served by the Zig backend (`src/handlers/actuator.zig`, routed in `src/router.zig`). Authentication is a Keycloak access token whose audience is `controller-client` and whose realm role is `controller-ingest`.

### GET /api/v1/actuator-commands

Returns up to 100 pending commands, oldest first.

**Response 200**
```json
{
  "commands": [
    { "id": 42, "actuator_id": "actuator01", "command": "FAN_ON" }
  ]
}
```

### POST /api/v1/actuator-commands/sent

Marks one command as sent. Idempotent: subsequent calls for the same id return `{"updated": 0}` because the SQL also checks `sent_at IS NULL`.

**Request body**
```json
{ "id": 42 }
```

**Response 200**
```json
{ "updated": 1 }
```

**Response 400** — `id` missing, not an integer, or non-positive.

## Controller loop

`docker/controller/controller.py` runs the following loop in its main thread while paho's network thread services MQTT in the background:

1. `GET /api/v1/actuator-commands` (HTTPS, `Authorization: Bearer <token>`).
2. For each row:
    1. Validate `actuator_id` against `^[A-Za-z0-9_-]+$` and `command` against `^[A-Z0-9_]+$`. Rejected rows are marked sent without publishing — this prevents MQTT topic injection if a malicious authenticated caller writes `actuator_id = "$SYS/foo"` or similar.
    2. Publish `{"command": "<command>"}` to `<actuator_id>/data` at QoS 1 and wait up to 5 s for the broker ack.
    3. On successful publish, `POST /api/v1/actuator-commands/sent` to mark the row.
3. Sleep `ACTUATOR_POLL_SECONDS` (2 s) and repeat.

The token is fetched from Keycloak on demand via the client-credentials flow (`controller-client`) and cached in memory. On a 401 from any drain call the token cache is invalidated and the request is retried once; on a 403 the loop logs and continues.

Errors at any step are logged and the loop continues; the row stays unsent and is retried on the next poll.

## Delivery semantics

At-least-once. If the controller crashes between a successful publish and the mark-sent POST, the next poll republishes the same row. All currently defined commands (`FAN_ON`, `FAN_OFF`, `HEAT_ON`, `HEAT_OFF`) are idempotent state assertions on the Pico, so duplicate delivery is safe.

Worst-case latency: poll interval (2 s) plus one publish round-trip. Under broker outage, the publish wait of 5 s per row will dominate, and rows accumulate until the broker recovers.

## Manual override

Operators issue heater/cooler commands through the dashboard, which posts to `/api/v1/actuator-command` with a `dashboard-user` token. The Phase-5 `scripts/heater.sh` / `scripts/cooler.sh` wrappers were removed when their `/auth/login` + `dashboard_users` auth path was retired; if a future ops tool needs out-of-band access it should run the same client-credentials flow as the LSTM.

## Database schema

```sql
CREATE TABLE actuator_commands (
    id          BIGSERIAL    PRIMARY KEY,
    actuator_id VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_by   VARCHAR(16)  NOT NULL DEFAULT 'user',  -- 'user' | 'machine'
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ
);

CREATE INDEX idx_actuator_commands_unsent
    ON actuator_commands (issued_at)
    WHERE sent_at IS NULL;
```

Grants: `iot_write_user` holds `INSERT, SELECT, UPDATE`. The Zig backend uses this role for both the issuing path (`POST /api/v1/actuator-command`) and the drain path (`GET` / `POST /sent`). No other service touches the table.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `actuator drain error: Connection refused` in controller logs | `API_BASE_URL` is wrong or nginx is down |
| `actuator drain error: HTTP Error 401` | Token expired or revoked mid-flight; the controller refreshes and retries once. Persistent 401 means the Keycloak client secret in `/run/secrets/keycloak_controller_secret` does not match `controller-client` in the realm |
| `actuator drain error: HTTP Error 403` | Token is valid but missing the `controller-ingest` realm role on the `controller-client` service account, or audience does not match |
| `actuator publish failed: id=N` | Mosquitto unreachable or the publish ack timed out; row stays unsent for the next poll |
| `actuator skipped invalid row id=N` | The row's `actuator_id` or `command` failed the regex check; row was marked sent without publishing |
| Row stays with `sent_at = NULL` and no log line | Controller is not running, or it cannot reach the backend at all |
