"""
config.py — Centralized configuration for the LSTM service.

Single source of truth for ALL constants used across the module:
paths, MQTT settings, model hyperparameters, control thresholds,
anomaly-detection limits, and simulation parameters.

Why one file?
  - Avoid duplication: if SEQ_LENGTH changes, only one line changes.
  - Keep training and runtime in sync: train_LSTM.py and controller_lstm.py
    MUST use the same SEQ_LENGTH and scaler, otherwise predictions break.
  - Easy to override per environment via OS environment variables
    (e.g. MQTT_HOST=mosquitto in Docker vs MQTT_HOST=localhost in dev).
"""

import os
from pathlib import Path


# PATHS
# Path(__file__) is the location of config.py itself. parent is the LSTM/
# folder. We resolve everything relative to that so the module works
# regardless of where you run python from.

BASE_DIR = Path(__file__).resolve().parent

# data/ holds artifacts produced by train_LSTM.py and consumed by
# controller_lstm.py. Mounted as a Docker volume so files survive
# container restarts.
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)        # auto-create on first run

MODEL_PATH = DATA_DIR / "model.keras"
SCALER_PATH = DATA_DIR / "scaler.npz"
ANOMALY_BASELINE_PATH = DATA_DIR / "anomaly_baseline.json"
HISTORY_PLOT_PATH = DATA_DIR / "training_history.png"
SYNTHETIC_DATA_PATH = DATA_DIR / "synthetic_data.csv"
# Seq2Seq LSTM autoencoder — second model for reconstruction-based anomaly detection.
AUTOENCODER_PATH = DATA_DIR / "autoencoder.keras"
AUTOENCODER_BASELINE_PATH = DATA_DIR / "autoencoder_baseline.json"


# TIME-SERIES / PREPROCESSING
# SEQ_LENGTH = how many past time steps the model sees before making a
# prediction. 40 (as in the professor's slides) means the LSTM "looks
# back" 40 minutes when each sample equals one minute.
# Bigger = more context but slower training and risk of overfitting.
# Smaller = less memory of long trends.
SEQ_LENGTH = 40

# When training, we use the first 80 % of the chronologically ordered
# series as the training set and the last 20 % as the test set.
# IMPORTANT: never shuffle a time series — that leaks future info.
TRAIN_RATIO = 0.8

# Sensor publishes every 40–50 s, which is irregular. Before feeding the
# series to the LSTM we resample to a uniform 1-minute grid (forward-fill
# for the rare gap). This keeps the "1 step = 1 minute" assumption
# consistent between training and runtime.
RESAMPLE_FREQ = "1min"


# MODEL HYPERPARAMETERS
# Dual-stack LSTM as in the slides: two LSTM layers of LSTM_UNITS each,
# with Dropout in between, ending with a single Dense neuron.
LSTM_UNITS = 64

# Dropout = fraction of neurons randomly disabled during each training
# step. Acts as regularization (prevents overfitting). 0.2 is the
# standard recommended starting point.
DROPOUT = 0.2

# Adam optimizer is adaptive; 1e-3 is its default and a safe starting LR
# for most LSTM problems. ReduceLROnPlateau will lower it automatically
# if training plateaus.
LEARNING_RATE = 0.0005

# Mean Squared Error: standard regression loss. Penalizes big errors
# more than small ones. Good when you care about avoiding large misses.
LOSS = "mse"

# Number of full passes over the training set. With EarlyStopping the
# actual number is usually much smaller — this is just the upper bound.
EPOCHS = 200

# How many sequences are processed in parallel before weights update.
# Larger = more stable gradient but more memory.
BATCH_SIZE = 32

# EarlyStopping: stop training when val_loss doesn't improve for this
# many epochs in a row. Saves time and avoids overfitting.
PATIENCE_EARLY_STOP = 20

# ReduceLROnPlateau: when val_loss stalls for this many epochs, multiply
# the learning rate by LR_REDUCE_FACTOR. Helps fine-grained convergence.
PATIENCE_LR_REDUCE = 5
LR_REDUCE_FACTOR = 0.5

# Verbosity for model.fit(): 0 silent, 1 progress bar, 2 one line per epoch
TRAIN_VERBOSE = 1


# FORECAST AND CONTROL LOGIC
# How many minutes into the future the model predicts (recursive,
# 1-step-at-a-time predictions chained together). Beyond ~30 the error
# accumulates too much to be useful.
FORECAST_MINUTES = 30

# Exponential smoothing factor in forecast.py:
#   next = (1 - alpha) * model_pred + alpha * previous_value
# 0 = pure model output, 1 = constant repetition. 0.2 is gentle smoothing
# that avoids visible jumps without flattening the trend.
SMOOTHING_ALPHA = 0.2

# Allowed temperature window (°C). Outside this range we switch a relay.
TARGET_MIN = 19.0
TARGET_MAX = 21.0

# How often controller_lstm.py recomputes a forecast and decides on an
# action. Faster = more responsive but more CPU. 60 s is plenty given
# the slow thermal dynamics of the room.
CONTROL_INTERVAL_SECONDS = 60


