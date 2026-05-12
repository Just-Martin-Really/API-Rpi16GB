# Webserver Documentation


## 1. Overview

`server.js` — a dedicated Node.js HTTP service that acts as the sensor data ingestion endpoint. It sits between the MQTT controller and the PostgreSQL database, validating and storing sensor readings.

**Data flow:**
1. Pico (DHT22 sensor) publishes `{value, unit}` via MQTT/TLS to mosquitto
2. `controller.py` subscribes to MQTT, collects temperature and humidity per `sensor_id`, forwards a combined payload via HTTPS
3. nginx (reverse proxy) receives the request and routes `/api/internal/` to `server.js`
4. `server.js` validates the payload and writes two rows to PostgreSQL (one for temperature, one for humidity)

> **Note:** The existing Zig backend remains unchanged. It handles the dashboard (read) and actuator commands independently. Both services share the same PostgreSQL database but serve different purposes.


## 2. Architecture

### 2.1 Component Overview

| Component         | Role                                                                                  | Auth                         |
|                   |                                                                                       |                              |
| Pico (`main.py`)  | Reads DHT22, publishes `{value, unit}` via MQTT/TLS                                   | MQTT user/password           |
| `controller.py`   | Subscribes MQTT, buffers per `sensor_id`, combines temp+humidity, POSTs to `server.js`| API-Key (`x-api-key`header)  |
| nginx             | Reverse proxy, TLS termination, rate limiting, routes `/api/internal/` to `server.js` | —                            |
| `server.js`       | Validates payload, writes to PostgreSQL                                               | API-Key                      |
| Zig backend       | Dashboard API, reads sensor data, manages actuator commands                           | JWT Bearer Token             |
| PostgreSQL        | Stores `sensor_data`, `actuator_commands`, `dashboard_users`                          | DB user/password             |
| archiver          | Moves rows older than 7 days to `sensor_data_archive`, purges after 3 years           | DB user/password             |

### 2.2 Docker Networks

| Network       | Services                                                                   |
|               |                                                                            |
| `sensor-net`  | mosquitto, controller                                                      |
| `app-net`     | postgres, backend (Zig), nginx, mosquitto, controller, webserver, archiver |


## 3. server.js — Webserver

### 3.1 Endpoints

| Method | Path                       | Auth               | Description                                 |
|        |                            |                    |                                             |
| `GET`  | `/health`                  | None               | Returns `{"status":"ok"}`                   |
| `POST` | `/api/internal/sensordata` | `x-api-key` header | Validates and stores temperature + humidity |

### 3.2 Expected Request Payload

```http
POST /api/internal/sensordata
x-api-key: <api_key>
Content-Type: application/json

{
  "sensor_id": "sensor01",
  "temperature": 22.5,
  "humidity": 55.0,
  "timestamp": "2026-05-11T12:00:00.000Z"
}
```

### 3.3 Validation Rules

| Field         | Type            | Rule                                                                             |
|               |                 |                                                                                  |
| `sensor_id`   | string          | Non-empty, max 64 characters — identifies the physical sensor unit               |
| `temperature` | number          | Must be finite, range -40..80 °C (DHT22 sensor spec)                             |
| `humidity`    | number          | Must be finite, range 0..100 % (DHT22 sensor spec)                               |
| `timestamp`   | ISO 8601 string | Must be valid date, not more than 5 minutes in the future, not older than 7 days |

> **Note:** The 7-day limit is intentionally aligned with the archiver job, which moves data older than 7 days to `sensor_data_archive`. Accepting older data would write to the wrong table.

> **Note:** Ranges are aligned with the actual DHT22 sensor specifications — not artificially narrowed. The controller performs the same range checks before buffering.

### 3.4 Authentication

The API-Key comparison uses `crypto.timingSafeEqual` to prevent timing attacks. The key is read from `/run/secrets/api_key` at startup.

### 3.5 Database Write

On successful validation, `server.js` inserts two rows into `sensor_data` in a single query. The `sensor_id` from the payload is used as a prefix to distinguish measurement types:

```sql
INSERT INTO sensor_data (sensor_id, value, unit, recorded_at)
VALUES
  ('<sensor_id>_temperature', <value>, 'C', <timestamp>),
  ('<sensor_id>_humidity',    <value>, '%', <timestamp>)
```

Example for `sensor_id = "sensor01"`:
```sql
  ('sensor01_temperature', 22.5, 'C', '2026-05-11T12:00:00Z'),
  ('sensor01_humidity',    55.0, '%', '2026-05-11T12:00:00Z')
```

### 3.6 Error Handling

| Error type                                              | HTTP status |
|                                                         |             |
| Validation error (invalid payload, out-of-range values) | `400`       |
| Database / infrastructure error                         | `500`       |
| Missing or wrong API-Key                                | `401`       |

## 4. controller.py

### 4.1 Changes from original version

The controller was updated to work with `server.js` instead of the Zig backend.

| Before (Zig backend)                                       | After (server.js)                                                         |
|                                                            |                                                                           |
| JWT login via `POST /auth/login`                           | No login — API-Key only                                                   |
| `Authorization: Bearer <token>` header                     | `x-api-key: <key>` header                                                 |
| `POST /api/v1/sensor-data` with `{sensor_id, value, unit}` | `POST /api/internal/sensordata` with `{temperature, humidity, timestamp}` |
| One MQTT message → one API call                            | Two MQTT messages buffered per `sensor_id` → one combined API call        |

### 4.2. Buffer Logic

The Pico publishes temperature and humidity as two separate MQTT messages on the same topic. The controller uses a `pending{}` buffer keyed by `sensor_id` to collect both values before forwarding.

```
pending = {
  "sensor01": {
    "temperature": (22.5, <timestamp>),
    "humidity":    (55.0, <timestamp>)
  }
}
```

