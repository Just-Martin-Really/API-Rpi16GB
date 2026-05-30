# Keycloak-Integration — Änderungsprotokoll

Dieser Branch (`feat/max_keycloak`) integriert Keycloak 26.1 als zentralen
Identity-Provider in den bestehenden Docker-Compose-Stack.

---

## Übersicht der Änderungen

| Datei | Aktion | Beschreibung |
|---|---|---|
| `docker/docker-compose.yml` | geändert | neuer `keycloak-db`-Service, Keycloak-Service überarbeitet, Secrets erweitert |
| `docker/keycloak/iot-realm.json` | neu | Realm-Konfiguration für automatischen Import beim Start |
| `docker/secrets/keycloak_db_password.txt` | neu | Datenbankpasswort für `keycloak-db` (gitignored) |
| `docker/secrets/keycloak_controller_secret.txt` | neu | Client-Secret für `controller-client` (gitignored) |
| `docker/secrets/keycloak_lstm_secret.txt` | neu | Client-Secret für `lstm-client` (gitignored) |
| `docker/postgres/create_keycloak_db.sql` | gelöscht | durch `keycloak-db`-Service überflüssig — DB wird automatisch per `POSTGRES_DB` angelegt |
| `scripts/backup_keycloak_db.sh` | neu | pg_dump-Backup-Skript für `keycloak-db` nach `docker/backups/` |
| `docker/backups/.gitignore` | neu | verhindert versehentliches Committen von Backup-Dateien |
| `docs/backend/keycloak-backup.md` | neu | Backup- und Restore-Anleitung |
| `docs/backend/setup.md` | geändert | Keycloak-Secrets in 1.8, Realm-Verifikation in Part 2, Backup in Part 5 |
| `README.md` | geändert | Keycloak im Stack, neue Secrets, Link zur Backup-Doku |
| `docker/postgres/migrate_to_kc.sql` | gelöscht | doppelter, fehlerhafter Ersatz von `create_keycloak_db.sql` (ungültiges `\idempotent`) |
| `docker/keycloak/migrate_data_2_kc.sh` | gelöscht | manuelles Setup-Skript, das nur die obigen SQL-Dateien ausgeführt hat — vollständig abgelöst |

---

## Architektur

### Container-Übersicht (vorher → nachher)

```
VORHER
──────
postgres ──────────────── (enthielt auch keycloak-DB per init-Script)
keycloak ──────────────── (start-dev, hardcoded Passwörter, keine Healthchecks)


NACHHER
───────
postgres          ──────── Sensor-DB (unverändert)
keycloak-db       ──────── dedizierte Postgres-Instanz nur für Keycloak
keycloak          ──────── start-dev --import-realm, Secrets per File, Healthcheck
```

### Netzwerk- und Abhängigkeitsgraph

```
                    app-net
                      │
      ┌───────────────┼───────────────────┐
      │               │                   │
  postgres        keycloak-db         [ andere Services ]
      │               │
  backend          keycloak
  webserver        (depends_on keycloak-db: service_healthy)
  archiver
  lstm
  controller
  nginx
```

### Startup-Reihenfolge (relevante Services)

```
keycloak-db startet
      │
      └─► pg_isready -U keycloak -d keycloak
              │  (Healthcheck: interval 10s, retries 5, start_period 30s)
              ▼
          [healthy]
              │
      keycloak startet  (start-dev --import-realm)
              │
              ├─► Verbindet sich mit keycloak-db
              ├─► Liest /run/secrets/keycloak_db_password
              ├─► Führt DB-Migrationen durch
              └─► Importiert /opt/keycloak/data/import/iot-realm.json
                      │  (nur beim ersten Start, wenn Realm noch nicht existiert)
                      ▼
                  [healthy]  curl -sf http://localhost:8080/auth/realms/master
                      │  (Healthcheck: interval 30s, retries 10, start_period 120s)
```

---

## docker-compose.yml — konkrete Änderungen

### 1. Neues Volume

```yaml
volumes:
  postgres_data:
  keycloak_db_data:   # <── neu (Name laut Story-Definition)
```

### 2. Erweiteter Secrets-Block