# ANOMALY DETECTION
# Physically impossible readings — anything outside this window is a
# hardware fault, not an environmental event.
PHYSICAL_MIN = -10.0
PHYSICAL_MAX = 60.0

# Stuck detector: how many consecutive identical readings count as
# "the sensor froze". Compared to STUCK_PRECISION decimal places.
STUCK_THRESHOLD = 10
STUCK_PRECISION = 3   # round to 3 decimals before comparing

# Timeout watchdog: max gap between sensor messages before raising an
# alert. The Pico publishes every 40–50 s, so 120 s means 2–3 missed.
TIMEOUT_SECONDS = 120

# Residual check: |actual - predicted| > N standard deviations of the
# training-set residuals → anomaly. N=4 is a common choice
# (covers 99.99 % of normal variation in a Gaussian).
RESIDUAL_THRESHOLD_SIGMAS = 4.0

# Autoencoder anomaly detection: number of sigma above baseline MSE that
# triggers an AUTOENCODER_RECONSTRUCTION alert.
AUTOENCODER_UNITS = 32
AUTOENCODER_RECONSTRUCTION_SIGMAS = 4.0



# SIMULATION (synthetic data for initial training)
# 50 hours of minute-resolution data. Enough to learn the daily cycle
# without being so big that training drags on.
SIM_TOTAL_MINUTES = 3000

# Daily sinusoid: temperature oscillates ±DAILY_AMPLITUDE around
# DAILY_OFFSET over a period of 1440 min (= 24 h).
SIM_DAILY_OFFSET = 20.0
SIM_DAILY_AMPLITUDE = 1.8
SIM_DAILY_PERIOD = 1440         # minutes in a day
SIM_DAILY_PHASE_FRAC = 0.25     # shifts the peak to a particular hour

# Short-period component: faster swings (3 h period) representing
# heating cycles in the production environment.
SIM_SHORT_AMPLITUDE = 0.5
SIM_SHORT_PERIOD = 180
SIM_SHORT_PHASE = 0.8

# Gaussian sensor noise — keeps the model from memorizing exact values.
SIM_NOISE_STD = 0.08

# Fixed seed for reproducibility: same simulation every run.
SIM_SEED = 42



# DEBUG
# Set DEBUG=1 in the environment (or docker-compose) to enable verbose
# per-message prints in controller_lstm.py. Safe to leave off in production.
DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")

# MQTT
# Environment-variable overrides let docker-compose set the broker
# hostname (which is "mosquitto" inside the Docker network but
# "localhost" when running on the dev machine) without code changes.

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))   # 8883 = MQTT over TLS

MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "lstm_controller")

# Set MQTT_TLS_INSECURE=1 only during LOCAL development when the broker
# certificate CN is "mosquitto" but you connect via "localhost".
# NEVER set this in production — it disables hostname verification.
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "").lower() in ("1", "true", "yes")

# QoS 1 = at-least-once delivery (the broker acknowledges, message may
# arrive more than once). Best balance of reliability vs. cost for us.
MQTT_QOS = 1

# Seconds without traffic before broker considers the client dead.
MQTT_KEEPALIVE = 60

# Topic names — keep these consistent with the rest of the system.
TOPIC_SENSOR_TEMPERATURE = "sensor/temperature"
TOPIC_ACTUATOR_CONTROL = "actuator/control"
TOPIC_SENSOR_ALERT = "sensor/alert"
# Live forecast broadcast — subscribers overlay predictions on the dashboard.
TOPIC_LSTM_FORECAST = "lstm/forecast"
# Retained model-version message — published once on controller startup.
TOPIC_LSTM_VERSION = "lstm/version"

# TLS certificate paths INSIDE the container. docker-compose mounts the
# host's CA/ folder to /app/certs.
CA_CERT_PATH = os.getenv("CA_CERT_PATH", "/app/certs/ca.crt")
CLIENT_CERT_PATH = os.getenv("CLIENT_CERT_PATH", "/app/certs/lstm.crt")
CLIENT_KEY_PATH = os.getenv("CLIENT_KEY_PATH", "/app/certs/lstm.key")



# ACTUATOR COMMANDS
# Exact strings the MicroController expects on actuator/control.
# Defined here so we don't accidentally typo them in controller_lstm.py.
CMD_FAN_ON = "fan_on"
CMD_HEATER_ON = "heater_on"
CMD_BOTH_OFF = "both_off"



# Sanity check: catch impossible combinations at import time so the user
# sees the problem immediately rather than after training finishes.
assert 0 < TRAIN_RATIO < 1, "TRAIN_RATIO must be in (0, 1)"
assert SEQ_LENGTH > 0, "SEQ_LENGTH must be positive"
assert TARGET_MIN < TARGET_MAX, "TARGET_MIN must be less than TARGET_MAX"
assert 0 <= SMOOTHING_ALPHA <= 1, "SMOOTHING_ALPHA must be in [0, 1]"