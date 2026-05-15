"""
data_loader.py — Loading or simulating raw temperature data.

Two paths:
  - simulate_temperature(): synthetic sinusoidal data for the first
    training round (no real measurements needed).
  - load_from_csv(): load a CSV exported from PostgreSQL / MariaDB.

Both return a pandas DataFrame with columns:
    minute       int   — time index (0, 1, 2, ...)
    temperature  float — value in °C
"""

import numpy as np
import pandas as pd

import config


def simulate_temperature(total_minutes: int | None = None,
                         seed: int | None = None) -> pd.DataFrame:
    """
    Build a synthetic temperature series, exactly as in the professor's
    slides.

    Formula:
        daily_wave  = OFFSET + AMPL_D * sin( t / 1440 * 2pi - 2pi*0.25 )
        short_wave  = AMPL_S * sin( t / 180  * 2pi + 0.8 )
        noise       = N(0, sigma)
        temperature = daily_wave + short_wave + noise

    The two sinusoids let the LSTM learn both a slow daily cycle and a
    faster 3-hour cycle. The noise prevents the model from memorizing
    exact values.

    Parameters override config defaults when provided; otherwise we read
    everything from config so behaviour stays consistent across runs.
    """
    total_minutes = total_minutes or config.SIM_TOTAL_MINUTES
    seed = seed if seed is not None else config.SIM_SEED

    minutes = np.arange(total_minutes)

    daily_wave = (
        config.SIM_DAILY_OFFSET
        + config.SIM_DAILY_AMPLITUDE
        * np.sin(
            minutes / config.SIM_DAILY_PERIOD * 2 * np.pi
            - 2 * np.pi * config.SIM_DAILY_PHASE_FRAC
        )
    )

    short_wave = config.SIM_SHORT_AMPLITUDE * np.sin(
        minutes / config.SIM_SHORT_PERIOD * 2 * np.pi
        + config.SIM_SHORT_PHASE
    )

    # default_rng with a fixed seed → reproducible across machines and runs.
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, config.SIM_NOISE_STD, total_minutes)

    temperature = daily_wave + short_wave + noise

    return pd.DataFrame({
        "minute": minutes,
        "temperature": temperature,
    })


def load_from_csv(path) -> pd.DataFrame:
    """
    Load real measurements from a CSV file.

    Expected CSV format (header required):
        timestamp,temperature
        2026-01-15T10:00:00,20.13
        2026-01-15T10:00:45,20.18
        ...

    Steps:
      1. Parse timestamps.
      2. Sort by time (defensive — input may be out of order).
      3. Drop duplicates on timestamp.
      4. Resample to a uniform RESAMPLE_FREQ grid (1 minute by default)
         using mean; forward-fill any short gap.
      5. Convert to the same (minute, temperature) layout as the simulator
         so downstream code does not care which loader produced the data.
    """
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates(subset="timestamp")
    df = df.set_index("timestamp")

    # Resample irregular sensor stream to a uniform 1-min grid.
    df = df.resample(config.RESAMPLE_FREQ).mean()
    df["temperature"] = df["temperature"].ffill()

    df = df.reset_index(drop=True)
    df["minute"] = np.arange(len(df))
    return df[["minute", "temperature"]]


def save_to_csv(df: pd.DataFrame, path) -> None:
    """Persist a DataFrame to CSV (used to snapshot the simulated set)."""
    df.to_csv(path, index=False)