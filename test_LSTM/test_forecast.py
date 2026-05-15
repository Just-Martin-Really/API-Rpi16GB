"""
test_forecast.py — Tests for forecast.py

Coverage:
  - forecast_future returns a 1-D array of exactly `minutes` length
  - With alpha=0 the output equals the pure model prediction at each step
  - With alpha=1 the output is constant and equal to the last value of the
    input sequence (smoothing collapses to identity)
  - The input sliding window is not mutated by the function (idempotency)
  - A short fake model is used to make the tests fast and deterministic
"""

import numpy as np

import forecast


class FakeModel:
    """
    Stand-in for a Keras model. .predict(seq) just returns the LAST value
    of the input sequence (plus an optional offset). That lets us reason
    about the output of forecast_future without training anything.
    """

    def __init__(self, offset: float = 0.0):
        self.offset = offset

    def predict(self, seq, verbose=0):
        last = seq[0, -1, 0]
        return np.array([[last + self.offset]], dtype=np.float32)


def make_window(value: float = 1.0, length: int = 5) -> np.ndarray:
    return np.full((length, 1), value, dtype=np.float32)


def test_output_length():
    out = forecast.forecast_future(FakeModel(), make_window(), minutes=7, alpha=0.0)
    assert out.shape == (7,)


def test_alpha_zero_is_pure_model_prediction():
    # FakeModel(offset=1) returns last + 1 each step. With alpha=0 and a
    # window full of 1s, the first prediction should be 2, then 3, etc.
    out = forecast.forecast_future(FakeModel(offset=1.0),
                                   make_window(1.0),
                                   minutes=3,
                                   alpha=0.0)
    np.testing.assert_allclose(out, [2.0, 3.0, 4.0])


def test_alpha_one_returns_last_value_constant():
    # With alpha=1 the model output is fully overwritten by the previous
    # value, so we stay flat at the input level forever.
    out = forecast.forecast_future(FakeModel(offset=100.0),
                                   make_window(0.7),
                                   minutes=4,
                                   alpha=1.0)
    np.testing.assert_allclose(out, [0.7, 0.7, 0.7, 0.7])


def test_does_not_mutate_input_window():
    window = make_window(1.0)
    original = window.copy()
    forecast.forecast_future(FakeModel(offset=1.0), window, minutes=5, alpha=0.0)
    np.testing.assert_array_equal(window, original)