```yaml
secrets:
  # ... bestehende Secrets ...
  keycloak_db_password:          # <── neu: DB-Passwort für keycloak-db
    file: ./secrets/keycloak_db_password.txt
  keycloak_controller_secret:    # <── neu: Client-Secret controller-client
    file: ./secrets/keycloak_controller_secret.txt
  keycloak_lstm_secret:          # <── neu: Client-Secret lstm-client
    file: ./secrets/keycloak_lstm_secret.txt
```

### 3. Postgres-Service (Haupt-DB)

Der Mount `create_keycloak_db.sql` wurde entfernt, da Keycloak nun eine
eigene Datenbank-Instanz (`keycloak-db`) nutzt.

```yaml
# ENTFERNT:
# - ./postgres/create_keycloak_db.sql:/docker-entrypoint-initdb.d/00_create_keycloak_db.sql:ro
```

### 4. Neuer Service: `keycloak-db`

```yaml
keycloak-db:
  image: postgres:16-alpine
  restart: unless-stopped
  environment:
    POSTGRES_DB: keycloak
    POSTGRES_USER: keycloak
    POSTGRES_PASSWORD_FILE: /run/secrets/keycloak_db_password
  secrets:
    - keycloak_db_password
  volumes:
    - keycloak_db_data:/var/lib/postgresql/data
  networks:
    - app-net
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U keycloak -d keycloak"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
```

### 5. Überarbeiteter Service: `keycloak`

| Eigenschaft | Vorher | Nachher |
|---|---|---|
| `command` | `start-dev` | `start-dev --import-realm` |
| `KC_DB_URL` | `jdbc:postgresql://postgres:5432/keycloak` | `jdbc:postgresql://keycloak-db:5432/keycloak` |
| `KC_DB_PASSWORD` | hardcoded `changeme_db_password` | per Entrypoint-Shellwrap: `export KC_DB_PASSWORD=$(cat /run/secrets/keycloak_db_password)` vor `kc.sh start-dev` |
| `KC_DB_PASSWORD_FILE` | — | nicht verwendet (Keycloak 26 ignoriert die `_FILE`-Konvention für DB-Credentials, daher der Shellwrap oben) |
| `KC_HOSTNAME` | — | `https://www.lab.local` |
| `KC_HTTP_RELATIVE_PATH` | — | `/auth` |
| `KC_HOSTNAME_STRICT` | — | `"false"` |
| `KC_PROXY_HEADERS` | — | `xforwarded` |
| `KC_HTTP_ENABLED` | — | `"true"` |
| `secrets` | — | `keycloak_db_password`, `keycloak_admin_password` |
| `volumes` | — | `iot-realm.json` → `/opt/keycloak/data/import/` |
| `healthcheck` | — | `curl -sf http://localhost:8080/auth/realms/master` |
| `depends_on` | `postgres` (kein condition) | `keycloak-db: condition: service_healthy` |

#### Hostname-, Pfad- und Proxy-Konfiguration

| Variable | Bedeutung | Wirkung |
|---|---|---|
| `KC_HOSTNAME=https://www.lab.local` | Öffentliche Frontend-URL, die Keycloak in Tokens und Redirects schreibt | `iss`-Claim wird zu `https://www.lab.local/auth/realms/iot` (Spec-konform zu Kap. 6, Folie 6-22) |
| `KC_HTTP_RELATIVE_PATH=/auth` | URL-Prefix für alle Keycloak-Endpunkte | Token/JWKS/Admin-UI hängen alle unter `/auth/*` — passt zu Chap6 Folien 6-19 und 6-21 |
| `KC_HOSTNAME_STRICT=false` | Erlaubt Aufrufe, die nicht über `KC_HOSTNAME` kommen (z. B. interner Container-DNS `keycloak:8080`) | LSTM und Controller können intern HTTP nutzen, ohne dass Keycloak per Hostname-Check ablehnt |
| `KC_PROXY_HEADERS=xforwarded` | Wertet `X-Forwarded-*`-Header aus, die nginx setzt | Keycloak erkennt die Original-HTTPS-Verbindung trotz internem HTTP zwischen nginx und Keycloak |
| `KC_HTTP_ENABLED=true` | Listener auf Port 8080 für Plain-HTTP | Ohne diesen Flag würde Keycloak 26 im `start-dev`-Modus zwar funktionieren, im späteren `start`-Modus aber HTTPS-only fahren |

