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
│   └── postgres   — PostgreSQL, not exposed to host
│
└── sensor-net (172.21.0.0/24)  [reserved, not yet deployed]
    ├── mosquitto  — MQTT broker, TLS, per-sensor ACL
    └── controller — bridges MQTT → backend API
```

Containers on `sensor-net` cannot reach `postgres` directly; they must go through the backend API on `app-net`.

## Security Principles Applied

### Least Common Mechanism
- Each container runs in its own isolated network segment.
- Two separate DB users (`iot_write_user`, `iot_read_user`) with distinct permissions.
- One MQTT topic per sensor, with per-sensor credentials and ACL.

### Complete Mediation
- nginx checks every inbound request before it reaches the backend.
- The firewall (nftables on WLAN-AP) checks every packet entering the network.
- JWT tokens validate every HTTP request to authenticated endpoints.

### Least Privilege
- `iot_write_user`: `SELECT`, `INSERT`, `UPDATE` on `sensor_data` only.
- `iot_read_user`: `SELECT` on `sensor_data` only.
- No container runs as root. No container has `--privileged`.
- nginx only forwards `/health` and `/api/` — all other paths are dropped.

## Data Flow

```
Pico sensor
  │  MQTT/TLS  (sensor-net)
  ▼
mosquitto
  │  internal HTTP
  ▼
controller
  │  POST /api/v1/sensor-data  (app-net)
  ▼
backend (Zig)
  │  libpq / iot_write_user
  ▼
postgres.sensor_data

Dashboard / browser
  │  HTTPS → nginx → GET /api/v1/sensor-data  (app-net)
  ▼
backend (Zig)
  │  libpq / iot_read_user
  ▼
postgres.sensor_data
```
