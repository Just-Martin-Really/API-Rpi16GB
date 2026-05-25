import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from forecast import SEQ_LENGTH, forecast, scale, unscale


class FakeModel:
    """Deterministic stand-in for keras Model: always predicts a fixed delta
    above the last value in the input window. Keeps tests free of TF compute."""

    def __init__(self, fixed_value: float):
        self.fixed_value = fixed_value
        self.call_count = 0

    def predict(self, seq, verbose=0):
        self.call_count += 1
        return np.array([[self.fixed_value]], dtype=np.float32)


def test_scale_unscale_roundtrip():
    x = np.linspace(15.0, 30.0, 50).reshape(-1, 1)
    mean = 22.5
    std = 4.0
    back = unscale(scale(x, mean, std), mean, std)
    np.testing.assert_allclose(back, x, rtol=1e-6)


def test_forecast_shape_and_call_count():
    window = np.zeros((SEQ_LENGTH, 1), dtype=np.float32)
    model = FakeModel(fixed_value=0.5)
    fc = forecast(model, window, minutes=10, alpha=0.0)
    assert fc.shape == (10,)
    assert model.call_count == 10


def test_forecast_alpha_zero_returns_pure_model():
    # alpha=0 means the model output is used verbatim. With a constant
    # FakeModel that returns 0.5, the entire forecast should be 0.5s.
    window = np.zeros((SEQ_LENGTH, 1), dtype=np.float32)
    model = FakeModel(fixed_value=0.5)
    fc = forecast(model, window, minutes=5, alpha=0.0)
    np.testing.assert_allclose(fc, [0.5] * 5)


def test_forecast_alpha_one_holds_last_window_value():
    # alpha=1 means ignore the model entirely and hold the last value
    # of the input window. Starting from a window whose last value is
    # 1.0, every forecast step should be 1.0 too.
    window = np.zeros((SEQ_LENGTH, 1), dtype=np.float32)
    window[-1, 0] = 1.0
    model = FakeModel(fixed_value=99.0)  # ignored at alpha=1
    fc = forecast(model, window, minutes=5, alpha=1.0)
    np.testing.assert_allclose(fc, [1.0] * 5)
