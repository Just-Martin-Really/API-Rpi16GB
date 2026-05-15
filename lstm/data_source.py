"""Pluggable data source for the LSTM scripts.

Change SOURCE to swap between simulated, CSV, and live API data. All loaders
return a numpy array of shape (N, 1) of temperature values in chronological
order.
"""
import os
from pathlib import Path

import numpy as np

# ---- knobs ------------------------------------------------------------------
# "sim" : two-sine-wave synthetic temperature trace (slide 5-31). No setup.
# "csv" : read CSV_PATH, take the CSV_COLUMN column.
# "api" : pull from the backend API (requires lstm/.env).
SOURCE = "sim"

# Simulated source.
SIM_MINUTES = 10000
SIM_SEED = 42

# CSV source.
CSV_PATH = Path(__file__).parent / "data" / "temps.csv"
CSV_COLUMN = "temperature"

# API source. Effective only when SOURCE == "api".
DAYS = 7
# -----------------------------------------------------------------------------


def load_temperatures():
    if SOURCE == "sim":
        return _simulate()
    if SOURCE == "csv":
        return _from_csv()
    if SOURCE == "api":
        return _from_api()
    raise SystemExit(f"unknown SOURCE: {SOURCE!r}")


def _simulate():
    rng = np.random.default_rng(SIM_SEED)
    minutes = np.arange(SIM_MINUTES)
    daily = 20 + 1.8 * np.sin(minutes / 1440 * 2 * np.pi - 2 * np.pi * 0.25)
    short = 0.5 * np.sin(minutes / 180 * 2 * np.pi + 0.8)
    noise = rng.normal(0, 0.08, SIM_MINUTES)
    return (daily + short + noise).reshape(-1, 1)


def _from_csv():
    import pandas as pd

    df = pd.read_csv(CSV_PATH)
    return df[CSV_COLUMN].astype(float).values.reshape(-1, 1)


def _from_api():
    import pandas as pd
    import requests
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
    base = os.environ["API_BASE_URL"]
    key = os.environ["API_KEY"]
    ca = os.environ["API_CA_CERT"]
    response = requests.get(
        f"{base}/api/v1/sensor-data",
        headers={"x-api-key": key},
        verify=ca,
        timeout=30,
    )
    response.raise_for_status()
    df = pd.DataFrame(response.json())
    df = df[df["unit"] == "C"].copy()
    df["value"] = df["value"].astype(float)
    df["recorded_at"] = pd.to_datetime(df["recorded_at"])
    df = df.sort_values("recorded_at")
    if DAYS is not None:
        cutoff = df["recorded_at"].max() - pd.Timedelta(days=DAYS)
        df = df[df["recorded_at"] >= cutoff]
    return df["value"].values.reshape(-1, 1)
