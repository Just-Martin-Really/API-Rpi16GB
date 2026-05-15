"""
autoencoder.py — Seq2Seq LSTM autoencoder for reconstruction-based anomaly detection.

Architecture:
    Input(SEQ_LENGTH, 1)
        → LSTM(32)                          encoder: compress window to a fixed vector
        → RepeatVector(SEQ_LENGTH)          repeat for each decoder step
        → LSTM(32, return_sequences=True)   decoder: reconstruct the sequence
        → TimeDistributed(Dense(1))         one output value per time step

Training objective: minimise reconstruction MSE — model.fit(X_train, X_train).
This is distinct from the forecasting LSTM which is trained on (X, y_next).

Anomaly scoring:
    mse = mean( (window - reconstructed_window)^2 )
    If mse > AUTOENCODER_RECONSTRUCTION_SIGMAS * baseline_std → flag anomaly.

Why this matters: the forecasting LSTM catches residual spikes (predicted vs actual).
The autoencoder catches whole-window patterns that are structurally unusual — e.g.
a sensor that slowly drifts rather than jumping. Two complementary detection methods.
"""

import json

import numpy as np
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.layers import Dense, Input, LSTM, RepeatVector, TimeDistributed
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam

import config


def build_autoencoder(input_shape: tuple) -> Model:
    """
    Build and compile the Seq2Seq LSTM autoencoder.

    Parameters
    ----------
    input_shape : (SEQ_LENGTH, n_features)
        Shape of ONE sample — no batch dimension.
    """
    seq_len, n_features = input_shape

    inputs = Input(shape=input_shape)
    encoded = LSTM(config.AUTOENCODER_UNITS)(inputs)
    repeated = RepeatVector(seq_len)(encoded)
    decoded = LSTM(config.AUTOENCODER_UNITS, return_sequences=True)(repeated)
    outputs = TimeDistributed(Dense(n_features))(decoded)

    model = Model(inputs, outputs)
    model.compile(optimizer=Adam(learning_rate=config.LEARNING_RATE), loss="mse")
    return model


def reconstruction_mse(model, sequence: np.ndarray) -> float:
    """
    Compute mean squared reconstruction error for a single window.

    Parameters
    ----------
    sequence : np.ndarray, shape (SEQ_LENGTH, 1) — already scaled.

    Returns
    -------
    float — MSE between input and reconstruction.
    """
    seq = np.asarray(sequence, dtype=np.float32)
    if seq.ndim == 2:
        seq = seq[np.newaxis]   # → (1, SEQ_LENGTH, 1)
    reconstruction = model.predict(seq, verbose=0)
    return float(np.mean((seq - reconstruction) ** 2))


def compute_reconstruction_errors(model, X: np.ndarray) -> np.ndarray:
    """
    Batch version of reconstruction_mse — returns one MSE per sample.
    Used during training to compute the baseline distribution.
    """
    reconstructions = model.predict(X, verbose=0)
    errors = np.mean((X - reconstructions) ** 2, axis=(1, 2))
    return errors.astype(np.float32)


def save_autoencoder_baseline(reconstruction_std: float, path=None) -> None:
    """
    Persist the reconstruction-MSE standard deviation computed on the test set.
    controller_lstm.py loads this at startup to derive the runtime threshold.
    """
    path = path or config.AUTOENCODER_BASELINE_PATH
    payload = {
        "reconstruction_std": float(reconstruction_std),
        "sigmas": float(config.AUTOENCODER_RECONSTRUCTION_SIGMAS),
        "threshold": float(reconstruction_std * config.AUTOENCODER_RECONSTRUCTION_SIGMAS),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_autoencoder_baseline(path=None) -> dict:
    """Load the autoencoder baseline written by save_autoencoder_baseline."""
    path = path or config.AUTOENCODER_BASELINE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_autoencoder_callbacks() -> list:
    return [
        EarlyStopping(
            monitor="val_loss",
            patience=config.PATIENCE_EARLY_STOP,
            restore_best_weights=True,
            verbose=1,
        ),
    ]
