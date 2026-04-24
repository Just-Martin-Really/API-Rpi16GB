# API-Rpi16GB

Backend server for the DHBW Anwendungsprojekt. Runs on a Raspberry Pi 5 (16 GB).

## Stack

- **Zig 0.16.0** — HTTP API server
- **PostgreSQL 16** — sensor data storage
- **nginx** — TLS termination, reverse proxy
- **Docker** — container orchestration, two isolated bridge networks (`app-net`, `sensor-net`)

## Repo Layout

```
src/                        Zig source
  main.zig                  entry point
  server.zig                TCP listener + HTTP connection handling
  router.zig                URL dispatch
  db.zig                    libpq wrapper
  handlers/
    health.zig              GET /health
    sensor.zig              GET + POST /api/v1/sensor-data
docker/
  docker-compose.yml
  backend/Dockerfile        multi-stage: downloads Zig, compiles, produces minimal runtime image
  nginx/nginx.conf          reverse proxy + TLS + rate limiting
  postgres/init.sql         schema + DB users
  secrets/                  gitignored — create manually on the Pi
docs/
  architecture.md           system design, network topology, security principles
  setup.md                  install + first-run guide
  api.md                    HTTP API reference
build.zig                   Zig build script
Makefile                    local dev shortcuts (build, run)
```

## Deploy (on the Pi)

```sh
cd ~/API-Rpi16GB/docker
docker compose up --build -d
```

Docker downloads Zig, compiles the backend, and starts all services. See [docs/setup.md](docs/setup.md) for the full first-time setup.

## Local Dev (on the Mac)

```sh
make run
```
