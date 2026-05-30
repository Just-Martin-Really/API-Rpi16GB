"""
seed_fake_data.py — fill sensor_data and sensor_data_archive with fake data.

Generates clean, realistic temperature and humidity readings driven by a
single shared ambient curve so that:
  - all sensors in a run see the same diurnal cycle and slow multi-day drift
  - humidity moves opposite to temperature (warmer air → lower RH)
  - per-sensor noise is a small mean-reverting walk on top of the ambient,
    not a free random walk that defines the whole signal

Smart split based on timestamp:
  - rows with recorded_at < NOW() - 7 days  →  sensor_data_archive
    (archived_at is set to recorded_at + 7 days, mirroring what the archiver
    would have done in production)
  - rows with recorded_at >= NOW() - 7 days →  sensor_data

Sensor IDs use a "demo-" prefix so fake data is trivial to clean up:
  DELETE FROM sensor_data         WHERE sensor_id LIKE 'demo-%';
  DELETE FROM sensor_data_archive WHERE sensor_id LIKE 'demo-%';

Signal model:
  ambient_offset(t) = diurnal_sine(t) + slow_drift_sine(t) + coarse_walk(t)
  temperature(t)    = TEMP_MEAN + ambient_offset(t) + per_sensor_deviation(t)
  humidity(t)       = HUMIDITY_MEAN - K * ambient_offset(t) + per_sensor_deviation(t)

  per_sensor_deviation is a small mean-reverting walk with a per-minute cap,
  so individual readings stay near the ambient curve and never drift away.

Anomalies are intentionally not generated. The controller and dashboard
validators filter invalid readings before they reach the database, so
injecting them here would only train the LSTM on a distribution it never
sees in production.

Usage (from the docker/ directory on the Pi):
  docker compose --profile tools run --rm seeder [options]

Examples:
  # 10 000 rows over the last 30 days, default sensors
  docker compose --profile tools run --rm seeder

  # large dataset spanning over 3 years to test the archive purge
  docker compose --profile tools run --rm seeder --rows 200000 --days 1100

  # custom sensors
  docker compose --profile tools run --rm seeder \\
      --sensors demo-temp-01,demo-temp-02,demo-humid-01 \\
      --rows 50000

CLI options:
  --rows N                 total rows to insert                       (default 10000)
  --days N                 timestamp spread in days                   (default 30)
  --sensors a,b,c          comma-separated sensor IDs                 (default demo-temp-01,demo-humid-01)
  --max-delta-per-min F    cap on per-minute change for the per-sensor
                           walk (humidity is scaled x6 internally)    (default 0.25)
  --diurnal-amplitude A    half-swing of the 24h cycle, in °C         (default 1.5)

Environment (provided by docker-compose):
  DB_HOST            postgres hostname inside app-net
  DB_NAME            database name
  /run/secrets/db_write_password   password for iot_write_user
"""

import argparse
import math
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values

BATCH_SIZE = 500
ARCHIVE_THRESHOLD_DAYS = 7

# Mean-reversion strength of the per-sensor deviation walk back toward 0.
REVERSION = 0.05

TEMP_MEAN = 21.5
HUMIDITY_MEAN = 45.0

# Diurnal cycle: indoor temperature peaks in the afternoon, dips before dawn.
DIURNAL_PEAK_HOUR_UTC = 15.0

# Multi-day weather-like meander layered on top of the diurnal cycle.
SLOW_DRIFT_AMPLITUDE_C = 0.6
SLOW_DRIFT_PERIOD_DAYS = 4.0

# Indoor RH drops by roughly 2-4 % per °C of warming when absolute moisture
# is held constant. 2.5 sits in the middle of the empirical range.
HUMIDITY_TEMP_COUPLING = 2.5


