# controller.py
# Aufgabe: Brücke zwischen MQTT-Broker und Zig-Backend-API.
#   - Empfängt Sensor-Messwerte per MQTT und schickt sie per HTTP ans Backend
#   - Holt Aktor-Befehle und Sensor-Anfragen vom Backend ab und publiziert
#     sie per MQTT an die entsprechenden Geräte
#
# Authentifizierung ab Phase 6: OAuth2 Client-Credentials-Flow gegen Keycloak
# statt statischem API-Key. Der Controller holt sich ein kurzlebiges
# Access-Token und hängt es als "Authorization: Bearer <token>" an jeden
# API-Request.

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

# Validierungs-Regexe
# Eingaben aus MQTT-Nachrichten werden vor Weiterverarbeitung geprüft,
# um ungültige IDs oder Befehle gar nicht erst ans Backend zu schicken.

ACTUATOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SENSOR_ID_RE   = re.compile(r"^[A-Za-z0-9_-]+$")
COMMAND_RE     = re.compile(r"^[A-Z0-9_]+$")

# MQTT-Verbindungsparameter
# Hostname und Port kommen aus Umgebungsvariablen (gesetzt in docker-compose.yml).
# Benutzername und Passwort werden aus Docker-Secrets gelesen – nie im Code
# oder in Umgebungsvariablen im Klartext.

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 8883))
MQTT_USER = open("/run/secrets/mqtt_controller_user").read().strip()
MQTT_PASS = open("/run/secrets/mqtt_controller_password").read().strip()

# Backend-API-Parameter
# API_BASE_URL zeigt auf nginx (Reverse Proxy), nicht direkt auf den
# Zig-Backend-Container. nginx prüft TLS und leitet weiter.

API_BASE_URL = os.environ.get("API_BASE_URL", "https://nginx")

# Keycloak-Parameter
# KEYCLOAK_TOKEN_URL: Endpunkt, an dem der Controller sein Access-Token holt.
#   Verwendet HTTP (intern, kein TLS nötig zwischen Docker-Containern).
# CONTROLLER_CLIENT_ID: der in Keycloak angelegte OAuth2-Client "controller-client".
# CONTROLLER_CLIENT_SECRET_FILE: Pfad zum Docker-Secret mit dem Client-Passwort.
#   Der Wert im Secret muss "sc_controller_client" sein (aus iot-realm.json).

KEYCLOAK_TOKEN_URL = os.environ.get(
    "KEYCLOAK_TOKEN_URL",
    "https://www.lab.local/auth/realms/iot/protocol/openid-connect/token",
)
CONTROLLER_CLIENT_ID = os.environ.get("CONTROLLER_CLIENT_ID", "controller-client")
CONTROLLER_CLIENT_SECRET_FILE = os.environ.get(
    "CONTROLLER_CLIENT_SECRET_FILE", "/run/secrets/keycloak_controller_secret"
)

# Token wird TOKEN_REFRESH_MARGIN_SECONDS vor Ablauf neu geholt,
# damit ein Request der kurz vor dem Ablauf ankommt noch ein gültiges Token hat.

TOKEN_REFRESH_MARGIN_SECONDS = 30

# Wie oft (in Sekunden) Aktor-Befehle und Sensor-Anfragen abgeholt werden.

ACTUATOR_POLL_SECONDS = 2

# Sensor-Datenpuffer
# Temperatur und Luftfeuchtigkeit kommen als separate MQTT-Nachrichten an.
# Wir puffern beide Werte pro sensor_id und senden erst, wenn beide da sind.
# Struktur: { "sensor01": { "temperature": (wert, zeitstempel), "humidity": (...) } }

pending = {}

# Messwerte die älter als FRESHNESS_SECONDS sind werden verworfen,
# damit keine veralteten Paare ans Backend geschickt werden.
# Knapp unter der 60s-Sendeperiode des Sensors.

FRESHNESS_SECONDS = 55


# Token-Cache
# Das Access-Token wird im Speicher gehalten (kein Redis, kein File –
# der Prozess ist kurzlebig und single-threaded).
# _token:             das aktuelle JWT-Access-Token als String
# _token_expires_at:  Unix-Timestamp, ab dem das Token abgelaufen ist

_token: str | None = None
_token_expires_at: float = 0.0


