import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

ACTUATOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
COMMAND_RE = re.compile(r"^[A-Z0-9_]+$")

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 8883))
MQTT_USER = open("/run/secrets/mqtt_controller_user").read().strip()
MQTT_PASS = open("/run/secrets/mqtt_controller_password").read().strip()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://nginx")
API_KEY = open("/run/secrets/api_key").read().strip()

ACTUATOR_POLL_SECONDS = 2

# Buffer per sensor_id: { sensor_id: { "temperature": (value, timestamp), "humidity": (value, timestamp) } }
pending = {}

# Maximum age of buffered data - just under the 60s sensor refresh rate.
FRESHNESS_SECONDS = 55


def api_ssl_context():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations("/run/secrets/ca_cert")
    return ctx


def api_post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-api-key": API_KEY}
    req = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, context=api_ssl_context(), timeout=10) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def api_get(path):
    req = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        headers={"x-api-key": API_KEY},
        method="GET",
    )
    with urllib.request.urlopen(req, context=api_ssl_context(), timeout=10) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to broker", flush=True)
        client.subscribe("sensor01/data")
    else:
        print(f"Broker connection failed: rc={rc}", flush=True)


def on_message(client, userdata, msg):

    try:
        payload = json.loads(msg.payload.decode())
        value = float(payload["value"])
        unit = str(payload.get("unit", ""))
        sensor_id = msg.topic.split("/")[0]

        # Range checks before buffering.
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

        # Forward to server.js only when both values are present.
        if "temperature" not in buf or "humidity" not in buf:
            return
        
        temp_val, temp_time = buf["temperature"]
        hum_val, hum_time = buf["humidity"]
        now = datetime.now(timezone.utc)

        # Freshness check: discard outdated data.
        temp_age = (now - temp_time).total_seconds()
        hum_age = (now - hum_time).total_seconds()

        if temp_age > FRESHNESS_SECONDS or hum_age > FRESHNESS_SECONDS:
            print(f"Veraltete Daten für {sensor_id} (temp: {temp_age:.0f}s, hum: {hum_age:.0f}s), verwerfe Buffer", flush=True)
            pending.pop(sensor_id, None)
            return
        
        timestamp = now.isoformat()

        try:
            result = api_post(
                "/api/internal/sensordata",
                {
                    "sensor_id": sensor_id,
                    "temperature": temp_val,
                    "humidity": hum_val,
                    "timestamp": timestamp,
                },
            )
            print(f"Gespeichert: {sensor_id}: temp={temp_val} hum={hum_val} → {result}", flush=True)
        except urllib.error.HTTPError as e:
                print(f"HTTP-Fehler: {e.code}: {e.read().decode()}", flush=True)
        except Exception as e:
                print(f"Fehler beim Senden der Daten: {e}", flush=True)
        finally:
            pending.pop(sensor_id, None)

    except Exception as e:
        print(f"Fehler beim Verarbeiten der Nachricht: {e}", flush=True)


def drain_actuator_commands(client):
    data = api_get("/api/internal/actuator-commands")
    rows = data.get("commands", [])
    for row in rows:
        row_id = row["id"]
        actuator_id = row["actuator_id"]
        command = row["command"]
        if not ACTUATOR_ID_RE.match(actuator_id) or not COMMAND_RE.match(command):
            print(f"actuator skipped invalid row id={row_id}: actuator_id={actuator_id!r} command={command!r}", flush=True)
            api_post("/api/internal/actuator-commands/sent", {"id": row_id})
            continue
        topic = f"{actuator_id}/data"
        payload = json.dumps({"command": command})
        info = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            print(f"actuator publish failed: id={row_id}", flush=True)
            continue
        api_post("/api/internal/actuator-commands/sent", {"id": row_id})
        print(f"actuator sent: {actuator_id} <- {command} (id={row_id})", flush=True)


def main():
    tls_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls_ctx.load_verify_locations("/run/secrets/ca_cert")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set_context(tls_ctx)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    while True:
        try:
            drain_actuator_commands(client)
        except Exception as e:
            print(f"actuator drain error: {e}", flush=True)
        time.sleep(ACTUATOR_POLL_SECONDS)


if __name__ == "__main__":
    main()
