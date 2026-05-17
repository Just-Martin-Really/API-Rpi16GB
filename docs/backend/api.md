# API Reference

Base URL: `https://backend-server.local`

All endpoints return `application/json`. All `/api/` endpoints require a valid JWT in the `Authorization: Bearer <token>` header — obtain one via `POST /auth/login`.

---

## POST /auth/login

Authenticates a dashboard user and returns a JWT. No `Authorization` header required.

**Request body**
```json
{
  "username": "admin",
  "password": "changeme"
}
```

**Response 200**
```json
{"token": "<jwt>"}
```

The token is valid for 24 hours. Include it in subsequent requests as `Authorization: Bearer <token>`.

**Response 401**
```json
{"error": "unauthorized"}
```

---

## GET /health

Returns the server liveness status. No auth required.

**Response 200**
```json
{"status": "ok"}
```

---

## GET /api/v1/sensor-data

Returns all sensor readings, newest first.

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

---

## POST /api/v1/sensor-data

Inserts a new sensor reading. Used by the controller service (not directly by the MCU).

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

**Response 400** — missing or invalid fields
```json
{"error": "invalid request body"}
```

---

## POST /api/v1/actuator-command

Queues a command for an actuator. The controller service picks it up within ~2 seconds and publishes it to the actuator's MQTT topic (`<actuator_id>/data`).

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
| `command` | string | yes | max 64 chars, must match `^[A-Z0-9_]+$` to be dispatched; current commands: `FAN_ON`, `FAN_OFF`, `HEAT_ON`, `HEAT_OFF` |

Rows whose `actuator_id` or `command` fail the regex are accepted by the API but skipped (and marked sent) by the controller to prevent MQTT topic injection from DB content.

**Response 201**
```json
{"queued": true}
```

**Response 400** — missing or invalid fields
```json
{"error": "invalid json"}
```

---

## POST /api/v1/sensor-request

Queues a request for a sensor to publish a fresh reading. The controller service picks it up within ~2 seconds and publishes it to the sensor's MQTT topic (`<sensor_id>/request`). The Pico, on receiving `READ_NOW`, reads the DHT22 and publishes the values on `<sensor_id>/data` as usual.

Used by the dashboard or a watchdog to force a fresh reading when periodic data has not arrived within its expected cadence. The request itself does not wait for the reading; the caller polls `GET /api/v1/sensor-data` for the new row, or watches the broker.

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
