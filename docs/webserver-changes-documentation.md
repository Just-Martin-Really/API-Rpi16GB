# Webserver Documentation


## 1. Overview

`server.js` — a dedicated Node.js HTTP service that acts as the sensor data ingestion endpoint. It sits between the MQTT controller and the PostgreSQL database, validating and storing sensor readings.

**Data flow:**
1. Pico (DHT22 sensor) publishes `{value, unit}` via MQTT/TLS to mosquitto
2. `controller.py` subscribes to MQTT, collects temperature and humidity, forwards a combined payload via HTTPS
3. nginx (reverse proxy) receives the request and routes `/api/internal/` to `server.js`
4. `server.js` validates the payload and writes two rows to PostgreSQL (one for temperature, one for humidity)

> **Note:** The existing Zig backend remains unchanged. It handles the dashboard (read) and actuator commands independently. Both services share the same PostgreSQL database but serve different purposes.


## 2. Architecture

### 2.1 Component Overview

| Component         | Role                                                                                  | Auth                         |
|                   |                                                                                       |                              |
| Pico (`main.py`)  | Reads DHT22, publishes `{value, unit}` via MQTT/TLS                                   | MQTT user/password           |
| `controller.py`   | Subscribes MQTT, combines temp+humidity, POSTs to `server.js`                         | API-Key (`x-api-key`header)  |
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
  "temperature": 22.5,
  "humidity": 55.0,
  "timestamp": "2026-05-11T12:00:00.000Z"
}
```

### 3.3 Validation Rules

| Field         | Type            | Rule                                                                             |
|               |                 |                                                                                  |
| `temperature` | number          | Must be finite, range 0..60 °C                                                   |
| `humidity`    | number          | Must be finite, range 10..70 %                                                   |
| `timestamp`   | ISO 8601 string | Must be valid date, not more than 5 minutes in the future, not older than 7 days |

> **Note:** The 7-day limit is intentionally aligned with the archiver job, which moves data older than 7 days to `sensor_data_archive`. Accepting older data would write to the wrong table.

### 3.4 Database Write

On successful validation, `server.js` inserts two rows into `sensor_data` in a single query:

```sql
INSERT INTO sensor_data (sensor_id, value, unit, recorded_at)
VALUES
  ('temperature', <value>, 'C', <timestamp>),
  ('humidity',    <value>, '%', <timestamp>)
```


## 4. controller.py — Changes

The controller was updated to work with `server.js` instead of the Zig backend.

| Before (Zig backend)                                       | After (server.js)                                                         |
|                                                            |                                                                           |
| JWT login via `POST /auth/login`                           | No login — API-Key only                                                   |
| `Authorization: Bearer <token>` header                     | `x-api-key: <key>` header                                                 |
| `POST /api/v1/sensor-data` with `{sensor_id, value, unit}` | `POST /api/internal/sensordata` with `{temperature, humidity, timestamp}` |
| One MQTT message → one API call                            | Two MQTT messages collected → one combined API call |

The controller uses a `pending{}` buffer to collect both MQTT messages. The Pico publishes temperature and humidity as separate messages on the same topic. Once both are present in the buffer, a single combined request is sent to `server.js` and the buffer is cleared.


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

| File                              | Change                                                                 |
|                                   |                                                                        |
| `docker/webserver/server.js`      | New — Node.js ingestion endpoint                                       |
| `docker/webserver/Dockerfile`     | New — builds the server.js container                                   |
| `docker/webserver/package.json`   | New — dependencies (express, pg)                                       |
| `docker/controller/controller.py` | Modified — API-Key instead of JWT, combines MQTT messages              |
| `docker/docker-compose.yml`       | Modified — added webserver service, api_key secret, updated controller |
| `docker/nginx/nginx.conf`         | Modified — added `/api/internal/` routing to webserver                 |
| `docker/secrets/api_key.txt`      | New — must be generated manually, never committed to git               |


## 9. Note on Database Choice

Since we used PostgreSQL instead of MariaDB, there is the following difference in the in the choice of Packages:

- The npm package used is `pg` instead of `mysql2`

## 10. TLS Certificate Update — setup_tls.sh

### What changed and why

`setup_tls.sh` was updated to include `DNS:nginx` as a Subject Alternative
Name (SAN) in the backend TLS certificate.

**Before:**
```bash
printf "subjectAltName=DNS:%s,DNS:localhost,IP:...
```

**After:**
```bash
printf "subjectAltName=DNS:%s,DNS:localhost,DNS:nginx,...
```

This is necessary because `controller.py` connects to `https://nginx` inside
the Docker network. When verifying the TLS certificate, the hostname `nginx`
must be listed as a valid SAN — otherwise the SSL handshake fails and the
controller cannot reach `server.js`.


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
