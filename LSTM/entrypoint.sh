#!/bin/sh
set -e

if [ ! -f /app/data/model.keras ]; then
    echo "[entrypoint] Model not found — running initial training..."
    python train_LSTM.py
fi

exec python controller_lstm.py
