# API Reference

Base URL: `https://backend-server.local`

All endpoints return `application/json`. All non-health endpoints require a valid JWT in the `Authorization: Bearer <token>` header (to be implemented).

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

## Database Schema

```sql
CREATE TABLE sensor_data (
    id          BIGSERIAL        PRIMARY KEY,
    sensor_id   VARCHAR(64)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        VARCHAR(16)      NOT NULL,
    recorded_at TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
```
