import json
import os
import ssl
import time
import paho.mqtt.client as mqtt
import psycopg2

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 8883))
MQTT_USER = open("/run/secrets/mqtt_controller_user").read().strip()
MQTT_PASS = open("/run/secrets/mqtt_controller_password").read().strip()

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_NAME = os.environ.get("DB_NAME", "sensor")
DB_USER = "iot_write_user"
DB_PASS = open("/run/secrets/db_write_password").read().strip()


def db_connect():
    return psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to broker", flush=True)
        client.subscribe("sensor01/data")
    else:
        print(f"Broker connection failed: rc={rc}", flush=True)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        sensor_id = msg.topic.split("/")[0]
        value = float(payload["value"])
        unit = str(payload.get("unit", ""))

        conn = userdata["conn"]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sensor_data (sensor_id, value, unit) VALUES (%s, %s, %s)",
                (sensor_id, value, unit),
            )
        conn.commit()
        print(f"Stored {sensor_id}: {value} {unit}", flush=True)
    except Exception as e:
        print(f"Error processing message: {e}", flush=True)
        try:
            userdata["conn"].rollback()
        except Exception:
            pass


def dispatch_actuator_commands(client, conn):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, actuator_id, command FROM actuator_commands WHERE sent_at IS NULL ORDER BY issued_at"
            )
            rows = cur.fetchall()
        for row_id, actuator_id, command in rows:
            topic = f"{actuator_id}/data"
            payload = json.dumps({"command": command})
            client.publish(topic, payload)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE actuator_commands SET sent_at = NOW() WHERE id = %s",
                    (row_id,),
                )
            conn.commit()
            print(f"Dispatched {actuator_id}: {command}", flush=True)
    except Exception as e:
        print(f"Actuator dispatch error: {e}", flush=True)
        try:
            conn.rollback()
        except Exception:
            pass


def main():
    conn = None
    while conn is None:
        try:
            conn = db_connect()
            print("DB connected", flush=True)
        except Exception as e:
            print(f"DB not ready, retrying: {e}", flush=True)
            time.sleep(3)

    tls_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    tls_ctx.load_verify_locations("/run/secrets/ca_cert")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set_context(tls_ctx)
    client.user_data_set({"conn": conn})
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    while True:
        dispatch_actuator_commands(client, conn)
        time.sleep(2)


if __name__ == "__main__":
    main()
