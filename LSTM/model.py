"""
model.py — Dual-stack LSTM architecture and training callbacks.
 
Architecture (verbatim from the professor's slides):
 
    Input(shape=(SEQ_LENGTH, n_features))
        │
        ▼
    LSTM(64, return_sequences=True)   <- emits one vector per time step
    Dropout(0.2)
        │
        ▼
    LSTM(64)                          <- emits only the final state
    Dropout(0.2)
        │
        ▼
    Dense(1)                          <- scalar prediction (scaled)
 
Why TWO LSTM layers (dual stack):
    - Layer 1 learns short-term patterns from raw input.
    - Layer 2 learns abstractions OVER layer 1's outputs.
    For mildly non-linear signals like temperature, 2 layers is usually
    sweet spot — 1 underfits, 3+ overfits and slows everything down.
 
Why return_sequences=True on layer 1:
    Layer 2 needs a SEQUENCE as input. Without this flag, layer 1 would
    output only its final hidden state and layer 2 would have nothing to
    chew on across time.
"""
 
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Input, LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
 
import config
 
 
def build_model(input_shape: tuple) -> Sequential:
    """
    Build and compile the dual-stack LSTM.
 
    Parameters
    ----------
    input_shape : (SEQ_LENGTH, n_features)
        Shape of ONE input sample (no batch dimension).
 
    Returns
    -------
    A compiled tf.keras.Model ready for .fit().
    """
    model = Sequential([
        Input(shape=input_shape),
        LSTM(config.LSTM_UNITS, return_sequences=True),
        Dropout(config.DROPOUT),
        LSTM(config.LSTM_UNITS),
        Dropout(config.DROPOUT),
        Dense(1),
    ])
    model.compile(
        optimizer=Adam(learning_rate=config.LEARNING_RATE),
        loss=config.LOSS,
    )
    return model
 
 
def get_callbacks() -> list:
    """
    Return the list of training callbacks.
 
    EarlyStopping
        Monitors val_loss. If it does not improve for PATIENCE_EARLY_STOP
        epochs in a row, training is stopped and the best weights (from
        the best epoch) are restored. Saves time AND prevents overfitting.
 
    ReduceLROnPlateau
        When val_loss plateaus for PATIENCE_LR_REDUCE epochs, multiplies
        the current learning rate by LR_REDUCE_FACTOR. This helps the
        optimizer make smaller, finer steps in the late phases of training
        when big jumps would overshoot the minimum.
    """
    return [
        EarlyStopping(
            monitor="val_loss",
            patience=config.PATIENCE_EARLY_STOP,
            restore_best_weights=True,
            verbose=1,
        ),
        ReduceLROnPlateau(
            monitor="val_loss",
            factor=config.LR_REDUCE_FACTOR,
            patience=config.PATIENCE_LR_REDUCE,
            min_lr=1e-6,
            verbose=1,
        ),
    ]
 