def _client_secret() -> str:
    """Liest das Client-Secret frisch aus dem Docker-Secret-File.
    Wird bei jedem Token-Fetch aufgerufen, damit ein rotiertes Secret
    sofort wirksam wird."""
    return open(CONTROLLER_CLIENT_SECRET_FILE).read().strip()


def _fetch_token() -> None:
    """Holt ein neues Access-Token von Keycloak via Client-Credentials-Flow.

    Client-Credentials = Maschinenkonto-Flow: kein Benutzer-Login, nur
    client_id + client_secret. Das Backend prüft dann, ob der Token die
    Realm-Rolle 'controller-ingest' enthält.

    Das Token und seine Ablaufzeit werden im Modul-State gespeichert."""
    global _token, _token_expires_at
    body = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     CONTROLLER_CLIENT_ID,
        "client_secret": _client_secret(),
    }).encode("utf-8")
    req = urllib.request.Request(
        KEYCLOAK_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, context=api_ssl_context(), timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _token = data["access_token"]
    expires_in = int(data.get("expires_in", 300))   # Keycloak-Standard: 300s
    _token_expires_at = time.time() + expires_in
    print(f"Keycloak: neues Token geholt, gültig für {expires_in}s", flush=True)


def _auth_headers() -> dict:
    """Gibt den Authorization-Header mit dem aktuellen Bearer-Token zurück.

    Holt automatisch ein neues Token, wenn:
        - noch kein Token vorhanden ist (erster Aufruf nach Start), oder
        - das Token weniger als TOKEN_REFRESH_MARGIN_SECONDS gültig ist."""
    global _token, _token_expires_at
    if _token is None or time.time() >= _token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
        _fetch_token()
    return {"Authorization": f"Bearer {_token}"}


def _force_refresh() -> None:
    """Invalidiert den Token-Cache, damit beim nächsten _auth_headers()-Aufruf
    zwingend ein neues Token geholt wird.

    Wird bei einem 401-Fehler aufgerufen: das Backend hat den Token abgelehnt
    (z.B. weil Keycloak ihn zwischenzeitlich revoziert hat)."""
    global _token, _token_expires_at
    _token = None
    _token_expires_at = 0.0


# TLS-Kontext für HTTPS-Requests ans Backend

def api_ssl_context():
    """Erstellt einen SSL-Kontext, der nur unserer eigenen CA vertraut.
    Das CA-Zertifikat liegt als Docker-Secret unter /run/secrets/ca_cert.
    Ohne diesen Schritt würde urllib das selbstsignierte nginx-Zertifikat ablehnen."""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations("/run/secrets/ca_cert")
    return ctx


# HTTP-Hilfsfunktionen

def api_post(path, payload):
    """Sendet einen POST-Request ans Backend mit Bearer-Token-Authentifizierung.

    Fehlerbehandlung:
        401 → Token könnte revoziert sein → einmalig neues Token holen + Retry
        403 → Zugriff dauerhaft verweigert (falsche Rolle o.ä.) → Abbruch + Log
        Alles andere → Exception weiterwerfen (Caller entscheidet)"""
    data = json.dumps(payload).encode("utf-8")

    def _do_request():
        # Frische Auth-Header bei jedem Versuch, damit nach _force_refresh()
        # tatsächlich das neue Token verwendet wird.
        headers = {"Content-Type": "application/json", **_auth_headers()}
        req = urllib.request.Request(
            f"{API_BASE_URL}{path}",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, context=api_ssl_context(), timeout=10) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    try:
        return _do_request()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"api_post {path}: 401 – Token abgelaufen, einmal wiederholen", flush=True)
            _force_refresh()
            return _do_request()   # zweiter Versuch mit frischem Token
        if e.code == 403:
            print(f"api_post {path}: 403 Forbidden – Zugriff verweigert, abbrechen", flush=True)
            raise
        raise


def api_get(path):
    """Sendet einen GET-Request ans Backend mit Bearer-Token-Authentifizierung.

    Gleiche Fehlerlogik wie api_post: 401 → Retry, 403 → Abbruch."""
    def _do_request():
        req = urllib.request.Request(
            f"{API_BASE_URL}{path}",
            headers={**_auth_headers()},
            method="GET",
        )
        with urllib.request.urlopen(req, context=api_ssl_context(), timeout=10) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    try:
        return _do_request()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"api_get {path}: 401 – Token abgelaufen, einmal wiederholen", flush=True)
            _force_refresh()
            return _do_request()
        if e.code == 403:
            print(f"api_get {path}: 403 Forbidden – Zugriff verweigert, abbrechen", flush=True)
            raise
        raise


