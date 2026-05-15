from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler

# PI-REBUILD: training stays on a workstation. On the Pi, do not import
# tensorflow at all; instead, after training here, export to TFLite:
#   converter = tf.lite.TFLiteConverter.from_keras_model(model)
#   open("data/model.tflite", "wb").write(converter.convert())
# and load on the Pi with tflite_runtime.interpreter.Interpreter.
import tensorflow as tf
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
from tensorflow.keras.models import Sequential
from tensorflow.keras.optimizers import Adam

from data_source import load_temperatures

# ---- knobs ------------------------------------------------------------------
# Sequence length: how many past minutes feed one prediction. Slide 5-29 uses
# 40 for real sensor data; for the sim, 240 covers > 1 full 180-min cycle.
SEQ_LENGTH = 240

# Training hyperparameters.
EPOCHS = 100
BATCH_SIZE = 32
LEARNING_RATE = 0.001
TRAIN_TEST_SPLIT = 0.8
# -----------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"


def create_sequences(data, seq_length):
    X, y = [], []
    for i in range(len(data) - seq_length):
        X.append(data[i : i + seq_length])
        y.append(data[i + seq_length])
    return np.array(X), np.array(y)


def build_model(seq_length):
    return Sequential(
        [
            Input(shape=(seq_length, 1)),
            LSTM(64, return_sequences=True),
            Dropout(0.2),
            LSTM(64),
            Dropout(0.2),
            Dense(1),
        ]
    )


def main():
    raw = load_temperatures()
    print(f"loaded {len(raw)} temperature rows")

    if len(raw) < SEQ_LENGTH + 10:
        raise SystemExit(f"not enough data: need > {SEQ_LENGTH + 10}, got {len(raw)}")

    scaler = StandardScaler()
    scaled = scaler.fit_transform(raw)

    X, y = create_sequences(scaled, SEQ_LENGTH)
    split = int(len(X) * TRAIN_TEST_SPLIT)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model = build_model(SEQ_LENGTH)
    model.compile(optimizer=Adam(learning_rate=LEARNING_RATE), loss="mse")
    model.summary()

    callbacks = [
        EarlyStopping(patience=10, restore_best_weights=True),
        ReduceLROnPlateau(patience=5, factor=0.5, min_lr=1e-6),
    ]
    model.fit(
        X_train,
        y_train,
        validation_data=(X_test, y_test),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=callbacks,
        verbose=1,
    )

    DATA_DIR.mkdir(exist_ok=True)
    model.save(DATA_DIR / "model.keras")
    np.savez(DATA_DIR / "scaler.npz", mean=scaler.mean_, scale=scaler.scale_)
    print(f"saved {DATA_DIR / 'model.keras'} and {DATA_DIR / 'scaler.npz'}")


if __name__ == "__main__":
    main()
