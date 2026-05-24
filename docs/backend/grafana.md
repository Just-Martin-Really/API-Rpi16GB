# Grafana

Grafana renders the Prometheus + Postgres data into dashboards and gates
access through Keycloak OIDC. The Prometheus stack is documented separately
in [observability.md](observability.md); this page covers Grafana itself.

## What's wired

- Container `grafana/grafana:11.3.0` on `app-net`, persistent volume
  `grafana_data`.
- Two provisioned datasources: `Prometheus` (default) and `Postgres`
  (read-only via `grafana_read_user`). Both come up from
  `docker/grafana/provisioning/datasources/` on container start; users
  cannot edit them through the UI.
- Six provisioned dashboards in `docker/grafana/provisioning/dashboards/`:

  | UID             | Title                       | Datasource(s)        | Status                   |
  |-----------------|-----------------------------|----------------------|--------------------------|
  | `system-health` | System Health (Pi host)     | Prometheus           | live                     |
  | `service-health`| Service Health (Zig backend)| Prometheus           | live                     |
  | `lstm`          | LSTM Control Loop           | Prometheus           | live                     |
  | `postgres`      | Postgres (sensor DB)        | Prometheus           | live                     |
  | `sensoren`      | Sensor-Daten (Postgres)     | Postgres             | live                     |
  | `actuator`      | Actuator (controller.py) STUB | Prometheus         | placeholder, see below   |

- Keycloak OIDC integration via `GF_AUTH_GENERIC_OAUTH_*` env vars. Realm
  roles map to Grafana roles:

  | Realm role        | Grafana role  |
  |-------------------|---------------|
  | `admin-user`      | `GrafanaAdmin`|
  | `dashboard-user`  | `Editor`      |
  | anything else     | `Viewer`      |

  Anonymous access and self-signup are disabled.

## Bootstrap

Grafana needs three secret files before the container will start.

```sh
cd docker/

# Admin user password (used only if OIDC is misconfigured / offline)
openssl rand -base64 32 > secrets/grafana_admin_password.txt

# Keycloak client secret. Value MUST match what is hardcoded as
# "sc_grafana_client" in keycloak/iot-realm.json. The realm import sets
# the client secret to that literal string, and Grafana reads its own
# copy from this file at startup.
#
# WARNING: use `printf`, not `echo`. `echo` appends a trailing newline,
# which Grafana sends verbatim to Keycloak's token endpoint, and Keycloak
# rejects the login with a generic `invalid_client` error. If OIDC login
# fails with no obvious cause, this is the first thing to check.
printf 'sc_grafana_client' > secrets/grafana_oidc_client_secret.txt

# Read-only Postgres password for the Grafana datasource
openssl rand -base64 32 > secrets/db_grafana_password.txt
```

Bring the container up and apply the Postgres password to the new role:

```sh
docker compose up -d grafana
sh ./set_passwords.sh
```

`set_passwords.sh` is idempotent; re-running it after the Grafana addition
just re-applies the `ALTER USER grafana_read_user WITH PASSWORD ...` line
alongside the existing ones.

Grafana is reachable at `http://<pi-ip>:3000` during development. The
`3000:3000` host-port mapping in `docker-compose.yml` is **temporary**, see
below.

## OIDC login flow

```
Browser → http://pi:3000/login              (Grafana login page)
       ← redirect → /auth/realms/iot/protocol/openid-connect/auth
                    (Keycloak login form)
       → POST credentials → Keycloak
       ← redirect with code → http://pi:3000/login/generic_oauth?code=...
       → Grafana exchanges code for tokens
       → Grafana reads realm_access.roles from the access token
       → role_attribute_path JMESPath assigns Editor / Viewer / GrafanaAdmin
```

The redirect URI in the `grafana-client` Keycloak client is intentionally a
list of three patterns so the same realm import works in three contexts:

| Pattern                          | When it matches                                              |
|----------------------------------|--------------------------------------------------------------|
| `http://localhost:3000/*`        | dev access via the temporary host-port mapping               |
| `https://backend.lab.local/*`    | current Pi hostname before Amica's `www.lab.local` switch    |
| `https://www.lab.local/*`        | future production hostname (Amica's PR)                      |

When Amica's nginx route lands and Grafana moves behind `/grafana/`, only
two things need to change: drop the `3000:3000` port from compose, and set
`GF_SERVER_ROOT_URL` to `https://www.lab.local/grafana/`. No realm changes.

## How to add a dashboard

1. Build the dashboard in the Grafana UI (`+` → New dashboard).
2. Open it → cog icon → JSON Model. Copy the JSON.
3. Save as `docker/grafana/provisioning/dashboards/<slug>.json`, **strip the
   `id` and `version` fields** at the top (they conflict with provisioning),
   and add a stable `uid` if the export doesn't have one.
4. Commit. The dashboard re-appears automatically; provisioning rescans
   every 30 seconds (`updateIntervalSeconds: 30` in `dashboards.yml`).

The Grafana folder used by all provisioned dashboards is `API-Rpi16GB`.

## What is intentionally not yet implemented

- **nginx route + TLS termination** for `/grafana/`. That belongs to
  Amica's task `869dcz0np` (nginx, TLS, hostname switch). Until then, port
  3000 is mapped to the host.
- **Actuator dashboard data**. Panels reference `controller_*` metrics
  that don't exist yet. Wait for the controller.py `/metrics` follow-up
  PR, then the panels populate without any Grafana change. The dashboard
  is shipped now so the layout is reviewable.
- **Alerts**. Optional per the Phase 6 spec. Will live in
  `docker/grafana/provisioning/alerting/` when added.

## Verification

After the container is healthy:

```sh
# Datasources
curl -s -u admin:"$(cat docker/secrets/grafana_admin_password.txt)" \
  http://localhost:3000/api/datasources | jq '.[] | {name, type, url}'

# Dashboards
curl -s -u admin:"$(cat docker/secrets/grafana_admin_password.txt)" \
  http://localhost:3000/api/search?type=dash-db | jq '.[] | {uid, title}'

# Test Prometheus query through Grafana
curl -s -u admin:"$(cat docker/secrets/grafana_admin_password.txt)" \
  'http://localhost:3000/api/datasources/proxy/uid/prometheus/api/v1/query?query=up' \
  | jq '.data.result | length'
```

Expected output of the first two: six dashboards (`system-health`,
`service-health`, `lstm`, `postgres`, `sensoren`, `actuator`) and two
datasources. The third should return the number of scrape targets that
Prometheus considers up — five once everything is running
(prometheus self, backend, lstm, postgres_exporter, node_exporter).

For the OIDC flow, open `http://<pi-ip>:3000/` in a browser, click "Sign in
with Keycloak", log in as `iotuser01` (`Test1234!`), and verify the role
shown in the top-right is "Editor".
