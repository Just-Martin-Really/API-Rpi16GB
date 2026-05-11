import json
import os
import ssl
import time
import urllib.request
import urllib.error
import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 8883))
MQTT_USER = open("/run/secrets/mqtt_controller_user").read().strip()
MQTT_PASS = open("/run/secrets/mqtt_controller_password").read().strip()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://nginx")
API_USERNAME = os.environ.get("API_USERNAME", "admin")
API_PASSWORD = open("/run/secrets/api_password").read().strip()

TOKEN = None


def api_ssl_context():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations("/run/secrets/ca_cert")
    return ctx


def api_post(path, payload, token=None):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, context=api_ssl_context(), timeout=10) as response:
        body = response.read().decode("utf-8")
        if not body:
            return {}
        return json.loads(body)


def login():
    global TOKEN

    response = api_post(
        "/auth/login",
        {
            "username": API_USERNAME,
            "password": API_PASSWORD,
        },
    )

    TOKEN = response["token"]
    print("Logged in to Zig API via nginx", flush=True)


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to broker", flush=True)
        client.subscribe("sensor01/data")
    else:
        print(f"Broker connection failed: rc={rc}", flush=True)


def on_message(client, userdata, msg):
    global TOKEN

    try:
        payload = json.loads(msg.payload.decode())
        sensor_id = msg.topic.split("/")[0]
        value = float(payload["value"])
        unit = str(payload.get("unit", ""))

        if unit not in ("C", "%"):
            raise ValueError(f"invalid unit: {unit}")

        if unit == "%" and not (0 <= value <= 100):
            raise ValueError(f"humidity out of range: {value}")

        if unit == "C" and not (-40 <= value <= 80):
            raise ValueError(f"temperature out of range: {value}")

        if TOKEN is None:
            login()

        try:
            api_post(
                "/api/v1/sensor-data",
                {
                    "sensor_id": sensor_id,
                    "value": value,
                    "unit": unit,
                },
                token=TOKEN,
            )
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("Token expired or invalid, logging in again", flush=True)
                login()
                api_post(
                    "/api/v1/sensor-data",
                    {
                        "sensor_id": sensor_id,
                        "value": value,
                        "unit": unit,
                    },
                    token=TOKEN,
                )
            else:
                raise

        print(f"Forwarded {sensor_id}: {value} {unit} via nginx to Zig API", flush=True)

    except Exception as e:
        print(f"Error processing message: {e}", flush=True)


def main():
    while True:
        try:
            login()
            break
        except Exception as e:
            print(f"API not ready, retrying: {e}", flush=True)
            time.sleep(3)

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