# MQTT-Callbacks

def on_connect(client, userdata, flags, rc, properties=None):
    """Wird aufgerufen, sobald die MQTT-Verbindung steht (oder fehlschlägt).
    rc == 0 bedeutet Erfolg. Bei Erfolg abonnieren wir das Sensor-Datentopic."""
    if rc == 0:
        print("Connected to broker", flush=True)
        client.subscribe("sensor01/data")
    else:
        print(f"Broker connection failed: rc={rc}", flush=True)


def on_message(client, userdata, msg):
    """Wird für jede eingehende MQTT-Nachricht aufgerufen.

    Ablauf:
        1. JSON parsen, Wert und Einheit extrahieren
        2. Plausibilitätsprüfung (Wertebereich)
        3. Wert im pending-Buffer speichern
        4. Sobald Temperatur UND Luftfeuchtigkeit für einen Sensor vorhanden
            und frisch genug sind → gemeinsam ans Backend senden
        5. Buffer nach dem Senden (oder bei Fehler) leeren"""
    try:
        payload = json.loads(msg.payload.decode())
        value     = float(payload["value"])
        unit      = str(payload.get("unit", ""))
        sensor_id = msg.topic.split("/")[0]  # "sensor01" aus "sensor01/data"

        # Wertebereich prüfen, bevor wir puffern.
        if unit == "C":
            if not (-40 <= value <= 80):
                print(f"Temperatur außerhalb der Range für {sensor_id}: {value}", flush=True)
                return
            if sensor_id not in pending:
                pending[sensor_id] = {}
            pending[sensor_id]["temperature"] = (value, datetime.now(timezone.utc))

        elif unit == "%":
            if not (0 <= value <= 100):
                print(f"Luftfeuchtigkeit außerhalb der Range für {sensor_id}: {value}", flush=True)
                return
            if sensor_id not in pending:
                pending[sensor_id] = {}
            pending[sensor_id]["humidity"] = (value, datetime.now(timezone.utc))

        else:
            print(f"Unbekannte Einheit: {unit}", flush=True)
            return

        buf = pending.get(sensor_id, {})

        # Noch nicht beide Werte → warten auf die zweite Nachricht.
        if "temperature" not in buf or "humidity" not in buf:
            return

        temp_val, temp_time = buf["temperature"]
        hum_val,  hum_time  = buf["humidity"]
        now = datetime.now(timezone.utc)

        # Frische prüfen: Wenn ein Wert zu alt ist, ist das Paar nicht mehr
        # konsistent. Buffer leeren und auf neue Werte warten.
        temp_age = (now - temp_time).total_seconds()
        hum_age  = (now - hum_time).total_seconds()

        if temp_age > FRESHNESS_SECONDS or hum_age > FRESHNESS_SECONDS:
            print(
                f"Veraltete Daten für {sensor_id} "
                f"(temp: {temp_age:.0f}s, hum: {hum_age:.0f}s), verwerfe Buffer",
                flush=True,
            )
            pending.pop(sensor_id, None)
            return

        timestamp = now.isoformat()

        try:
            # Beide Werte vorhanden und frisch → POST ans Backend.
            # Endpunkt ist /api/v1/sensor-data — der Zig-Router prüft hier,
            # ob der Token controller-client als Audience/azp enthält
            # und die Realm-Rolle controller-ingest gesetzt ist.
            result = api_post(
                "/api/v1/sensor-data",
                {
                    "sensor_id":   sensor_id,
                    "temperature": temp_val,
                    "humidity":    hum_val,
                    "timestamp":   timestamp,
                },
            )
            print(f"Gespeichert: {sensor_id}: temp={temp_val} hum={hum_val} → {result}", flush=True)
        except urllib.error.HTTPError as e:
            print(f"HTTP-Fehler: {e.code}: {e.read().decode()}", flush=True)
        except Exception as e:
            print(f"Fehler beim Senden der Daten: {e}", flush=True)
        finally:
            # Buffer in jedem Fall leeren (Erfolg oder Fehler),
            # damit keine Altdaten das nächste Paar verfälschen.
            pending.pop(sensor_id, None)

    except Exception as e:
        print(f"Fehler beim Verarbeiten der Nachricht: {e}", flush=True)