---

## Realm-Konfiguration (`iot-realm.json`)

### Realm-Einstellungen

```
realm:               iot
sslRequired:         external
registrationAllowed: false
bruteForceProtected: true
```

> **Hinweis:** `sslRequired: external` verlangt HTTPS für externe (nicht-private)
> Clients und erlaubt gleichzeitig Plain-HTTP im internen `app-net`. nginx
> terminiert TLS am Edge (`https://www.lab.local`), Services wie `lstm` und
> `controller` reden intern HTTP gegen `keycloak:8080`.

### Rollen

```
Realm-Rollen
├── dashboard-user      → Browser-Nutzer des Dashboards
├── admin-user          → Administratoren
├── controller-ingest   → MQTT-Controller (Service-Account)
└── lstm-control        → LSTM-Service (Service-Account)
```

### Benutzer

| Username | Typ | Rolle | Passwort |
|---|---|---|---|
| `iotuser01` | normaler Nutzer | `dashboard-user` | `Test1234!` |
| `service-account-controller-client` | Service-Account | `controller-ingest` | — (OAuth2 Client Credentials) |
| `service-account-lstm-client` | Service-Account | `lstm-control` | — (OAuth2 Client Credentials) |

### Clients

```
dashboard-client
├── Typ:             Public Client (kein Secret)
├── Flow:            Authorization Code (Standard Flow)
├── redirectUris:    https://www.lab.local/*
└── Verwendung:      Browser-Login über OIDC
                     (PKCE empfohlen)

controller-client
├── Typ:             Confidential Client
├── Flow:            Client Credentials (kein User-Login)
├── Secret:          aus keycloak_controller_secret.txt
├── Service-Account: Rolle controller-ingest
└── Verwendung:      Controller-Service authentifiziert sich
                     maschinenweise gegen Keycloak

lstm-client
├── Typ:             Confidential Client
├── Flow:            Client Credentials (kein User-Login)
├── Secret:          aus keycloak_lstm_secret.txt
├── Service-Account: Rolle lstm-control
└── Verwendung:      LSTM-Service authentifiziert sich
                     maschinenweise gegen Keycloak
```

### OAuth2-Flows im Überblick

```
Browser-Login (dashboard-client)
─────────────────────────────────
Browser ──► nginx ──► Keycloak (Authorization Code + PKCE)
                          │
                     gibt JWT zurück
                          │
Browser ──► nginx ──► Backend (JWT im Authorization-Header)


Service-to-Service (controller-client / lstm-client)
─────────────────────────────────────────────────────
Service ──► Keycloak  POST /auth/realms/iot/protocol/openid-connect/token
                      grant_type=client_credentials
                      client_id=controller-client
                      client_secret=<aus Secret-Datei>
                          │
                     gibt Access-Token zurück
                          │
Service ──► Backend   Authorization: Bearer <token>
```

---

## Secret-Dateien

Alle Dateien liegen in `docker/secrets/` und sind per `.gitignore` ausgeschlossen.

| Datei | Verwendet von | Inhalt |
|---|---|---|
| `keycloak_db_password.txt` | `keycloak-db` (`POSTGRES_PASSWORD_FILE`), `keycloak` (Shellwrap exportiert `KC_DB_PASSWORD` aus dieser Datei vor dem Start) | DB-Passwort |
| `keycloak_controller_secret.txt` | zukünftig: `controller`-Service | muss mit `secret` in `iot-realm.json` → `controller-client` übereinstimmen |
| `keycloak_lstm_secret.txt` | zukünftig: `lstm`-Service | muss mit `secret` in `iot-realm.json` → `lstm-client` übereinstimmen |

> **Wichtig:** Die Werte in den `.txt`-Dateien und in `iot-realm.json` müssen
> identisch sein. Wird ein Secret geändert, muss es in beiden Stellen aktualisiert
> und Keycloak neu gestartet werden (oder das Secret per Admin-API aktualisiert).

Aktueller Stand der hardcoded Secrets (`docker/keycloak/iot-realm.json`):

| Client | `secret` in `iot-realm.json` | Erwarteter Inhalt der `.txt`-Datei |
|---|---|---|
| `controller-client` | `sc_controller_client` | `sc_controller_client` |
| `lstm-client` | `sc_lstm_client` | `sc_lstm_client` |

