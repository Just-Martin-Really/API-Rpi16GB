import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 8883))
MQTT_USER = open("/run/secrets/mqtt_controller_user").read().strip()
MQTT_PASS = open("/run/secrets/mqtt_controller_password").read().strip()

API_BASE_URL = os.environ.get("API_BASE_URL", "https://nginx")
API_USERNAME = os.environ.get("API_USERNAME", "controller_service")
API_PASSWORD = open("/run/secrets/api_password").read().strip()

# Internal Docker bridge connection. The CA pin authenticates nginx;
# the SAN of the public cert does not include the internal hostname.
API_SSL_CONTEXT = ssl.create_default_context()
API_SSL_CONTEXT.check_hostname = False
API_SSL_CONTEXT.load_verify_locations("/run/secrets/ca_cert")

TOKEN = None
VALID_UNITS = ("C", "%")
ACTUATOR_POLL_INTERVAL = 2


def _request(method, path, payload=None, token=None):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=API_SSL_CONTEXT, timeout=10) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600:
                last_err = e
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(2 ** attempt)
            continue
    raise last_err


def api_post(path, payload, token=None):
    return _request("POST", path, payload=payload, token=token)


def api_get(path, token=None):
    return _request("GET", path, token=token)


def login():
    global TOKEN
    response = api_post(
        "/auth/login",
        {"username": API_USERNAME, "password": API_PASSWORD},
    )
    token = response.get("token")
    if not token:
        raise RuntimeError(f"login response missing 'token' field: {response}")
    TOKEN = token
    print("Logged in to Zig API via nginx", flush=True)


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to broker", flush=True)
        client.subscribe("+/data")
    else:
        print(f"Broker connection failed: rc={rc}", flush=True)


def on_message(client, userdata, msg):
    global TOKEN

    try:
        payload = json.loads(msg.payload.decode())
        sensor_id = msg.topic.split("/")[0]
        value = float(payload["value"])
        unit = str(payload["unit"])

        if unit not in VALID_UNITS:
            raise ValueError(f"invalid unit: {unit}")
        if unit == "%" and not (0 <= value <= 100):
            raise ValueError(f"humidity out of range: {value}")
        if unit == "C" and not (-40 <= value <= 80):
            raise ValueError(f"temperature out of range: {value}")

        if TOKEN is None:
            login()

        body = {"sensor_id": sensor_id, "value": value, "unit": unit}
        try:
            api_post("/api/v1/sensor-data", body, token=TOKEN)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("Token expired or invalid, logging in again", flush=True)
                login()
                api_post("/api/v1/sensor-data", body, token=TOKEN)
            else:
                raise

        print(f"Forwarded {sensor_id}: {value} {unit} via nginx to Zig API", flush=True)

    except Exception as e:
        print(f"Error processing message: {e}", flush=True)


def dispatch_actuator_commands(client):
    global TOKEN
    try:
        try:
            rows = api_get("/api/v1/actuator-commands?unsent=true", token=TOKEN)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                login()
                rows = api_get("/api/v1/actuator-commands?unsent=true", token=TOKEN)
            else:
                raise

        for row in rows:
            topic = f"{row['actuator_id']}/data"
            client.publish(topic, json.dumps({"command": row["command"]}))
            api_post(
                "/api/v1/actuator-commands/mark-sent",
                {"id": row["id"]},
                token=TOKEN,
            )
            print(f"Dispatched {row['actuator_id']}: {row['command']}", flush=True)
    except Exception as e:
        print(f"Actuator dispatch error: {e}", flush=True)


def dispatch_loop(client):
    while True:
        time.sleep(ACTUATOR_POLL_INTERVAL)
        dispatch_actuator_commands(client)


def main():
    max_login_attempts = 10
    for attempt in range(max_login_attempts):
        try:
            login()
            break
        except Exception as e:
            backoff = min(2 ** attempt, 30)
            print(f"API not ready ({e}), retrying in {backoff}s", flush=True)
            time.sleep(backoff)
    else:
        print(f"Login failed after {max_login_attempts} attempts, exiting", flush=True)
        raise SystemExit(1)

    tls_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls_ctx.load_verify_locations("/run/secrets/ca_cert")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set_context(tls_ctx)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT)

    threading.Thread(target=dispatch_loop, args=(client,), daemon=True).start()

    client.loop_forever()


if __name__ == "__main__":
    main()