# Drain-Funktionen

def drain_actuator_commands(client):
    """Holt alle offenen Aktor-Befehle vom Backend und publiziert sie per MQTT.

    Das Backend schreibt Befehle in eine DB-Tabelle. Der Controller pollt diese
    alle ACTUATOR_POLL_SECONDS Sekunden, sendet jeden Befehl per MQTT und
    markiert ihn dann als gesendet (sent_at wird gesetzt).

    Ungültige IDs oder Befehle werden übersprungen und sofort als gesendet
    markiert, damit sie nicht ewig in der Queue bleiben."""
    data = api_get("/api/v1/actuator-commands")
    rows = data.get("commands", [])
    for row in rows:
        row_id     = row["id"]
        actuator_id = row["actuator_id"]
        command    = row["command"]

        # Eingabe validieren bevor sie als MQTT-Topic oder Payload verwendet wird.
        if not ACTUATOR_ID_RE.match(actuator_id) or not COMMAND_RE.match(command):
            print(
                f"actuator skipped invalid row id={row_id}: "
                f"actuator_id={actuator_id!r} command={command!r}",
                flush=True,
            )
            api_post("/api/v1/actuator-commands/sent", {"id": row_id})
            continue

        topic   = f"{actuator_id}/data"
        payload = json.dumps({"command": command})
        info    = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=5)

        if not info.is_published():
            # MQTT-Publish fehlgeschlagen → nicht als gesendet markieren,
            # damit der nächste Poll-Durchlauf es erneut versucht.
            print(f"actuator publish failed: id={row_id}", flush=True)
            continue

        # Erst nach erfolgreichem MQTT-Publish als gesendet markieren.
        api_post("/api/v1/actuator-commands/sent", {"id": row_id})
        print(f"actuator sent: {actuator_id} <- {command} (id={row_id})", flush=True)


def drain_sensor_requests(client):
    """Holt alle offenen Sensor-Anfragen vom Backend und publiziert sie per MQTT.

    Gleiche Drain-Logik wie drain_actuator_commands, nur für das
    sensor-requests-Topic (z.B. "sensor01/request" mit Befehl "READ_NOW")."""
    data = api_get("/api/v1/sensor-requests")
    rows = data.get("requests", [])
    for row in rows:
        row_id    = row["id"]
        sensor_id = row["sensor_id"]
        command   = row["command"]

        if not SENSOR_ID_RE.match(sensor_id) or not COMMAND_RE.match(command):
            print(
                f"sensor-request skipped invalid row id={row_id}: "
                f"sensor_id={sensor_id!r} command={command!r}",
                flush=True,
            )
            api_post("/api/v1/sensor-requests/sent", {"id": row_id})
            continue

        topic   = f"{sensor_id}/request"
        payload = json.dumps({"command": command})
        info    = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=5)

        if not info.is_published():
            print(f"sensor-request publish failed: id={row_id}", flush=True)
            continue

        api_post("/api/v1/sensor-requests/sent", {"id": row_id})
        print(f"sensor-request sent: {sensor_id} <- {command} (id={row_id})", flush=True)


# Einstiegspunkt

def main():
    """Startet den Controller:
        1. TLS-Kontext für MQTT aufbauen (gleiche CA wie für HTTPS)
        2. MQTT-Client konfigurieren und verbinden
        3. MQTT-Loop im Hintergrund starten (on_message läuft in eigenem Thread)
        4. Haupt-Loop: alle ACTUATOR_POLL_SECONDS Aktor-Befehle und
            Sensor-Anfragen drainagen"""
    tls_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls_ctx.load_verify_locations("/run/secrets/ca_cert")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set_context(tls_ctx)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()  # startet einen Background-Thread für MQTT I/O

    while True:
        try:
            drain_actuator_commands(client)
        except Exception as e:
            print(f"actuator drain error: {e}", flush=True)
        try:
            drain_sensor_requests(client)
        except Exception as e:
            print(f"sensor-request drain error: {e}", flush=True)
        time.sleep(ACTUATOR_POLL_SECONDS)


if __name__ == "__main__":
    main()