> Diese Werte sind absichtlich keine Zufallsstrings — die Entscheidung gegen einen Vault/Entrypoint-Injection-Mechanismus ist in [`docker/keycloak/keycloak_secrets.md`](../../docker/keycloak/keycloak_secrets.md) dokumentiert. Vor dem Produktiveinsatz unbedingt rotieren.

---

## Realm-Import testen

```bash
# Nur die Keycloak-relevanten Services starten
cd docker
docker compose up keycloak-db keycloak -d

# Logs verfolgen (erster Start dauert ~2 Minuten)
docker compose logs -f keycloak

# Erfolgsmeldungen im Log:
#   "KC-Services-0050: Keycloak 26.1.x ... started"
#   "Realm 'iot' imported" (beim allerersten Start)

# Realm per REST-API prüfen
curl -s http://localhost:8080/auth/realms/iot | jq '{realm: .realm, sslRequired: .sslRequired}'
# Erwartete Ausgabe:
# {
#   "realm": "iot",
#   "sslRequired": "external"
# }

# Token für iotuser01 holen (testet Login-Flow)
curl -s -X POST http://localhost:8080/auth/realms/iot/protocol/openid-connect/token \
  -d "grant_type=password" \
  -d "client_id=dashboard-client" \
  -d "username=iotuser01" \
  -d "password=Test1234!" | jq .access_token

# Token für controller-client (Service-Account) holen
curl -s -X POST http://localhost:8080/auth/realms/iot/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=controller-client" \
  -d "client_secret=$(cat secrets/keycloak_controller_secret.txt)" | jq .access_token
```

---

## Entfernte Dateien

Drei Dateien wurden im Zuge dieser Integration gelöscht, weil sie durch den
dedizierten `keycloak-db`-Service vollständig abgelöst werden.

### `docker/postgres/create_keycloak_db.sql` (gelöscht)

```sql
CREATE DATABASE keycloak;
```

Zweck war, die `keycloak`-Datenbank auf der Haupt-Postgres-Instanz anzulegen.
Nicht mehr nötig: der `keycloak-db`-Service erstellt die Datenbank automatisch
über `POSTGRES_DB: keycloak` beim ersten Start.

### `docker/postgres/migrate_to_kc.sql` (gelöscht)

```sql
\idempotent

CREATE DATABASE keycloak;
```

Inhaltlich identisch mit `create_keycloak_db.sql`, zusätzlich noch fehlerhaft:
`\idempotent` ist kein gültiges psql-Kommando und hätte in einem echten Run
einen Fehler geworfen. Ebenfalls obsolet.

### `docker/keycloak/migrate_data_2_kc.sh` (gelöscht)

Das Skript führte nur die beiden SQL-Dateien oben per `docker exec` aus.
Da beide SQL-Dateien nicht mehr existieren und die Datenbankerstellung
vollautomatisch erfolgt, ist auch das Skript überflüssig.

**Vorher** (manueller Setup-Schritt nach `docker compose up`):
```
docker compose up -d
./keycloak/migrate_data_2_kc.sh   ← war nötig
```

**Nachher** (vollautomatisch beim Start):
```
docker compose up -d   ← keycloak-db legt DB an, Keycloak importiert Realm
```

---

## Vor dem Produktiveinsatz

- [ ] `keycloak_db_password.txt` und `keycloak_admin_password.txt` mit starken Zufallspasswörtern befüllen (siehe `docs/backend/setup.md` § 1.8)
- [ ] `keycloak_controller_secret.txt` und `keycloak_lstm_secret.txt` rotieren und in `iot-realm.json` synchron halten (Wert in beiden Stellen identisch)
- [ ] Ende-zu-Ende-Test mit aktiviertem nginx-TLS-Proxy: Browser-Login, Controller-Token, LSTM-Token (siehe Testmatrix in `INTEGRATION-TODOS.md` auf `integration/phase-6`)
- [ ] `command: start-dev` auf `command: start` umstellen, sobald `KC_HOSTNAME`/`KC_PROXY_HEADERS` produktiv getestet sind
- [ ] Passwort für `iotuser01` (`Test1234!`) ändern oder den Account deaktivieren
