from pathlib import Path

import numpy as np

# PI-REBUILD: same swap as forecast.py if you want to evaluate on the Pi.
# Easier to just evaluate on the workstation before deploying.
import tensorflow as tf

from data_source import load_temperatures
from forecast import SEQ_LENGTH, forecast, scale, unscale

DATA_DIR = Path(__file__).parent / "data"


def main():
    model = tf.keras.models.load_model(DATA_DIR / "model.keras")
    s = np.load(DATA_DIR / "scaler.npz")
    mean, std = s["mean"], s["scale"]

    data = load_temperatures()
    split = int(len(data) * 0.8)
    train, test = data[:split], data[split:]
    if len(test) < SEQ_LENGTH + 30:
        raise SystemExit(f"not enough holdout data: {len(test)}")

    window = train[-SEQ_LENGTH:]
    horizon = min(30, len(test))
    fc = unscale(forecast(model, scale(window, mean, std), horizon), mean, std)
    actual = test[:horizon].flatten()

    mae = np.mean(np.abs(fc - actual))
    rmse = np.sqrt(np.mean((fc - actual) ** 2))
    print(f"MAE: {mae:.3f}  RMSE: {rmse:.3f}")
    print("predicted vs actual:")
    for p, a in zip(fc, actual):
        print(f"  {p:6.2f}   {a:6.2f}")


if __name__ == "__main__":
    main()
