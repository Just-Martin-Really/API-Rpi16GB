# Backend-Server — Übergabe-Dokumentation

Dieses Dokument beschreibt, was ich auf dem 16-GB-Raspberry-Pi-5 gebaut habe, wie es entstanden ist, wie man es einrichtet, und was noch fehlt.

---

## Überblick

Das Projekt baut eine sichere IoT-Infrastruktur nach den Prinzipien Security-by-Design, Zero Trust und Least Privilege (Vorgabe von Prof. Dr. J. Schneider).

Drei Nodes kommunizieren über ein eigenes WLAN (`192.168.50.0/24`, SSID "Production"):

| Node | Hardware | Zuständigkeit |
|------|----------|---------------|
| WLAN-AP | RPi 5 2 GB | WLAN Access Point, Router, nftables-Firewall |
| Backend | RPi 5 16 GB | HTTP API, PostgreSQL, MQTT Broker — dieses Repo |
| MCU | RPi Pico WH | Sensoren/Aktoren, MicroPython |

Der WLAN-AP ist das einzige Gateway. Der Backend-Pi ist über das Internet nicht erreichbar.

---

## Was ich gebaut habe und wie

### Schritt 1 — Grundgerüst (Commit `d4b5db3`)

Erstes Backend-Scaffold: Zig 0.16.0 HTTP-Server, PostgreSQL 16, nginx als Reverse Proxy, alles in Docker mit einem Dockerfile als Multi-Stage-Build. Die Builder-Stage lädt Zig herunter und kompiliert den Source-Code, die Runtime-Stage kopiert nur das fertige Binary plus `libpq5`. Gleichzeitig Docker-Compose-Setup mit zwei Bridge-Netzwerken (`app-net`, `sensor-net`), Datenbankschema (`sensor_data`, `actuator_commands`, `dashboard_users`) und nginx-Config mit Rate Limiting und Subnet-Whitelist.

### Schritt 2 — Docker-Build-Fix für aarch64 (Commit `17ee027`)

Der Zig-Tarball-Name für ARM64 war falsch im Dockerfile — korrigiert auf den richtigen aarch64-Dateinamen für 0.16.0.

### Schritt 3 — WiFi-Problem und erster Setup-Guide (Commits `c0192e7`, `c4c882c`)

**Das größte Einzelproblem:** Der 16-GB-Pi konnte andere Nodes anpingen, war aber selbst nicht pingbar — also von außen nicht erreichbar, obwohl er im Netzwerk war. Habe lange gesucht, bis ich das herausgefunden habe:

Der WLAN-Chip des Pi geht standardmäßig nach ca. 2 Minuten in den Power-Save-Modus. Im Power-Save-Modus verpasst er eingehende Frames, solange er schläft. Ausgehende Verbindungen funktionieren trotzdem, weil der Pi dabei selbst aufwacht. Eingehende Pakete (Ping, SSH, HTTP) kommen aber nicht an.

Lösung: Power-Save dauerhaft deaktivieren über NetworkManager:

```sh
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf && sudo systemctl reload NetworkManager
```

Referenz: https://raspberrypi.stackexchange.com/questions/4773/raspberry-pi-sleep-mode-how-to-avoid

### Schritt 4 — Datenbank-Integration (Commit `65197d9`)

DB-Verbindung über libpq in Zig eingebaut. Sensor-Handler lesen jetzt echte Daten aus PostgreSQL. DB-Passwörter werden aus `docker/secrets/` gelesen (gitignored, werden einmalig auf dem Pi generiert).

### Schritt 5 — Zig-0.16.0-Breakages fixen (Commits `cce6b8c`, `d8c2afe`, `bed1439`, `5f021ba`)

Zig 0.16.0 hat viele Breaking Changes gegenüber früheren Versionen. Die sind mir beim Kompilieren im Container nacheinander aufgefallen:

- `std.fs.cwd()` und `openFileAbsolute` entfernt → C `fopen` via `@cImport(@cInclude("stdio.h"))`
- `std.mem.trimRight` entfernt → inline als Schleife
- libpq Include-Pfad im Container anders als lokal → `addIncludePath` in `build.zig` plattformabhängig
- `std.ArrayList(T).init(allocator)` entfernt → Fixed Buffer stattdessen

### Schritt 6 — Auth, Login-Handler, Aktor-Handler (untracked: `src/auth.zig`, `src/handlers/login.zig`, `src/handlers/actuator.zig`)

