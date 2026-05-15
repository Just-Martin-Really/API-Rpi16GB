import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sklearn.preprocessing import StandardScaler

from train import SEQ_LENGTH, create_sequences


def test_create_sequences_shapes():
    data = np.arange(100, dtype=float).reshape(-1, 1)
    X, y = create_sequences(data, SEQ_LENGTH)
    assert X.shape == (60, SEQ_LENGTH, 1)
    assert y.shape == (60, 1)


def test_create_sequences_alignment():
    data = np.arange(50, dtype=float).reshape(-1, 1)
    X, y = create_sequences(data, 5)
    assert np.all(X[0].flatten() == np.arange(5))
    assert y[0, 0] == 5
    assert np.all(X[1].flatten() == np.arange(1, 6))
    assert y[1, 0] == 6


def test_create_sequences_empty_when_too_short():
    data = np.arange(3, dtype=float).reshape(-1, 1)
    X, y = create_sequences(data, 5)
    assert len(X) == 0
    assert len(y) == 0


def test_scaler_roundtrip():
    data = np.array([[10.0], [20.0], [30.0], [40.0]])
    sc = StandardScaler()
    scaled = sc.fit_transform(data)
    back = scaled * sc.scale_ + sc.mean_
    np.testing.assert_allclose(back, data)
