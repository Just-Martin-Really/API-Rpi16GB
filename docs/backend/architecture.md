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
│   ├── nginx — reverse proxy + ModSecurity WAF (OWASP CRS), sole HTTPS entry point from WLAN and controller
│   ├── backend — Zig HTTP server, business logic, database access
│   ├── postgres — PostgreSQL, not exposed to host
│   ├── mosquitto — also on app-net for internal service communication
│   └── controller — MQTT→HTTPS API bridge, forwards sensor readings via nginx
│
└── sensor-net (172.21.0.0/24)
    ├── mosquitto — exposed on port 8883 to the Production WLAN
    └── controller — subscribes to sensor topics, publishes actuator topics
```

`mosquitto` and `controller` are on both networks. `postgres` is only on `app-net` and is never exposed to the host or sensor network.

The controller no longer writes directly to PostgreSQL. It receives MQTT messages on `sensor-net`, authenticates against the backend API, and forwards validated sensor readings to nginx over HTTPS. nginx then proxies the request to the Zig backend, which performs validation and persistence in PostgreSQL.

This keeps database access centralized in the backend and prevents the MQTT/controller layer from requiring database credentials.

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
3. ModSecurity WAF with OWASP Core Rule Set — every request is inspected at HTTP Layer 7 for SQLi, XSS, RCE, and path-traversal patterns before nginx hands off to the backend
4. JWT validation on every `/api/` endpoint — authentication is re-checked per request, not per session
5. DB users with minimal rights — even full compromise of the backend process cannot DROP, ALTER, or access tables outside the granted scope
6. Docker network isolation — sensor-side traffic has no route to postgres; sensor data reaches the database only through controller → nginx → backend
7. MQTT ACL — a compromised sensor credential cannot read other sensors' data or write to actuator topics



### Least Common Mechanism
- Services are split across isolated Docker networks (`app-net`, `sensor-net`) so MQTT traffic, API traffic, and database traffic are separated.
- Two separate DB users (`iot_write_user`, `iot_read_user`) with distinct permissions.
- One MQTT topic per sensor, with per-sensor credentials and ACL.

### Complete Mediation
- nginx checks every inbound request before it reaches the backend.
- The firewall (nftables on WLAN-AP) checks every packet entering the network.
- JWT validation is performed for every authenticated HTTP request.

### Least Privilege
- `iot_write_user`: internal backend database user with restricted write access to application tables.
- `iot_read_user`: `SELECT` on `sensor_data` and `dashboard_users` only.
- MQTT: `sensor01` can publish to `sensor01/data` and subscribe to `actuator01/data` (so the Pico receives relay commands). `controller` can subscribe to sensor topics and publish actuator topics. Both restricted by the broker ACL.
- The controller does not receive database credentials. It only receives MQTT credentials and API credentials.
- Containers are designed to avoid unnecessary privileges and do not use `--privileged`.
- nginx forwards only `/health`, `/auth/`, and `/api/` — all other paths are dropped.

## Web Application Firewall (Kap4 4.2)

nginx is built on the `owasp/modsecurity-crs:nginx-alpine` image, which bundles libmodsecurity 3, the ModSecurity-nginx connector, and the OWASP Core Rule Set. Every request that survives the TLS handshake and the subnet allowlist is inspected by ModSecurity before it can reach the backend.

**Configuration source of truth**: `docker/nginx/templates/conf.d/default.conf.template` (server block, `modsecurity on;`) and `docker/nginx/modsec/` (include chain, custom rule, exclusions).

**Engine mode**: `DetectionOnly` on first deploy. CRS matches are logged but requests are not blocked. The mode is flipped to `On` only after the audit log shows no false positives on legitimate traffic. Controlled by `MODSEC_RULE_ENGINE` on the nginx service.

**Paranoia level**: 1 (CRS default). Higher levels add stricter heuristics but require more exclusions for a JSON API.

**Audit log**: written to container stdout as JSON. Read with `docker logs nginx`, parse with `docker logs nginx | jq 'select(.transaction)'`.

**Custom rule**: `docker/nginx/modsec/custom-rules.conf` contains the Kap4 slide 4-19 demonstration rule (`id:1000`) that denies any request whose URI contains `<script>`.

**False-positive exclusions**: live in `docker/nginx/modsec/exclusions.conf`. Each entry must cite the CRS rule ID it disables and the legitimate request that triggered it.

**Path note**: Kap4 slide 4-17 prescribes config paths under `/etc/nginx/modsecurity/`. The OWASP-maintained image places the same files under `/etc/modsecurity.d/`. The directives, rule set, and audit-log format all match the slide; only the in-container filesystem layout differs.

## Data Flow
### Sensor ingestion
```
Pico (sensor01)
    │
    │ MQTT/TLS publish to sensor01/data
    ▼
mosquitto
    │
    │ MQTT subscription
    ▼
controller.py
    │
    │ HTTPS POST /api/v1/sensor-data
    │ Authorization: Bearer <JWT>
    ▼
nginx
    │
    │ proxy_pass → backend:8080
    ▼
backend (Zig API)
    │
    │ INSERT INTO sensor_data
    ▼
postgres
```

Sensor data is never written directly to PostgreSQL by the controller.  
All persistence happens through the backend API.

The controller:
- validates MQTT payloads
- authenticates against the API
- forwards readings via HTTPS/TLS
- retries login if JWT tokens expire

### Dashboard read

```
Browser
    │
    │ HTTPS GET /api/v1/sensor-data
    ▼
nginx
    ▼
backend (JWT validation)
    ▼
postgres
```

### Actuator commands

```
Browser
    │
    │ HTTPS POST /api/v1/actuator-command
    ▼
nginx
    ▼
backend
    ▼
postgres.actuator_commands
```

Actuator commands are queued through the backend API and stored centrally in PostgreSQL.

> Note: Sensor ingestion has been fully refactored to use the API path (`controller → nginx → backend → postgres`). Actuator delivery can later be implemented through a dedicated dispatcher service or a future controller extension.