JWT-Implementierung in Zig (HS256, 24h TTL) ohne externe Bibliothek:
- `auth.zig`: Token ausstellen (`issueToken`) und validieren (`validateBearer`), Signatur-Vergleich konstant-zeitig per XOR-Akkumulation
- `login.zig`: `POST /auth/login` — SHA-256-Hash des Passworts gegen `dashboard_users` in der DB prüfen, bei Erfolg JWT zurückgeben
- `actuator.zig`: `POST /api/v1/actuator-command` — Befehl in `actuator_commands` schreiben, `controller.py` holt ihn ab und publiziert ihn per MQTT

### Schritt 7 — Mosquitto und Controller (untracked: `docker/controller/`, `docker/mosquitto/`)

- Mosquitto MQTT Broker mit TLS auf Port 8883, kein Anonymous-Zugang, ACL pro Sensor: `sensor01` darf nur auf `sensor01/data` schreiben, `controller` darf `sensor+/data` lesen und `actuator+/data` schreiben
- `controller.py`: abonniert alle Sensor-MQTT-Topics → schreibt in DB; pollt `actuator_commands` alle 2 Sekunden → publiziert per MQTT an die Aktoren, setzt `sent_at`

---

## Architektur

### Docker-Netzwerke

```
host (RPi 5 16 GB)
│
├── app-net (172.20.0.0/24)
│   ├── nginx      — Reverse Proxy, einziger Eintrittspunkt aus dem WLAN
│   ├── backend    — Zig HTTP-Server
│   ├── postgres   — nicht direkt vom Host erreichbar
│   ├── mosquitto  — erreichbar für controller
│   └── controller — MQTT→DB-Bridge
│
└── sensor-net (172.21.0.0/24)
    ├── mosquitto  — Port 8883 ins WLAN exponiert
    └── controller — abonniert Sensor-Topics, publiziert Aktor-Topics
```

`mosquitto` und `controller` sind in beiden Netzwerken. `postgres` ist nur in `app-net` — der Pico kann die DB nie direkt erreichen.

### Datenfluss

**Sensor-Messwert:**
```
Pico → MQTT/TLS (sensor01/data) → mosquitto → controller.py → INSERT sensor_data → postgres
```

**Dashboard lesen:**
```
Browser → HTTPS → nginx → GET /api/v1/sensor-data → backend (JWT-Check) → SELECT → postgres
```

**Aktor-Befehl:**
```
Browser → POST /auth/login → JWT
Browser → POST /api/v1/actuator-command → backend (JWT-Check) → INSERT actuator_commands
controller.py (pollt alle 2s) → MQTT publish actuator01/data → mosquitto → Pico
```

### Sicherheitsprinzipien

**Least Privilege:**
- `iot_write_user`: nur `INSERT`/`SELECT` auf `sensor_data` und `actuator_commands`
- `iot_read_user`: nur `SELECT` auf `sensor_data` und `dashboard_users`
- MQTT-ACL: ein Topic pro Sensor, pro Sensor eigene Credentials

**Complete Mediation:**
- nginx prüft jeden eingehenden Request (Rate Limiting, Subnet-Whitelist)
- JWT-Validierung auf jedem `/api/`-Endpunkt

**Least Common Mechanism:**
- Jeder Container im eigenen Netzwerksegment
- Zwei DB-User mit verschiedenen Rechten
- Kein Container läuft als root, kein `--privileged`

---

## Einrichtung (erster Start auf dem Pi)

### 1 — SD-Karte flashen

Raspberry Pi Imager (Dev-Rechner):
- Model: Raspberry Pi 5
- OS: Raspberry Pi OS Lite (64-bit)
- Einstellungen: Hostname `backend-server`, SSH aktivieren, Benutzername nicht `pi`/`admin`/`root`, Passwort mit Zahl und Sonderzeichen, Locale Europe/Berlin

### 2 — Erster Boot, System-Update

Pi direkt per Ethernet mit dem Mac verbinden (Internet Sharing am Mac aktivieren):

```sh
ssh <username>@backend-server.local
sudo apt update && sudo apt upgrade -y
```

### 3 — WLAN aktivieren

```sh
sudo iw reg set DE
sudo nmcli radio wifi on
```

### 4 — Mit Production-WLAN verbinden

```sh
sudo nmcli device wifi connect "Production" password "<wlan-passwort>"
sudo nmcli connection modify "Production" connection.autoconnect yes
```

Ab hier läuft alles über das Production-WLAN (`192.168.50.x`).

### 5 — WiFi Power-Save deaktivieren

Ohne diesen Schritt ist der Pi von außen nicht erreichbar (siehe oben).

```sh
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf && sudo systemctl reload NetworkManager
```

### 6 — Docker installieren

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Aus- und wieder einloggen, dann:

```sh
docker run --rm hello-world
```

### 7 — Repo klonen

```sh
sudo apt install -y git
git clone https://github.com/Just-Martin-Really/API-Rpi16GB.git ~/API-Rpi16GB
```