A combined request is only sent when **both** values are present **and** fresh. After sending (or on error), the buffer entry for that `sensor_id` is cleared.

### 4.3 Freshness Window

```python
FRESHNESS_SECONDS = 55  # just under the Pico publish interval of 60s
```

If either buffered value is older than 55 seconds when the second value arrives, the entire buffer entry for that `sensor_id` is discarded. This prevents stale temperature values from being paired with a new humidity reading.

### 4.4 Range Checks

Range checks are performed **before** buffering — invalid values are discarded immediately:

| Measurement | Range      |
|             |            |
| Temperature | -40..80 °C |
| Humidity    | 0..100 %   |

### 4.5 Multi-Sensor Support

Because the buffer is keyed by `sensor_id` (extracted from `msg.topic.split("/")[0]`), multiple physical sensors on different topics are handled independently without interference.

## 5. API Key Setup

The API key is a shared secret between `controller.py` and `server.js`. It is **never** stored in the repository — it lives only in `secrets/` on the host.

### 5.1 Development Environment (Windows)

**Option A — PowerShell:**
```powershell
cd C:\path\to\your\repo\docker
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) }) | Out-File -Encoding ascii -NoNewline secrets\api_key.txt
```

**Option B — Manual:**
Create `secrets\api_key.txt` in VS Code and paste any long random string, e.g.:
```
f3a9c2e1b4d7f0a8c5e2b9d6f3a0c7e4b1d8f5a2c9e6b3d0f7a4c1e8b5d2f9a6
or
79e9f59b1164ac00d9b28f1b7ddb6a2a4fc2cdcc10a954bce5a34899872b2a02
```

**Verify:**
```powershell
type secrets\api_key.txt
```

### 5.2 Production Environment (Raspberry Pi)

```bash
openssl rand -hex 32 > secrets/api_key.txt
chmod 600 secrets/api_key.txt
```

**Verify:**
```bash
cat secrets/api_key.txt
```

The output must be a single line with a 64-character hex string — no spaces, no newlines, no quotes.

>`secrets/` has to be listed in `.gitignore`. **Never commit `api_key.txt`.**


## 6. docker-compose.yml Changes

### 6.1 New Secret
```yaml
secrets:
  api_key:
    file: ./secrets/api_key.txt
```

### 6.2 New webserver Service
```yaml
webserver:
  build:
    context: ./webserver
    dockerfile: Dockerfile
  restart: unless-stopped
  environment:
    DB_HOST: postgres
    DB_PORT: "5432"
    DB_NAME: sensor
    DB_USER: iot_write_user
  secrets:
    - db_write_password
    - api_key
  networks:
    - app-net
  depends_on:
    - postgres
```

### 6.3 Updated controller Service
- **Removed:** `api_password` secret, `API_USERNAME` environment variable
- **Added:** `api_key` secret


## 7. nginx Changes

A new location block was added **before** the general `/api/` block:

```nginx
location /api/internal/ {
    limit_req zone=api burst=10 nodelay;
    proxy_pass http://webserver:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

location /api/ {
    limit_req zone=api burst=10 nodelay;
    proxy_pass http://backend:8080;
    ...
}
```

> **Important:** `/api/internal/` must appear **before** `/api/`. nginx uses the most specific matching location. Without this order, all `/api/internal/` requests would be routed to the Zig backend instead of `server.js`.


## 8. File Overview

| File                                 | Change                                                                 |
|                                      |                                                                        |
| `docker/webserver/server.js`         | New — Node.js ingestion endpoint                                       |
| `docker/webserver/Dockerfile`        | New — builds the server.js container                                   |
| `docker/webserver/package.json`      | New — dependencies (express, pg)                                       |
| `docker/webserver/package-lock.json` | New — lockfile for reproducible builds                                 |
| `docker/controller/controller.py`    | Modified — API-Key instead of JWT, combines MQTT messages              |
| `docker/docker-compose.yml`          | Modified — added webserver service, api_key secret, updated controller |
| `docker/nginx/nginx.conf`            | Modified — added `/api/internal/` routing to webserver                 |
| `docker/secrets/api_key.txt`         | New — must be generated manually, never committed to git               |
| `docker/setup_tls.sh`                | Modified — added `DNS:nginx` SAN, added 10s abort warning              |


## 9. Note on Database Choice

Since we used PostgreSQL instead of MariaDB, there is the following difference in the in the choice of Packages:

- The npm package used is `pg` instead of `mysql2`

## 10. TLS Certificate Update — setup_tls.sh

### What changed and why

`setup_tls.sh` was updated in two ways:

**1. Added `DNS:nginx` as Subject Alternative Name (SAN)**

`controller.py` connects to `https://nginx` inside the Docker network. The hostname `nginx` must be listed as a valid SAN in the TLS certificate — otherwise the SSL handshake fails and the controller cannot reach `server.js`.

**Before:**
```bash
printf "subjectAltName=DNS:%s,DNS:localhost,IP:...
```

**After:**
```bash
printf "subjectAltName=DNS:%s,DNS:localhost,DNS:nginx,...
```

**2. Added 10-second abort warning**

The script now prints a warning and waits 10 seconds before overwriting existing certificates, giving operators time to abort with `Ctrl+C`.

### What to do on the Pi

If the existing TLS certificates on the Pi were generated without `DNS:nginx`
then they must be regenerated. 
If this applies, please run the following once after pulling the latest changes:

```bash
cd docker
bash setup_tls.sh
docker compose down
docker compose up -d
```

>`setup_tls.sh` overwrites the existing certificates under
>`/etc/ssl/backend/`. After regeneration all containers that use TLS must
> be restarted — `docker compose down && docker compose up -d` handles this.
