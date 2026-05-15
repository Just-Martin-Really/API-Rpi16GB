"""
controller_lstm.py — Runtime service (runs 24/7 inside a Docker container).
 
What it does on each incoming temperature reading:
 
  1. Update the timestamp of the last message (for the watchdog).
  2. Run anomaly checks; publish on sensor/alert if anything is off.
  3. Append the value to the sliding window.
  4. If enough samples are buffered AND enough time has passed since
     the last decision, run a forecast.
  5. Inspect the forecast (peak, trough) and decide on an action.
  6. Publish the action on actuator/control.
 
A separate watchdog thread fires a TIMEOUT alert if too much time
elapses without a message.
"""
 
import hashlib
import json
import os
import sys
import threading
import time
from collections import deque

import numpy as np
from tensorflow.keras.models import load_model

import config
from anomaly import AlertType, classify_anomaly, load_baseline
from autoencoder import load_autoencoder_baseline, reconstruction_mse
from client import TlsMqttClient
from forecast import forecast_future
from preprocessing import load_scaler, scale
 
 
def main() -> int:
    # Load artifacts produced by train_LSTM.py
    print("[startup] loading model + scaler + baseline...")
    model = load_model(config.MODEL_PATH)
    scaler = load_scaler(config.SCALER_PATH)
    baseline = load_baseline()
    residual_threshold = float(baseline["threshold"])
    print(f"          residual_threshold={residual_threshold:.6f}")

    # Load the Seq2Seq autoencoder (trained alongside the main model).
    autoencoder = load_model(config.AUTOENCODER_PATH)
    ae_baseline = load_autoencoder_baseline()
    ae_threshold = float(ae_baseline["threshold"])
    print(f"          autoencoder reconstruction_threshold={ae_threshold:.6f}")
 
    # Runtime state 
    # Sliding window of the last SEQ_LENGTH measurements (in °C, unscaled).
    window: deque[float] = deque(maxlen=config.SEQ_LENGTH)
    # Recent rounded readings — used by the STUCK detector.
    stuck_history: deque[float] = deque(maxlen=config.STUCK_THRESHOLD)
 
    # State that mutates across messages. Wrapped in a dict so the inner
    # callbacks can update it without nonlocal gymnastics.
    state = {
        "last_message_ts": time.time(),
        "last_prediction": None,           # in °C, used by residual check
        "last_decision_ts": 0.0,           # rate-limits the forecast loop
    }
 
    # MQTT client
    client = TlsMqttClient()

    # Publish model version as a retained message so any subscriber can
    # always query which model is currently running.
    model_hash = hashlib.md5(
        open(config.MODEL_PATH, "rb").read()
    ).hexdigest()[:8]
    version_payload = json.dumps({
        "model_hash": model_hash,
        "trained_at": os.path.getmtime(config.MODEL_PATH),
        "config": {
            "seq_length": config.SEQ_LENGTH,
            "horizon_minutes": config.FORECAST_MINUTES,
            "lstm_units": config.LSTM_UNITS,
        },
    })

    def publish_alert(alert: AlertType, value):
        client.publish(
            config.TOPIC_SENSOR_ALERT,
            json.dumps({
                "type": alert.value,
                "value": value,
                "timestamp": time.time(),
            }),
        )
 
    # ---- Main message handler ----------------------------------------
    def on_temperature(payload: bytes) -> None:
        if config.DEBUG:
            print(f"[recv] {payload!r}")

        try:
            value = float(payload.decode("utf-8").strip())
        except (UnicodeDecodeError, ValueError):
            print(f"[warn] bad payload: {payload!r}")
            return

        if config.DEBUG:
            print(f"[parsed] {value}")

        now = time.time()
        prev_message_ts = state["last_message_ts"]
        state["last_message_ts"] = now

        # 1. Anomaly detection BEFORE we let the value into the model.
        alert = classify_anomaly(
            value,
            state["last_prediction"],
            stuck_history,
            prev_message_ts,
            residual_threshold,
            now=now,
        )
        if alert is not None:
            publish_alert(alert, value)
            # Untrustworthy reading: do not feed it into the window.
            if alert == AlertType.PHYSICAL_OUT_OF_RANGE:
                return

        # 2. Update sliding window + stuck history.
        window.append(value)
        stuck_history.append(round(value, config.STUCK_PRECISION))

        if config.DEBUG:
            print(f"[window] len={len(window)}/{config.SEQ_LENGTH}")

        # 3. Need a full window AND control-interval has elapsed.
        if len(window) < config.SEQ_LENGTH:
            return
        if (now - state["last_decision_ts"]) < config.CONTROL_INTERVAL_SECONDS:
            return
        state["last_decision_ts"] = now

        # 4. Scale the current window and run the autoencoder anomaly check.
        window_arr = np.asarray(window, dtype=np.float32).reshape(-1, 1)
        window_scaled = scale(window_arr, scaler)

        mse = reconstruction_mse(autoencoder, window_scaled)
        if config.DEBUG:
            print(f"[autoencoder] mse={mse:.6f}  threshold={ae_threshold:.6f}")
        if mse > ae_threshold:
            publish_alert(AlertType.AUTOENCODER_RECONSTRUCTION, value)

        # 5. Forecast FORECAST_MINUTES steps ahead, in °C.
        predictions_c = forecast_future(
            model,
            window_scaled,
            minutes=config.FORECAST_MINUTES,
            alpha=config.SMOOTHING_ALPHA,
            scaler=scaler,
        )

        # Save the next-step prediction for the residual check on the next message.
        state["last_prediction"] = float(predictions_c[0])

        # 6. Decide based on the extremes of the forecast horizon.
        peak = float(predictions_c.max())
        trough = float(predictions_c.min())
        if config.DEBUG:
            print(f"[forecast] peak={peak:.2f}  trough={trough:.2f}")

        if peak > config.TARGET_MAX:
            cmd = config.CMD_FAN_ON
        elif trough < config.TARGET_MIN:
            cmd = config.CMD_HEATER_ON
        else:
            cmd = config.CMD_BOTH_OFF

        # 7. Publish control command.
        client.publish(config.TOPIC_ACTUATOR_CONTROL, cmd)
        print(
            f"[{int(now)}] T={value:5.2f}  "
            f"peak={peak:5.2f}  trough={trough:5.2f}  -> {cmd}"
        )

        # 8. Broadcast full forecast so dashboards can overlay predictions
        #    on the live temperature curve in real time.
        client.publish(
            config.TOPIC_LSTM_FORECAST,
            json.dumps({
                "timestamp": now,
                "horizon_minutes": config.FORECAST_MINUTES,
                "predictions": predictions_c.tolist(),
                "current_value": value,
                "decision": cmd,
            }),
        )
 
    # ---- Watchdog: stand-alone timeout detector -----------------------
    def watchdog() -> None:
        while True:
            time.sleep(30)
            now = time.time()
            if (now - state["last_message_ts"]) > config.TIMEOUT_SECONDS:
                publish_alert(AlertType.TIMEOUT, None)
 
    threading.Thread(target=watchdog, daemon=True).start()
 
    # ---- Wire up and run forever -------------------------------------
    client.subscribe(config.TOPIC_SENSOR_TEMPERATURE, on_temperature)
    client.connect()

    # Announce which model is running (retained → always available to new subscribers).
    client.publish(config.TOPIC_LSTM_VERSION, version_payload, retain=True)
    print(f"[startup] model version published (hash={model_hash})")

    print("[ready] LSTM controller running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[shutdown] disconnecting...")
        client.disconnect()
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())
 