def classify(sensor_id: str) -> tuple[str, float, float]:
    """Return (unit, normal_mean, noise_stddev_per_min) for a sensor ID.

    The sensor ID must contain "temp" or "humid"; nothing else maps to a
    unit the production schema accepts. The old fallback produced rows
    with unit="u" that never make it through the real controller.
    """
    name = sensor_id.lower()
    if "temp" in name:
        return ("C", TEMP_MEAN, 0.05)
    if "humid" in name:
        return ("%", HUMIDITY_MEAN, 0.3)
    raise SystemExit(
        f"unknown sensor kind in id {sensor_id!r}: name must contain"
        f" 'temp' or 'humid' so the seeder picks a valid unit (C or %)"
    )


def delta_cap_for(sensor_id: str, base_cap: float) -> float:
    """Per-minute change cap for the sensor. Humidity moves faster than temp."""
    name = sensor_id.lower()
    if "humid" in name:
        return base_cap * 6.0
    return base_cap


def connect():
    password = open("/run/secrets/db_write_password").read().strip()
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user="iot_write_user",
        password=password,
    )


def insert_batch_active(cur, rows):
    execute_values(
        cur,
        "INSERT INTO sensor_data (sensor_id, value, unit, recorded_at) VALUES %s",
        rows,
    )


def insert_batch_archive(cur, rows):
    # Grab fresh IDs from the shared sequence so archive rows don't collide
    # with future sensor_data inserts.
    rows_with_ids = []
    for sensor_id, value, unit, recorded_at, archived_at in rows:
        cur.execute("SELECT nextval('sensor_data_id_seq')")
        new_id = cur.fetchone()[0]
        rows_with_ids.append((new_id, sensor_id, value, unit, recorded_at, archived_at))
    execute_values(
        cur,
        "INSERT INTO sensor_data_archive (id, sensor_id, value, unit, recorded_at, archived_at) VALUES %s",
        rows_with_ids,
    )


