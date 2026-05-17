"""One-shot forecasting smoke test.

Loads the trained model, pulls the latest SEQ_LENGTH temperature window from
the configured data source, predicts MINUTES ahead, prints the numbers, and
plots window + forecast in the terminal.

Use this to sanity-check training output before wiring up the control loop.
The control loop itself (lstm/control_loop.py) reuses forecast() and the
scale helpers from here.
"""
import argparse
from pathlib import Path

import numpy as np
import plotext as plt

# PI-REBUILD: on the Pi, install tflite-runtime and replace the
# load_artifacts() body with:
#   from tflite_runtime.interpreter import Interpreter
#   interpreter = Interpreter(str(DATA_DIR / "model.tflite"))
#   interpreter.allocate_tensors()
# Then rewrite forecast() to use interpreter.set_tensor / invoke / get_tensor
# instead of model.predict.
import tensorflow as tf

from data_source import load_temperatures

SEQ_LENGTH = 240  # must match train.py:SEQ_LENGTH
ALPHA = 0.0  # forecast smoothing factor from slide 5-35. 0 = raw model.
DEFAULT_MINUTES = 240
DATA_DIR = Path(__file__).parent / "data"


def load_artifacts():
    model = tf.keras.models.load_model(DATA_DIR / "model.keras")
    s = np.load(DATA_DIR / "scaler.npz")
    return model, s["mean"], s["scale"]


def scale(x, mean, std):
    return (x - mean) / std


def unscale(x, mean, std):
    return x * std + mean


def forecast(model, window_scaled, minutes, alpha=ALPHA):
    seq = np.array(window_scaled, dtype=np.float32).reshape(1, SEQ_LENGTH, 1)
    out = []
    for _ in range(minutes):
        pred = model.predict(seq, verbose=0)
        next_val = (1 - alpha) * float(pred[0, 0]) + alpha * float(seq[0, -1, 0])
        out.append(next_val)
        next_arr = np.array([[[next_val]]], dtype=np.float32)
        seq = np.concatenate([seq[:, 1:, :], next_arr], axis=1)
    return np.array(out)


def latest_window():
    data = load_temperatures()
    if len(data) < SEQ_LENGTH:
        raise SystemExit(f"only {len(data)} temperatures available, need {SEQ_LENGTH}")
    return data[-SEQ_LENGTH:]


def plot_forecast(window, fc):
    plt.clear_figure()
    x_window = list(range(len(window)))
    x_fc = list(range(len(window), len(window) + len(fc)))
    plt.plot(x_window, window.flatten().tolist(), label="window")
    plt.plot(x_fc, fc.tolist(), label="forecast")
    plt.title("temperature forecast")
    plt.xlabel("minute")
    plt.ylabel("deg C")
    plt.show()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=int, default=DEFAULT_MINUTES,
                    help=f"minutes to forecast ahead (default {DEFAULT_MINUTES})")
    ap.add_argument("--no-plot", action="store_true",
                    help="suppress the terminal plot (still prints numbers)")
    args = ap.parse_args()

    print("loading model")
    model, mean, std = load_artifacts()
    print("pulling latest window")
    window = latest_window()
    print(f"window tail (last 5): {window[-5:].flatten()}")
    print(f"forecasting {args.minutes} minutes ahead")
    fc_scaled = forecast(model, scale(window, mean, std), args.minutes)
    fc = unscale(fc_scaled, mean, std)
    if not args.no_plot:
        plot_forecast(window, fc)
    print("forecast head (first 10):", ", ".join(f"{v:.2f}" for v in fc[:10]))
    print(f"forecast min/max: {fc.min():.2f} / {fc.max():.2f}")


if __name__ == "__main__":
    main()