### 8 — Secrets erzeugen

Passwörter werden nie im Repo gespeichert. Einmalig auf dem Pi generieren:

```sh
mkdir -p ~/API-Rpi16GB/docker/secrets
chmod 700 ~/API-Rpi16GB/docker/secrets

echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/db_password.txt
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/db_write_password.txt
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/db_read_password.txt
echo "$(openssl rand -base64 48)" > ~/API-Rpi16GB/docker/secrets/jwt_secret.txt
echo "controller"                 > ~/API-Rpi16GB/docker/secrets/mqtt_controller_user.txt
echo "$(openssl rand -base64 24)" > ~/API-Rpi16GB/docker/secrets/mqtt_controller_password.txt

chmod 600 ~/API-Rpi16GB/docker/secrets/*.txt
```

### 9 — TLS-Zertifikate erzeugen

Erzeugt eine lokale CA, signiert ein Zertifikat für nginx (`backend.lab.local`) und eines für den MQTT Broker:

```sh
cd ~/API-Rpi16GB
sudo sh docker/setup_tls.sh
```

Das CA-Zertifikat muss danach auf den Pico und in jeden Browser kopiert werden, der das Dashboard öffnen soll.

### 10 — Host-Firewall

SSH nur aus dem Production-WLAN erlauben:

```sh
sudo iptables -A INPUT -p tcp --dport 22 -s 192.168.50.0/24 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j DROP
sudo apt install iptables-persistent -y
sudo netfilter-persistent save
```

### 11 — Stack starten

```sh
cd ~/API-Rpi16GB/docker
docker compose up --build -d
```

Beim ersten Mal dauert das mehrere Minuten (Zig ~90 MB herunterladen, kompilieren, Images bauen). Danach DB- und MQTT-Passwörter setzen:

```sh
sh set_passwords.sh
```

Prüfen:

```sh
docker compose ps
curl -k https://192.168.50.<backend-ip>/health
# → {"status":"ok"}
```

---

## Updates einspielen

```sh
cd ~/API-Rpi16GB && git pull
cd docker && docker compose up --build -d --no-deps backend
```

`--no-deps` baut nur den Backend-Container neu, ohne postgres oder nginx anzufassen.

---

## API

Basis-URL: `https://backend-server.local`

Alle `/api/`-Endpunkte brauchen `Authorization: Bearer <token>`.

### POST /auth/login

```json
{ "username": "admin", "password": "changeme" }
```

Antwort 200: `{"token": "<jwt>"}` — gültig 24 Stunden  
Antwort 401: `{"error": "unauthorized"}`

### GET /health

Kein Auth nötig. Antwort 200: `{"status": "ok"}`

### GET /api/v1/sensor-data

Alle Messwerte, neueste zuerst.

```json
[{ "id": 1, "sensor_id": "sensor01", "value": 23.4, "unit": "°C", "recorded_at": "2026-04-24T10:00:00Z" }]
```

### POST /api/v1/sensor-data

```json
{ "sensor_id": "sensor01", "value": 23.4, "unit": "°C" }
```

Antwort 201: `{"created": true}`

### POST /api/v1/actuator-command

```json
{ "actuator_id": "actuator01", "command": "on" }
```

Antwort 201: `{"queued": true}` — controller.py liefert den Befehl innerhalb ~2s per MQTT

---

## Datenbankschema

```sql
CREATE TABLE sensor_data (
    id          BIGSERIAL        PRIMARY KEY,
    sensor_id   VARCHAR(64)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        VARCHAR(16)      NOT NULL,
    recorded_at TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE TABLE actuator_commands (
    id          BIGSERIAL    PRIMARY KEY,
    actuator_id VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ           -- NULL bis controller.py den Befehl abgesendet hat
);

CREATE TABLE dashboard_users (
    id              BIGSERIAL    PRIMARY KEY,
    username        VARCHAR(64)  UNIQUE NOT NULL,
    password_sha256 CHAR(64)     NOT NULL
);
```

---

## Was noch fehlt / was ich als nächstes mache

- Die neu erstellten Dateien (`src/auth.zig`, `src/handlers/login.zig`, `src/handlers/actuator.zig`, `docker/controller/`, `docker/mosquitto/`, `docker/setup_tls.sh`) sind noch nicht committed
- End-to-End-Test auf dem echten Pi: Pico schickt MQTT → controller.py schreibt in DB → API liefert die Daten zurück
- Frontend/Dashboard existiert noch nicht — `dashboard_users` ist schon in der DB, aber es gibt noch keine Web-Oberfläche, die die Sensordaten anzeigt
- Die Verbindung zwischen WLAN-AP und Backend-Pi ist noch nicht final getestet (hängt auch vom AP-Repo ab)
