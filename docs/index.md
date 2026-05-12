# Anwendungsprojekt

DHBW Anwendungsprojekt. Documentation for the IoT backend across three Raspberry Pis.

## Components

- **[Backend](backend/)** — RPi 5 (16 GB). Zig HTTP API, PostgreSQL, nginx, Mosquitto MQTT broker, Python controller, Docker stack.
- **[Router](router/)** — RPi 5 (2 GB). WLAN access point, DHCP/DNS via dnsmasq, nftables firewall.
- **[Pico](pico/)** — RPi Pico WH. MicroPython sensor/actuator node, MQTT over TLS to the backend.

## Data flow

```
Sensor (Pico)
    → MQTT over TLS
    → Mosquitto
    → Python Controller
    → HTTPS via nginx
    → Zig Backend API
    → PostgreSQL
```

---

## Contributing docs

This site builds from three separate repos:

- `API-Rpi16GB` — hosts the site, owns the build pipeline, content lives in `docs/backend/`
- `API-pico` — content auto-synced into `docs/pico/`
- `API-Rpi2GB` — content auto-synced into `docs/router/`

To add or update documentation for the pico or router, just add or edit `.md` files in those repos. Push to their main branch. The site rebuilds hourly and picks up the changes automatically. No PR to `API-Rpi16GB` needed.

The sync runs in `.github/workflows/docs.yml` and calls `scripts/sync-external-docs.sh`. The script clones the two external repos and mirrors every `.md` file, preserving the directory structure. A top-level `README.md` becomes that section's landing page (`index.md`). Anything else keeps its filename.

To trigger a rebuild without waiting for the hourly cron, run the workflow manually from the Actions tab in `API-Rpi16GB` (the workflow has `workflow_dispatch:` enabled).

### Local preview

```zsh
pip install zensical
./scripts/sync-external-docs.sh
zensical serve
```

The sync script writes into `docs/pico/` and `docs/router/`, which are gitignored. Run it once before `zensical serve`. Re-run when the external repos change.
