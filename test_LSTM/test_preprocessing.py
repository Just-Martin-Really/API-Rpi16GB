"""
test_preprocessing.py — Tests for preprocessing.py

Coverage:
  - create_sequences returns the correct number of pairs: N - SEQ_LENGTH
  - X[i] and y[i] are paired correctly (y[i] equals the value right after
    the X[i] window)
  - X has shape (samples, SEQ_LENGTH, n_features) and y has shape (samples,)
  - StandardScaler fitted on the training set returns data with mean ~0
    and std ~1 on that same set
  - inverse_scale composed with scale is the identity (round-trip test)
  - save_scaler -> load_scaler preserves the mean and std exactly
  - train_test_split_timeseries preserves chronology: X_test starts at the
    sample immediately after the last X_train sample (NO shuffle)
  - Edge cases: SEQ_LENGTH greater than data length raises ValueError;
    a series of exactly SEQ_LENGTH + 1 points yields exactly one (X, y) pair
"""

import numpy as np
import pytest

import preprocessing


SEQ_LENGTH = 5


def make_series(n: int) -> np.ndarray:
    """Helper: deterministic 1-D ramp reshaped to (n, 1)."""
    return np.arange(n, dtype=np.float32).reshape(-1, 1)


def test_create_sequences_count_and_shape():
    data = make_series(20)
    X, y = preprocessing.create_sequences(data, SEQ_LENGTH)
    assert X.shape == (15, SEQ_LENGTH, 1)
    assert y.shape == (15, 1)


def test_create_sequences_pairing():
    data = make_series(10)
    X, y = preprocessing.create_sequences(data, 3)
    # X[0] should be [0,1,2], y[0] should be 3
    np.testing.assert_array_equal(X[0].ravel(), [0, 1, 2])
    np.testing.assert_array_equal(y[0].ravel(), [3])
    # X[-1] should be [6,7,8], y[-1] should be 9
    np.testing.assert_array_equal(X[-1].ravel(), [6, 7, 8])
    np.testing.assert_array_equal(y[-1].ravel(), [9])


def test_create_sequences_minimal_data():
    data = make_series(SEQ_LENGTH + 1)
    X, y = preprocessing.create_sequences(data, SEQ_LENGTH)
    assert X.shape == (1, SEQ_LENGTH, 1)
    assert y.shape == (1, 1)


def test_create_sequences_too_short_raises():
    data = make_series(SEQ_LENGTH)
    with pytest.raises(ValueError):
        preprocessing.create_sequences(data, SEQ_LENGTH)


def test_scaler_mean_and_std():
    train = np.linspace(15.0, 25.0, 100).reshape(-1, 1)
    scaler = preprocessing.fit_scaler(train)
    scaled = preprocessing.scale(train, scaler)
    assert abs(scaled.mean()) < 1e-5
    assert abs(scaled.std() - 1.0) < 1e-5


def test_inverse_scale_round_trip():
    train = np.linspace(15.0, 25.0, 100).reshape(-1, 1)
    scaler = preprocessing.fit_scaler(train)
    scaled = preprocessing.scale(train, scaler)
    back = preprocessing.inverse_scale(scaled, scaler)
    np.testing.assert_allclose(back, train, atol=1e-5)


def test_save_load_scaler(tmp_path):
    train = np.linspace(15.0, 25.0, 100).reshape(-1, 1)
    scaler = preprocessing.fit_scaler(train)
    p = tmp_path / "scaler.npz"
    preprocessing.save_scaler(scaler, p)

    loaded = preprocessing.load_scaler(p)
    np.testing.assert_array_equal(loaded.mean_, scaler.mean_)
    np.testing.assert_array_equal(loaded.scale_, scaler.scale_)


def test_train_test_split_chronology():
    data = make_series(100)
    train, test = preprocessing.train_test_split_timeseries(data, 0.8)
    assert len(train) == 80
    assert len(test) == 20
    # First test sample is the one immediately after the last train sample.
    assert float(test[0]) == float(train[-1]) + 1