# Actuator Command Flow

Describes how an actuator command travels from a caller (dashboard, operator script) to the Pico.

## Overview

```
caller ──► nginx ──► backend ──► postgres (actuator_commands)
                                       ▲
                                       │ poll every 2s
                                       │
controller ──► nginx ──► webserver ────┘
   │
   └──► mosquitto ──► Pico   (publishes <actuator_id>/data, QoS 1)
```

The flow is decoupled by the `actuator_commands` table. The caller inserts a row and returns immediately. The controller drains pending rows asynchronously and publishes them over MQTT. The controller never opens a direct connection to PostgreSQL; all database access goes through the webserver via nginx, mirroring the sensor ingest path.

## Public endpoint

`POST /api/v1/actuator-command` — see [API Reference](api.md#post-apiv1actuator-command). Authenticated with a dashboard JWT. Inserts one row into `actuator_commands` with `sent_at = NULL`.

## Internal endpoints

Both routes live on the Node webserver (`docker/webserver/server.js`) under `/api/internal/`, behind the existing `x-api-key` header check. They are not exposed outside the docker network's allowlist.

### GET /api/internal/actuator-commands

Returns up to 100 pending commands, oldest first.

**Response 200**
```json
{
  "commands": [
    { "id": 42, "actuator_id": "actuator01", "command": "FAN_ON" }
  ]
}
```

### POST /api/internal/actuator-commands/sent

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

1. `GET /api/internal/actuator-commands` (HTTPS, x-api-key).
2. For each row:
    1. Validate `actuator_id` against `^[A-Za-z0-9_-]+$` and `command` against `^[A-Z0-9_]+$`. Rejected rows are marked sent without publishing — this prevents MQTT topic injection if a malicious authenticated caller writes `actuator_id = "$SYS/foo"` or similar.
    2. Publish `{"command": "<command>"}` to `<actuator_id>/data` at QoS 1 and wait up to 5 s for the broker ack.
    3. On successful publish, `POST /api/internal/actuator-commands/sent` to mark the row.
3. Sleep `ACTUATOR_POLL_SECONDS` (2 s) and repeat.

Errors at any step are logged and the loop continues; the row stays unsent and is retried on the next poll.

## Delivery semantics

At-least-once. If the controller crashes between a successful publish and the mark-sent POST, the next poll republishes the same row. All currently defined commands (`FAN_ON`, `FAN_OFF`, `HEAT_ON`, `HEAT_OFF`) are idempotent state assertions on the Pico, so duplicate delivery is safe.

Worst-case latency: poll interval (2 s) plus one publish round-trip. Under broker outage, the publish wait of 5 s per row will dominate, and rows accumulate until the broker recovers.

## Operator scripts

`scripts/cooler.sh on|off` and `scripts/heater.sh on|off` are thin wrappers around the public endpoint. They authenticate against `/auth/login`, then POST a single command. See `scripts/.env.example` for the required environment.

## Database schema

```sql
CREATE TABLE actuator_commands (
    id          BIGSERIAL    PRIMARY KEY,
    actuator_id VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ
);

CREATE INDEX idx_actuator_commands_unsent
    ON actuator_commands (issued_at)
    WHERE sent_at IS NULL;
```

Grants: `iot_write_user` holds `INSERT, SELECT, UPDATE`. The backend inserts (issuing path), the webserver selects and updates (drain path). No other service touches the table.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `actuator drain error: Connection refused` in controller logs | `API_BASE_URL` does not include the in-network port (`https://nginx:8443`), or nginx is down |
| `actuator drain error: HTTP Error 401` | API key in `/run/secrets/api_key` does not match what the webserver reads |
| `actuator publish failed: id=N` | Mosquitto unreachable or the publish ack timed out; row stays unsent for the next poll |
| `actuator skipped invalid row id=N` | The row's `actuator_id` or `command` failed the regex check; row was marked sent without publishing |
| Row stays with `sent_at = NULL` and no log line | Controller is not running, or it cannot reach the webserver at all |
