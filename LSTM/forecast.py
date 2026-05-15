"""
forecast.py — Recursive multi-step temperature prediction.
 
The model is trained to predict ONE step ahead. To get N steps ahead we
"roll" predictions forward, appending each prediction back into the
input window:
 
    step 1: input = [t-39 .. t]            -> pred at t+1
    step 2: input = [t-38 .. t, t+1]       -> pred at t+2
    step N: input = [t-39+N .. t+N-1]      -> pred at t+N
 
Because later steps build on top of earlier predictions, ERRORS
ACCUMULATE. The deeper into the future you go, the less trust the model
deserves. Beyond ~30 steps it is largely hallucinating.
 
Mitigation — exponential smoothing on every new prediction:
    next_value = (1 - alpha) * model_pred + alpha * previous_value
alpha=0  → pure model output (no smoothing)
alpha=1  → constant repetition of the last input value
alpha=0.2 (default) → gentle damping of unrealistic jumps.
"""
 
import numpy as np
 
import config
 
 
def forecast_future(model,
                    last_sequence: np.ndarray,
                    minutes: int | None = None,
                    alpha: float | None = None,
                    scaler=None) -> np.ndarray:
    """
    Predict `minutes` steps ahead, recursively.
 
    Parameters
    ----------
    model : tf.keras.Model
        A trained LSTM that maps (1, SEQ_LENGTH, 1) → (1, 1).
    last_sequence : np.ndarray
        The most recent SEQ_LENGTH measurements, ALREADY SCALED.
        Accepted shapes: (SEQ_LENGTH, 1) or (1, SEQ_LENGTH, 1).
    minutes : int, optional
        How many steps ahead to predict. Defaults to config.FORECAST_MINUTES.
    alpha : float, optional
        Smoothing factor in [0, 1]. Defaults to config.SMOOTHING_ALPHA.
    scaler : sklearn StandardScaler, optional
        If provided, the returned predictions are inverse-scaled back to
        the original units (°C). Otherwise they stay in the scaled space.
 
    Returns
    -------
    np.ndarray, shape (minutes,)
        Future values, in °C if a scaler was given, scaled otherwise.
    """
    minutes = minutes if minutes is not None else config.FORECAST_MINUTES
    alpha = alpha if alpha is not None else config.SMOOTHING_ALPHA
 
    # Work on a copy so the caller's window is not mutated.
    seq = np.asarray(last_sequence, dtype=np.float32).copy()
    if seq.ndim == 2:
        seq = np.expand_dims(seq, axis=0)        # → (1, SEQ_LENGTH, 1)
 
    predictions = []
    for _ in range(minutes):
        pred = model.predict(seq, verbose=0)     # shape (1, 1)
 
        # Smooth between raw prediction and last known value.
        last_value = float(seq[0, -1, 0])
        smoothed = (1.0 - alpha) * float(pred[0, 0]) + alpha * last_value
        predictions.append(smoothed)
 
        # Roll the window: drop oldest, append our new value.
        next_step = np.array([[[smoothed]]], dtype=np.float32)
        seq = np.concatenate([seq[:, 1:, :], next_step], axis=1)
 
    predictions = np.asarray(predictions, dtype=np.float32)
 
    # Optional inverse scaling to bring values back to °C.
    if scaler is not None:
        predictions = scaler.inverse_transform(
            predictions.reshape(-1, 1)
        ).ravel()
 
    return predictions