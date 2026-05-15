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


def mode_paste(model, mean, std):
    print(f"paste {SEQ_LENGTH} comma-separated temperatures, then press enter:")
    line = input("> ").strip()
    try:
        vals = [float(x) for x in line.split(",")]
    except ValueError as e:
        print(f"parse error: {e}")
        return
    if len(vals) != SEQ_LENGTH:
        print(f"need exactly {SEQ_LENGTH} values, got {len(vals)}")
        return
    minutes = int(input("forecast how many minutes? [240] ") or "240")
    window = np.array(vals).reshape(-1, 1)
    fc_scaled = forecast(model, scale(window, mean, std), minutes)
    fc = unscale(fc_scaled, mean, std)
    plot_forecast(window, fc)
    print("forecast:", ", ".join(f"{v:.2f}" for v in fc))


def mode_step(model, mean, std):
    print("seeding window from the configured data source")
    window = latest_window()
    print(f"starting window (last 5): {window[-5:].flatten()}")
    print("enter next observed value each prompt, or 'q' to quit")
    while True:
        nxt = input("next value: ").strip()
        if nxt in ("q", "quit", "exit"):
            return
        try:
            v = float(nxt)
        except ValueError:
            print("not a number")
            continue
        window = np.vstack([window[1:], [[v]]])
        fc_scaled = forecast(model, scale(window, mean, std), minutes=30)
        fc = unscale(fc_scaled, mean, std)
        plot_forecast(window, fc)
        print(f"next-minute prediction: {fc[0]:.2f}")


def mode_latest(model, mean, std):
    window = latest_window()
    minutes = int(input("forecast how many minutes? [240] ") or "240")
    fc_scaled = forecast(model, scale(window, mean, std), minutes)
    fc = unscale(fc_scaled, mean, std)
    plot_forecast(window, fc)
    print("forecast:", ", ".join(f"{v:.2f}" for v in fc))


def main():
    print("loading model")
    model, mean, std = load_artifacts()
    print("ready. commands: paste, step, latest, quit")
    while True:
        cmd = input("forecast> ").strip().lower()
        if cmd in ("q", "quit", "exit"):
            return
        if cmd == "paste":
            mode_paste(model, mean, std)
        elif cmd == "step":
            mode_step(model, mean, std)
        elif cmd == "latest":
            mode_latest(model, mean, std)
        else:
            print("unknown command")


if __name__ == "__main__":
    main()
