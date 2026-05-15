"""
train_LSTM.py — Offline training entry point.
 
Run with:
    cd backend/LSTM
    python train_LSTM.py
 
Outputs (under data/):
    model.keras              trained LSTM (architecture + weights)
    scaler.npz               StandardScaler mean and std
    anomaly_baseline.json    residual std for runtime anomaly detection
    training_history.png     loss curve
    synthetic_data.csv       snapshot of the simulated dataset
"""
 
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import config
from anomaly import save_baseline
from autoencoder import (
    build_autoencoder,
    compute_reconstruction_errors,
    get_autoencoder_callbacks,
    save_autoencoder_baseline,
)
from data_loader import save_to_csv, simulate_temperature
from model import build_model, get_callbacks
from preprocessing import (
    create_sequences,
    fit_scaler,
    save_scaler,
    scale,
    train_test_split_timeseries,
)
 
 
def main() -> int:
    # 1. Load (or simulate) the raw series 
    print("[1/7] Generating synthetic temperature data...")
    df = simulate_temperature()
    save_to_csv(df, config.SYNTHETIC_DATA_PATH)
    print(f"      {len(df):,} samples written to {config.SYNTHETIC_DATA_PATH}")
 
    # We treat temperature as a univariate series; shape is (N, 1).
    raw = df[["temperature"]].values.astype(np.float32)
 
    # 2. Chronological train / test split
    # IMPORTANT: split BEFORE scaling. Otherwise the scaler "sees" the
    # test data via its mean/std → data leakage.
    print("[2/8] Splitting train / test chronologically...")
    train_raw, test_raw = train_test_split_timeseries(raw, config.TRAIN_RATIO)
    print(f"      train: {len(train_raw)}   test: {len(test_raw)}")
    # Leakage guard: if train and test share no overlap in range, we're clean.
    print(f"      Train range: [{train_raw.min():.3f}, {train_raw.max():.3f}]")
    print(f"      Test range:  [{test_raw.min():.3f}, {test_raw.max():.3f}]")
 
    # 3. Fit scaler on train only
    print("[3/8] Fitting StandardScaler on the training portion...")
    scaler = fit_scaler(train_raw)
    save_scaler(scaler, config.SCALER_PATH)
    print(f"      mean={scaler.mean_[0]:.4f}  std={scaler.scale_[0]:.4f}")
 
    train_scaled = scale(train_raw, scaler)
    test_scaled = scale(test_raw, scaler)

    # Sanity check: scaled series should be ~N(0,1). If not, scaler is broken.
    print(f"      scaled train mean={train_scaled.mean():.4f}  (should be ~0)")
    print(f"      scaled train std= {train_scaled.std():.4f}   (should be ~1)")

    # 4. Build sliding windows
    print("[4/8] Building sliding-window sequences...")
    X_train, y_train = create_sequences(train_scaled, config.SEQ_LENGTH)
    X_test, y_test = create_sequences(test_scaled, config.SEQ_LENGTH)
    print(f"      X_train={X_train.shape}   X_test={X_test.shape}")
 
    # 5. Build and inspect the model 
    print("[5/8] Building model...")
    model = build_model(input_shape=(config.SEQ_LENGTH, 1))
    model.summary()
 
    # 6. Train
    print("[6/8] Training...")
    history = model.fit(
        X_train, y_train,
        validation_data=(X_test, y_test),
        epochs=config.EPOCHS,
        batch_size=config.BATCH_SIZE,
        verbose=config.TRAIN_VERBOSE,
        callbacks=get_callbacks(),
    )
 
    # Persist the trained network.
    model.save(config.MODEL_PATH)
    print(f"      model saved to {config.MODEL_PATH}")
 
    #7. Compute and persist the anomaly baseline
    # The std of (y_test - y_pred) on the held-out set tells us what
    # "normal residuals" look like. At runtime, anything bigger than
    # N * std is flagged as a RESIDUAL_SPIKE anomaly.
    print("[7/8] Computing anomaly baseline...")
    y_pred = model.predict(X_test, verbose=0).ravel()
    residuals = y_test.ravel() - y_pred
    residual_std = float(residuals.std())
    save_baseline(residual_std)
    print(f"      residual_std={residual_std:.6f}  "
          f"threshold={residual_std * config.RESIDUAL_THRESHOLD_SIGMAS:.6f}")

    # Collapse-to-mean diagnostic: if pred_std << true_std, the model
    # learned only the average (increase EPOCHS or lower DROPOUT).
    pred_std = float(y_pred.std())
    true_std = float(y_test.ravel().std())
    print(f"      pred_std={pred_std:.4f}  true_std={true_std:.4f}", end="")
    if pred_std < 0.3 * true_std:
        print("  ← WARNING: model may have collapsed to mean. "
              "Try EPOCHS=200, DROPOUT=0.1, BATCH_SIZE=16.")
    else:
        print()
 
    # Plot loss curve 
    plt.figure(figsize=(10, 6))
    plt.plot(history.history["loss"], label="train loss")
    plt.plot(history.history["val_loss"], label="val loss")
    plt.xlabel("epoch")
    plt.ylabel("MSE")
    plt.title("LSTM training history")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(config.HISTORY_PLOT_PATH, dpi=120)
    plt.close()
    print(f"      loss curve saved to {config.HISTORY_PLOT_PATH}")

    # 8. Train Seq2Seq LSTM autoencoder for reconstruction-based anomaly detection.
    # Objective: model.fit(X, X) — learn to reconstruct normal windows.
    # At runtime, windows with high reconstruction MSE are structurally unusual.
    print("[8/8] Training autoencoder (Seq2Seq LSTM)...")
    autoencoder = build_autoencoder(input_shape=(config.SEQ_LENGTH, 1))
    autoencoder.summary()
    autoencoder.fit(
        X_train, X_train,
        validation_data=(X_test, X_test),
        epochs=config.EPOCHS,
        batch_size=config.BATCH_SIZE,
        verbose=config.TRAIN_VERBOSE,
        callbacks=get_autoencoder_callbacks(),
    )
    autoencoder.save(config.AUTOENCODER_PATH)
    print(f"      autoencoder saved to {config.AUTOENCODER_PATH}")

    # Baseline: std of per-window reconstruction MSE on the held-out set.
    recon_errors = compute_reconstruction_errors(autoencoder, X_test)
    autoencoder_baseline_std = float(recon_errors.std())
    save_autoencoder_baseline(autoencoder_baseline_std)
    print(f"      reconstruction_std={autoencoder_baseline_std:.6f}  "
          f"threshold={autoencoder_baseline_std * config.AUTOENCODER_RECONSTRUCTION_SIGMAS:.6f}")

    print("\nDone.")
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())