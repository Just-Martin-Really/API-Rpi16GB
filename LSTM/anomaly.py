"""
anomaly.py — Sensor fault / error detection.
 
Four complementary checks, ordered by priority in classify_anomaly():
 
  1) PHYSICAL_OUT_OF_RANGE  — value outside [-10, 60] °C is definitely
                              a hardware fault. Highest priority.
  2) TIMEOUT                — sensor stopped reporting. The reading we
                              just got is moot if it is stale; check
                              separately in a watchdog thread.
  3) STUCK                  — last N readings are identical → frozen.
  4) RESIDUAL_SPIKE         — actual reading too far from prediction.
                              Lowest priority because predictions are
                              never perfect; only flag big deviations.
 
Baseline:
  The residual threshold is derived during training: we compute the std
  of residuals (actual - predicted) on the test set, then use
  N * std as the cutoff (N from config.RESIDUAL_THRESHOLD_SIGMAS).
  train_LSTM.py writes this to data/anomaly_baseline.json.
"""
 
import json
import time
from collections import deque
from enum import Enum
from typing import Optional
 
import numpy as np
 
import config
 
 
class AlertType(Enum):
    """Tags published on sensor/alert so downstream consumers can route."""
    PHYSICAL_OUT_OF_RANGE = "physical_out_of_range"
    TIMEOUT = "timeout"
    STUCK = "stuck"
    RESIDUAL_SPIKE = "residual_spike"
    # Reconstruction error from the Seq2Seq autoencoder exceeds baseline threshold.
    # Catches structural anomalies (slow drift, unusual patterns) missed by residual_spike.
    AUTOENCODER_RECONSTRUCTION = "autoencoder_reconstruction"
 
 
def check_sanity(value: float) -> bool:
    """True if the value is within the physically plausible range."""
    return config.PHYSICAL_MIN <= value <= config.PHYSICAL_MAX
 
 
def check_stuck(history) -> bool:
    """
    True if the last STUCK_THRESHOLD readings are identical (to
    STUCK_PRECISION decimals).
 
    `history` should be a sequence of recent readings rounded to
    STUCK_PRECISION decimals. We use a deque of fixed size in the
    controller so this check is O(1).
    """
    if len(history) < config.STUCK_THRESHOLD:
        return False
    last = list(history)[-config.STUCK_THRESHOLD:]
    return len(set(last)) == 1
 
 
def check_timeout(last_ts: float, now: float) -> bool:
    """True if too much time has elapsed since the last sensor message."""
    return (now - last_ts) > config.TIMEOUT_SECONDS
 
 
def check_residual(actual: float, predicted: float, threshold: float) -> bool:
    """True if the prediction error exceeds the threshold."""
    return abs(actual - predicted) > threshold
 
 
def classify_anomaly(value: float,
                     predicted: Optional[float],
                     history,
                     last_ts: float,
                     residual_threshold: float,
                     now: Optional[float] = None) -> Optional[AlertType]:
    """
    Apply all four checks in priority order. Returns the FIRST matching
    AlertType, or None if everything looks normal.
    """
    if now is None:
        now = time.time()
 
    if not check_sanity(value):
        return AlertType.PHYSICAL_OUT_OF_RANGE
 
    if check_timeout(last_ts, now):
        return AlertType.TIMEOUT
 
    if check_stuck(history):
        return AlertType.STUCK
 
    if predicted is not None and check_residual(value, predicted, residual_threshold):
        return AlertType.RESIDUAL_SPIKE
 
    return None
 
 
def save_baseline(residual_std: float, path=None) -> None:
    """
    Persist the residual-standard-deviation baseline computed during
    training. controller_lstm.py loads this at startup and multiplies it
    by config.RESIDUAL_THRESHOLD_SIGMAS to get the runtime threshold.
    """
    path = path or config.ANOMALY_BASELINE_PATH
    payload = {
        "residual_std": float(residual_std),
        "sigmas": float(config.RESIDUAL_THRESHOLD_SIGMAS),
        "threshold": float(residual_std * config.RESIDUAL_THRESHOLD_SIGMAS),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
 
 
def load_baseline(path=None) -> dict:
    """Load the residual baseline written by save_baseline."""
    path = path or config.ANOMALY_BASELINE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)