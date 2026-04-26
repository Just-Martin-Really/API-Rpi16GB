# Architecture

## System Overview

Three nodes communicate over the Production WLAN (`192.168.50.0/24`):

| Node | Hardware | Role |
|------|----------|------|
| WLAN-AP | RPi 5 2 GB | WLAN access point, router, nftables firewall |
| Backend | RPi 5 16 GB | This repo — HTTP API, PostgreSQL, Docker host |
| MCU | RPi Pico WH | Sensor/actuator node (MicroPython) |

The WLAN-AP is the only gateway to the lab LAN. The backend is not directly reachable from the internet.

## Backend Docker Networks

Two isolated bridge networks enforce least-privilege between container groups.

```
host (RPi 5 16 GB)
│
├── app-net (172.20.0.0/24)
│   ├── nginx      — reverse proxy, sole entry point from WLAN
│   ├── backend    — Zig HTTP server, business logic
│   ├── postgres   — PostgreSQL, not exposed to host
│   ├── mosquitto  — also on app-net so controller can reach it
│   └── controller — MQTT→DB bridge, also on app-net to reach postgres
│
└── sensor-net (172.21.0.0/24)
    ├── mosquitto  — exposed on port 8883 to the Production WLAN
    └── controller — subscribes to sensor topics, publishes actuator topics
```

`mosquitto` and `controller` are on both networks. `postgres` is only on `app-net` — the MCU and any sensor-side traffic can never reach it directly.

## Security Principles Applied

### Fail-Secure Defaults
- nginx returns 502 if the backend container is down — requests are never passed through to an unprotected fallback
- JWT validation returns 401 on any failure (missing header, invalid signature, expired token) — there is no code path that allows a request through on error
- MQTT broker rejects the connection outright on failed authentication — no anonymous fallback
- If a DB query fails during a request, the handler returns an error response — never partial or empty data silently treated as success

### Defence in Depth
Security is enforced at multiple independent layers, so a failure or bypass of any single layer does not compromise the system:
1. nftables on the WLAN-AP — only Production WLAN traffic enters the network at all
2. nginx subnet whitelist + rate limiting — further restricts which hosts can reach which paths
3. JWT validation on every `/api/` endpoint — authentication is re-checked per request, not per session
4. DB users with minimal rights — even full compromise of the backend process cannot DROP, ALTER, or access tables outside the granted scope
5. Docker network isolation — sensor-side containers have no route to postgres
6. MQTT ACL — a compromised sensor credential cannot read other sensors' data or write to actuator topics



### Least Common Mechanism
- Each container runs in its own isolated network segment.
- Two separate DB users (`iot_write_user`, `iot_read_user`) with distinct permissions.
- One MQTT topic per sensor, with per-sensor credentials and ACL.

### Complete Mediation
- nginx checks every inbound request before it reaches the backend.
- The firewall (nftables on WLAN-AP) checks every packet entering the network.
- JWT tokens validate every HTTP request to authenticated endpoints.

### Least Privilege
- `iot_write_user`: `SELECT`, `INSERT`, `UPDATE` on `sensor_data`; `INSERT`, `SELECT` on `actuator_commands` only.
- `iot_read_user`: `SELECT` on `sensor_data` and `dashboard_users` only.
- MQTT: `sensor01` can only publish to `sensor01/data`. `controller` can only read `sensor+/data` and write `actuator+/data`.
- No container runs as root. No container has `--privileged`.
- nginx forwards only `/health`, `/auth/`, and `/api/` — all other paths are dropped.

## Data Flow

```
Sensor reading
──────────────
Pico (sensor01)
  │  MQTT/TLS on sensor01/data  (sensor-net, port 8883)
  ▼
mosquitto  [ACL: sensor01 write-only to sensor01/data]
  │  MQTT/TLS subscription
  ▼
controller.py
  │  INSERT INTO sensor_data  (app-net, iot_write_user)
  ▼
postgres.sensor_data

Dashboard read
──────────────
Browser
  │  HTTPS → nginx → GET /api/v1/sensor-data  (app-net)
  ▼
backend (Zig)  [JWT validated]
  │  SELECT  (iot_read_user)
  ▼
postgres.sensor_data

Actuator command
────────────────
Browser
  │  HTTPS → nginx → POST /auth/login → JWT
  │  HTTPS → nginx → POST /api/v1/actuator-command  (app-net)
  ▼
backend (Zig)  [JWT validated]
  │  INSERT INTO actuator_commands  (iot_write_user)
  ▼
postgres.actuator_commands
  │  polled every 2s by controller.py
  ▼
controller.py
  │  MQTT publish to actuator01/data  (sensor-net)
  │  UPDATE actuator_commands SET sent_at = NOW()
  ▼
mosquitto → Pico (actuator01)
```
