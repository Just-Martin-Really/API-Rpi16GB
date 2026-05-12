import json
import os
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 8883))
MQTT_USER = open("/run/secrets/mqtt_controller_user").read().strip()
MQTT_PASS = open("/run/secrets/mqtt_controller_password").read().strip()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://nginx")
API_KEY = open("/run/secrets/api_key").read().strip()

# Buffer until both measurement values for a round are available.
pending = {}


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

        if unit == "C":
            pending["temperature"] = value
        elif unit == "%":
            pending["humidity"] = value
        else:
            print(f"Unbekannte Einheit: {unit}", flush=True)
            return

        # Forward to server.js only when both values are present.
        if "temperature" in pending and "humidity" in pending:
            timestamp = datetime.now(timezone.utc).isoformat()
        
            try:
                result = api_post(
                    "/api/internal/sensordata",
                    {
                        "temperature": pending["temperature"],
                        "humidity": pending["humidity"],
                        "timestamp": timestamp,
                    },
                )
                print(f"Gespeichert: {pending} → {result}", flush=True)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    print(f"HTTP-Fehler: {e.code}: {e.read().decode()}", flush=True)
            except Exception as e:
                    print(f"Fehler beim Senden der Daten: {e}", flush=True)
            finally:
                pending.clear()  # Clear buffer after attempt

    except Exception as e:
        print(f"Fehler beim Verarbeiten der Nachricht: {e}", flush=True)


def main():
    tls_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls_ctx.load_verify_locations("/run/secrets/ca_cert")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set_context(tls_ctx)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_forever()


if __name__ == "__main__":
    main()
