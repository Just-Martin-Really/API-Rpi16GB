"""
preprocessing.py — Scaling, sliding window, train/test split.
 
Pipeline order matters and is the most common place to get a subtle bug:
 
    raw values
       │
       │  1) split into train / test (NO shuffle for time series)
       ▼
    train portion              test portion
       │                          │
       │  2) fit_scaler on        │  scaler is REUSED here
       │     train portion        │  (no separate fit)
       ▼                          ▼
    scaled train               scaled test
       │                          │
       │  3) create_sequences     │  same SEQ_LENGTH
       ▼                          ▼
    (X_train, y_train)         (X_test, y_test)
 
If you fit the scaler on the WHOLE dataset (train + test together), the
test set leaks information into the mean/std and your validation metrics
become falsely optimistic. Always fit on train only.
"""
 
import numpy as np
from sklearn.preprocessing import StandardScaler
 
import config
 
 
def create_sequences(data: np.ndarray, seq_length: int):
    """
    Build (X, y) supervised-learning pairs from a 2-D time-series array
    using a sliding window.
 
    Parameters
    ----------
    data : np.ndarray, shape (N, features)
        Already scaled time series (one row per time step).
    seq_length : int
        Length of each input window.
 
    Returns
    -------
    X : np.ndarray, shape (M, seq_length, features)
        Input windows. M = N - seq_length.
    y : np.ndarray, shape (M, features)
        The value immediately AFTER each window — what the model must
        learn to predict.
 
    Example (seq_length=3, features=1):
        data = [[20.1],[20.2],[20.3],[20.4],[20.5]]
        X[0] = [[20.1],[20.2],[20.3]]   y[0] = [20.4]
        X[1] = [[20.2],[20.3],[20.4]]   y[1] = [20.5]
    """
    if not isinstance(data, np.ndarray):
        data = np.asarray(data)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if len(data) <= seq_length:
        raise ValueError(
            f"Need more than seq_length={seq_length} samples, "
            f"got {len(data)}."
        )
 
    X, y = [], []
    for i in range(len(data) - seq_length):
        X.append(data[i:i + seq_length])
        y.append(data[i + seq_length])
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32)
 
 
def fit_scaler(train_data: np.ndarray) -> StandardScaler:
    """
    Fit a StandardScaler on the training portion only.
 
    StandardScaler computes mean and std per column:
        scaled = (x - mean) / std
    so the resulting array has mean 0 and std 1 on the training set.
    """
    if not isinstance(train_data, np.ndarray):
        train_data = np.asarray(train_data)
    if train_data.ndim == 1:
        train_data = train_data.reshape(-1, 1)
    scaler = StandardScaler()
    scaler.fit(train_data)
    return scaler
 
 
def scale(data: np.ndarray, scaler: StandardScaler) -> np.ndarray:
    """Apply an already-fitted scaler."""
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    return scaler.transform(data).astype(np.float32)
 
 
def inverse_scale(data: np.ndarray, scaler: StandardScaler) -> np.ndarray:
    """Undo scaling — bring predictions back to °C."""
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    return scaler.inverse_transform(data)
 
 
def save_scaler(scaler: StandardScaler, path) -> None:
    """
    Save mean and std to a .npz file.
 
    We persist only the two arrays we actually need (not the whole sklearn
    object) so the file is portable across sklearn versions.
    """
    np.savez(path, mean=scaler.mean_, scale=scaler.scale_)
 
 
def load_scaler(path) -> StandardScaler:
    """Rebuild a StandardScaler from a .npz file produced by save_scaler."""
    data = np.load(path)
    scaler = StandardScaler()
    scaler.mean_ = data["mean"]
    scaler.scale_ = data["scale"]
    scaler.var_ = scaler.scale_ ** 2
    scaler.n_features_in_ = len(scaler.mean_)
    return scaler
 
 
def train_test_split_timeseries(data: np.ndarray, train_ratio: float):
    """
    Chronological split: first train_ratio fraction → train, rest → test.
    NO shuffle. This is the single most important preprocessing rule for
    time series.
    """
    n = len(data)
    split_idx = int(n * train_ratio)
    return data[:split_idx], data[split_idx:]