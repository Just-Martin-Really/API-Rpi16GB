# Controller — Dokumentation (Phase 6)

> **Hinweis:** Dieses Dokument löst den `controller.py`-Abschnitt (Kapitel 4) der
> `webserver-changes-documentation.md` ab. Jene Datei beschreibt den Stand von
> Phase 5 (API-Key, `server.js`, `/api/internal/`). Ab Phase 6 authentifiziert
> sich der Controller über Keycloak — alle aktuellen Informationen stehen hier.

---

## 1. Was ist der Controller?

`controller.py` ist die Brücke zwischen dem MQTT-Broker und dem Zig-Backend.
Er läuft als eigener Docker-Container auf dem 16-GB-Raspberry-Pi-5 und übernimmt
drei Aufgaben:

- **Sensor-Daten empfangen:** Abonniert MQTT-Topics der Sensoren, puffert Temperatur
  und Luftfeuchtigkeit pro `sensor_id` und schickt beide Werte gemeinsam per HTTP
  ans Backend, sobald ein frisches Paar vorliegt.
- **Aktor-Befehle weiterleiten:** Pollt alle 2 Sekunden offene Befehle vom Backend
  und publiziert sie per MQTT an die Aktoren.
- **Sensor-Anfragen weiterleiten:** Gleiche Drain-Logik wie Aktor-Befehle, für
  manuelle Sensor-Anfragen (z.B. „sofort messen").

---

## 2. Warum wurde etwas geändert?

Bisher hat sich `controller.py` mit einem **statischen API-Key** (`x-api-key`-Header)
beim Backend authentifiziert. Das ist unsicher, weil:

- der Key nie abläuft und nirgendwo automatisch rotiert wird,
- kein Unterschied zwischen verschiedenen Clients möglich ist (jeder mit dem Key
  hat dieselben Rechte), und
- ein einmal kompromittierter Key dauerhaft gültig bleibt.

Ab Phase 6 authentifizieren sich alle Clients (Dashboard-Browser, LSTM-Service,
Controller) über **Keycloak** mit kurzlebigen Access-Tokens. Der Controller
verwendet den **OAuth2 Client-Credentials-Flow**, da er kein Benutzer-Login hat,
sondern ein Maschinenkonto ist.

---

## 3. Änderungen an `controller.py`

Die Datei wurde **komplett neu geschrieben** — nicht weil die bestehende Logik
falsch war, sondern weil der neue Keycloak-Authentifizierungscode (Token-Cache,
`_fetch_token()`, `_auth_headers()`, Retry-Logik) strukturell oben eingefügt
werden musste und sich nicht sinnvoll als einzelne Stelle patchen ließ.
Die MQTT-Logik, der Sensor-Puffer und die Drain-Funktionen wurden
dabei inhaltlich nicht verändert.

### 3.1 Entfernt

| Was | Warum |
|-----|-------|
| `API_KEY = open("/run/secrets/api_key").read()` | API-Key-Authentifizierung wird durch Keycloak ersetzt |
| `"x-api-key": API_KEY` in `api_post` und `api_get` | Header wird durch `Authorization: Bearer <token>` ersetzt |
| `/api/internal/sensordata` als POST-Endpunkt | Dieser interne Pfad wird nicht mehr verwendet; korrekter Pfad ist `/api/v1/sensor-data` |
| `/api/internal/actuator-commands` und `/api/internal/sensor-requests` | Gleicher Grund — Pfade auf `/api/v1/...` korrigiert |

### 3.2 Hinzugefügt

#### Keycloak-Konfigurationsvariablen

```python
KEYCLOAK_TOKEN_URL          # Endpunkt für Token-Requests gegen Keycloak
CONTROLLER_CLIENT_ID        # "controller-client" (in iot-realm.json angelegt)
CONTROLLER_CLIENT_SECRET_FILE  # Pfad zum Docker-Secret mit dem Client-Passwort
TOKEN_REFRESH_MARGIN_SECONDS   # 30s Puffer vor Token-Ablauf
```

#### Token-Cache (`_token`, `_token_expires_at`)

Das Access-Token wird im Speicher gehalten, damit nicht bei jedem API-Call
ein neues Token geholt werden muss. Zwei Modul-Variablen speichern Token
und Ablaufzeitpunkt.

#### `_fetch_token()`

Holt ein neues Token von Keycloak mit `grant_type=client_credentials`.
Speichert Token und berechneten Ablauf-Timestamp im Modul-State.

#### `_auth_headers()`

Gibt `{"Authorization": "Bearer <token>"}` zurück. Ruft automatisch
`_fetch_token()` auf, wenn kein Token vorhanden oder es bald abläuft.
Wird von `api_post` und `api_get` bei jedem Request aufgerufen.

#### `_force_refresh()`

Setzt Token und Ablaufzeit zurück. Wird bei einem `401`-Fehler aufgerufen,
damit beim nächsten `_auth_headers()`-Aufruf zwingend ein neues Token
geholt wird.

#### 401/403-Fehlerbehandlung in `api_post` / `api_get`

| HTTP-Code | Bedeutung | Reaktion |
|-----------|-----------|----------|
| `401` | Token abgelaufen oder revoziert | `_force_refresh()` + einmaliger Retry |
| `403` | Falsche Rolle oder Client nicht autorisiert | Abbruch + Fehlermeldung im Log |

Die innere Request-Logik wurde in eine lokale `_do_request()`-Hilfsfunktion
ausgelagert, damit der Retry-Code nicht doppelt geschrieben werden muss.

### 3.3 Nicht geändert

- MQTT-Verbindungslogik (`on_connect`, `on_message`, TLS-Setup)
- Sensor-Datenpuffer und Frische-Logik (`pending`, `FRESHNESS_SECONDS`)
- Drain-Funktionen für Aktor-Befehle und Sensor-Anfragen (nur Pfade korrigiert)
- Haupt-Loop in `main()`

---

## 4. Änderungen an `docker-compose.yml`

### 4.1 Globaler `secrets:`-Block

```yaml
# Neu hinzugefügt:
keycloak_controller_secret:
  file: ./secrets/keycloak_controller_secret.txt
```

**Warum:** Docker Secrets müssen zuerst global deklariert werden, bevor ein
Service sie referenzieren kann. Der Wert in der Datei muss `sc_controller_client`
sein — derselbe Wert, der in `docker/keycloak/iot-realm.json` für den
`controller-client` hinterlegt ist.

### 4.2 `controller`-Service — `environment`

```yaml
# Neu hinzugefügt:
KEYCLOAK_TOKEN_URL: https://www.lab.local/auth/realms/iot/protocol/openid-connect/token
CONTROLLER_CLIENT_ID: controller-client
CONTROLLER_CLIENT_SECRET_FILE: /run/secrets/keycloak_controller_secret
```

**Warum:** `controller.py` liest diese Werte per `os.environ.get()`. Durch
Umgebungsvariablen statt hartkodierten Werten lässt sich der Service für
andere Umgebungen (Staging, Tests) einfach umkonfigurieren.

### 4.3 `controller`-Service — `secrets`

```yaml
# Entfernt:
- api_key

# Neu:
- keycloak_controller_secret
```

**Warum:** Der API-Key wird vom Controller nicht mehr benötigt. Das
Keycloak-Secret wird als Docker Secret gemountet, damit es nie als
Umgebungsvariable im Klartext sichtbar ist (`docker inspect` zeigt Env-Variablen,
aber keine Secret-Inhalte).

### 4.4 `controller`-Service — `depends_on`

```yaml
# Neu hinzugefügt:
keycloak:
  condition: service_healthy
```

**Warum:** Beim ersten Start versucht `controller.py` sofort ein Token zu
holen. Wenn Keycloak noch nicht bereit ist, schlägt das fehl und der
Container crasht (was zu Restart-Loops führen kann). Mit `service_healthy`
wartet Docker, bis Keycloak seinen Healthcheck besteht, bevor der Controller
gestartet wird.

---

## 5. Keycloak-Konfiguration

| Parameter | Wert |
|-----------|------|
| Realm | `iot` |
| Client-ID | `controller-client` |
| Grant-Type | `client_credentials` |
| Realm-Rolle | `controller-ingest` (wird vom Zig-Router geprüft) |
| Secret-Quelle | `/run/secrets/keycloak_controller_secret` |

Der Secret-Wert `sc_controller_client` ist in `docker/keycloak/iot-realm.json`
hinterlegt (Feld `secret` beim Client `controller-client`).

### Secret einmalig auf dem Pi anlegen

```sh
echo "sc_controller_client" > ~/API-Rpi16GB/docker/secrets/keycloak_controller_secret.txt
chmod 600 ~/API-Rpi16GB/docker/secrets/keycloak_controller_secret.txt
```

---

## 6. API-Endpunkte

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `POST` | `/api/v1/sensor-data` | Messwert-Paar (Temp + Feuchte) speichern |
| `GET`  | `/api/v1/actuator-commands` | Offene Aktor-Befehle abholen |
| `POST` | `/api/v1/actuator-commands/sent` | Befehl als gesendet markieren |
| `GET`  | `/api/v1/sensor-requests` | Offene Sensor-Anfragen abholen |
| `POST` | `/api/v1/sensor-requests/sent` | Anfrage als gesendet markieren |

---

## 7. Dateiübersicht

| Datei | Änderung |
|-------|----------|
| `docker/controller/controller.py` | Neu geschrieben — Keycloak statt API-Key, Endpunkte korrigiert |
| `docker/docker-compose.yml` | Angepasst — `api_key` raus, `keycloak_controller_secret` rein, `depends_on` ergänzt |
| `docs/backend/controller.md` | Neu angelegt — ersetzt Kapitel 4 der `webserver-changes-documentation.md` |