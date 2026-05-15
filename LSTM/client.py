"""
client.py — TLS MQTT client wrapper around paho-mqtt 2.x.
 
Why a wrapper:
  - Keep TLS configuration in ONE place — change cert paths in config.py
    and every service picks it up.
  - Hide paho's slightly awkward callback API (with userdata, properties,
    reason_code, etc.) behind a small, friendly interface.
  - Easier to mock in tests.
 
paho-mqtt 2.x note:
  Starting in 2.x the client requires a CallbackAPIVersion. We pin to
  VERSION2 — the modern signatures with reason_code and properties.
"""
 
import ssl
from typing import Callable, Optional
 
import paho.mqtt.client as mqtt
 
import config
 
 
class TlsMqttClient:
    """
    Thin wrapper that handles TLS, auto-reconnect, and topic callbacks.
 
    Usage:
        client = TlsMqttClient()
        client.subscribe("sensor/temperature", on_temperature)
        client.connect()
        ...
        client.publish("actuator/control", "fan_on")
        ...
        client.disconnect()
    """
 
    def __init__(self,
                 host: Optional[str] = None,
                 port: Optional[int] = None,
                 client_id: Optional[str] = None,
                 ca_cert: Optional[str] = None,
                 client_cert: Optional[str] = None,
                 client_key: Optional[str] = None):
        self._host = host or config.MQTT_HOST
        self._port = port if port is not None else config.MQTT_PORT
        self._client_id = client_id or config.MQTT_CLIENT_ID
 
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
        )
 
        # TLS with mutual authentication: the broker verifies us via
        # client_cert/key, we verify the broker via the CA cert.
        self._client.tls_set(
            ca_certs=ca_cert or config.CA_CERT_PATH,
            certfile=client_cert or config.CLIENT_CERT_PATH,
            keyfile=client_key or config.CLIENT_KEY_PATH,
            tls_version=ssl.PROTOCOL_TLSv1_2,
        )

        # MQTT_TLS_INSECURE=1 bypasses hostname verification.
        # Use ONLY for local dev when the broker cert CN is "mosquitto" but
        # you connect via "localhost". Never set in production.
        if config.MQTT_TLS_INSECURE:
            self._client.tls_insecure_set(True)
            print("[MQTT] WARNING: TLS hostname verification is DISABLED (MQTT_TLS_INSECURE=1)")
 
        # paho retries automatically; just bound the backoff.
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)
 
        # Track subscriptions so we can re-subscribe on reconnect.
        self._subscriptions: dict[str, Callable[[bytes], None]] = {}
 
        # Wire up paho's base callbacks.
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
 
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"[MQTT] connected to {self._host}:{self._port}")
            # Re-subscribe everything (matters after a reconnect).
            for topic in self._subscriptions:
                client.subscribe(topic, qos=config.MQTT_QOS)
        else:
            print(f"[MQTT] connect failed, reason={reason_code}")
 
    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        print(f"[MQTT] disconnected, reason={reason_code}")
 
    def subscribe(self, topic: str, callback: Callable[[bytes], None]) -> None:
        """
        Register a callback for messages on `topic`. The callback receives
        only the raw payload (bytes); parsing is the caller's job so this
        wrapper stays domain-agnostic.
        """
        self._subscriptions[topic] = callback
 
        # paho's per-topic callback signature.
        def _on_message(_client, _userdata, message):
            try:
                callback(message.payload)
            except Exception as e:
                print(f"[MQTT] callback error on {topic}: {e}")
 
        self._client.message_callback_add(topic, _on_message)
 
        # If we are already connected, subscribe now; otherwise _on_connect
        # will pick it up when the connection completes.
        if self._client.is_connected():
            self._client.subscribe(topic, qos=config.MQTT_QOS)
 
    def publish(self, topic: str, payload, retain: bool = False) -> None:
        """Send a message. Payload can be str/bytes/int/float."""
        self._client.publish(topic, payload, qos=config.MQTT_QOS, retain=retain)
 
    def connect(self) -> None:
        """Open the TLS connection and start the background loop thread."""
        self._client.connect(self._host, self._port, config.MQTT_KEEPALIVE)
        self._client.loop_start()
 
    def disconnect(self) -> None:
        """Stop the loop and close the connection."""
        self._client.loop_stop()
        self._client.disconnect()