def build_ambient_offset(span_seconds: float, now: datetime,
                         diurnal_amplitude: float):
    """Return a callable ambient_offset(ts) → °C-offset from TEMP_MEAN.

    The same callable is used by every sensor in the run, so the diurnal cycle
    and slow drift are shared (and humidity tracks temperature inversely).

    The slow component is a mean-reverting walk precomputed on a 10-minute
    grid; we interpolate linearly between grid points at lookup time.
    """
    grid_step_seconds = 10 * 60
    n_points = max(2, int(span_seconds // grid_step_seconds) + 2)
    walk = [0.0]
    walk_reversion = 0.02
    walk_stddev = 0.04
    for _ in range(n_points - 1):
        pull = -walk[-1] * walk_reversion
        walk.append(walk[-1] + pull + random.gauss(0.0, walk_stddev))
    oldest = now - timedelta(seconds=span_seconds)

    # Phase the diurnal sine so it peaks at DIURNAL_PEAK_HOUR_UTC.
    phase_hours = DIURNAL_PEAK_HOUR_UTC - 6.0

    def offset(ts: datetime) -> float:
        elapsed = (ts - oldest).total_seconds()

        idx_f = elapsed / grid_step_seconds
        i0 = max(0, min(n_points - 2, int(idx_f)))
        frac = idx_f - i0
        slow_walk = walk[i0] * (1 - frac) + walk[i0 + 1] * frac

        hour = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
        diurnal = diurnal_amplitude * math.sin(
            2 * math.pi * (hour - phase_hours) / 24.0
        )

        drift = SLOW_DRIFT_AMPLITUDE_C * math.sin(
            2 * math.pi * elapsed / (SLOW_DRIFT_PERIOD_DAYS * 86400.0)
        )

        return diurnal + drift + slow_walk

    return offset


def generate_series(sensor_id, count, span_seconds, now,
                    base_delta_cap, ambient_offset):
    """Yield (sensor_id, value, unit, recorded_at) tuples for one sensor."""
    unit, mean, noise_stddev_per_min = classify(sensor_id)
    delta_cap = delta_cap_for(sensor_id, base_delta_cap)
    is_temp = unit == "C"

    if count == 1:
        timestamps = [now - timedelta(seconds=span_seconds / 2)]
    else:
        step = span_seconds / (count - 1)
        timestamps = [
            now - timedelta(seconds=max(
                0.0,
                span_seconds - i * step + random.uniform(-step / 4, step / 4),
            ))
            for i in range(count)
        ]

    # Per-sensor deviation from the shared ambient curve. Small, mean-reverting.
    deviation = random.gauss(0.0, noise_stddev_per_min * 5)
    prev_ts = timestamps[0]

    for ts in timestamps:
        dt_minutes = max((ts - prev_ts).total_seconds() / 60.0, 1e-6)
        pull = -deviation * REVERSION
        noise = random.gauss(0.0, noise_stddev_per_min * math.sqrt(dt_minutes))
        step_value = pull + noise
        max_step = delta_cap * dt_minutes
        if step_value > max_step:
            step_value = max_step
        elif step_value < -max_step:
            step_value = -max_step
        deviation += step_value

        amb = ambient_offset(ts)
        if is_temp:
            value = mean + amb + deviation
        else:
            value = mean - HUMIDITY_TEMP_COUPLING * amb + deviation

        prev_ts = ts
        yield (sensor_id, round(value, 2), unit, ts)


def main():
    parser = argparse.ArgumentParser(description="Seed fake sensor data.")
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--sensors", type=str, default="demo-temp-01,demo-humid-01")
    parser.add_argument("--max-delta-per-min", type=float, default=0.25,
                        help="cap on per-minute change for the per-sensor walk "
                             "(humidity is scaled internally)")
    parser.add_argument("--diurnal-amplitude", type=float, default=1.5,
                        help="half-swing of the 24h ambient cycle, in °C")
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",") if s.strip()]
    if not sensors:
        parser.error("--sensors must contain at least one ID")

    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=ARCHIVE_THRESHOLD_DAYS)
    span_seconds = args.days * 24 * 3600

    ambient_offset = build_ambient_offset(span_seconds, now, args.diurnal_amplitude)

    print(f"generating {args.rows} rows across {args.days} days "
          f"for sensors: {', '.join(sensors)} "
          f"(diurnal: ±{args.diurnal_amplitude}°C, "
          f"max walk delta: {args.max_delta_per_min}/min)", flush=True)

    # Split row budget across sensors. Any remainder goes to the first sensors
    # so total stays exactly --rows.
    per_sensor = [args.rows // len(sensors)] * len(sensors)
    for i in range(args.rows % len(sensors)):
        per_sensor[i] += 1

    active_buf = []
    archive_buf = []
    counts = {"active": 0, "archive": 0}

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                for sensor_id, count in zip(sensors, per_sensor):
                    if count == 0:
                        continue

                    for sid, value, unit, recorded_at in generate_series(
                        sensor_id, count, span_seconds, now,
                        args.max_delta_per_min, ambient_offset,
                    ):
                        if recorded_at < archive_cutoff:
                            archived_at = recorded_at + timedelta(days=ARCHIVE_THRESHOLD_DAYS)
                            archive_buf.append((sid, value, unit, recorded_at, archived_at))
                        else:
                            active_buf.append((sid, value, unit, recorded_at))

                        if len(active_buf) >= BATCH_SIZE:
                            insert_batch_active(cur, active_buf)
                            counts["active"] += len(active_buf)
                            active_buf.clear()
                            print(f"  ...{counts['active'] + counts['archive']} rows inserted", flush=True)

                        if len(archive_buf) >= BATCH_SIZE:
                            insert_batch_archive(cur, archive_buf)
                            counts["archive"] += len(archive_buf)
                            archive_buf.clear()
                            print(f"  ...{counts['active'] + counts['archive']} rows inserted", flush=True)

                if active_buf:
                    insert_batch_active(cur, active_buf)
                    counts["active"] += len(active_buf)
                if archive_buf:
                    insert_batch_archive(cur, archive_buf)
                    counts["archive"] += len(archive_buf)
    finally:
        conn.close()

    print(f"done: {counts['active']} into sensor_data, "
          f"{counts['archive']} into sensor_data_archive",
          flush=True)


if __name__ == "__main__":
    main()
