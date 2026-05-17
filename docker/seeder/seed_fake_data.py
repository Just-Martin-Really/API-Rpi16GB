"""
seed_fake_data.py — fill sensor_data and sensor_data_archive with fake data.

Generates realistic temperature and humidity readings as a per-sensor random
walk (so consecutive readings differ smoothly over time, not jumping multiple
degrees per minute) and optionally injects anomalies to simulate a
malfunctioning sensor.

Smart split based on timestamp:
  - rows with recorded_at < NOW() - 7 days  →  sensor_data_archive
    (archived_at is set to recorded_at + 7 days, mirroring what the archiver
    would have done in production)
  - rows with recorded_at >= NOW() - 7 days →  sensor_data

Sensor IDs use a "demo-" prefix so fake data is trivial to clean up:
  DELETE FROM sensor_data         WHERE sensor_id LIKE 'demo-%';
  DELETE FROM sensor_data_archive WHERE sensor_id LIKE 'demo-%';

How values are generated:
  - For each sensor, rows are spread evenly across the time window and
    processed in chronological order.
  - Each reading is the previous reading nudged by a small random step plus
    a mean-reversion pull back toward the sensor's normal mean.
  - The step is hard-capped at max_delta_per_min * dt_minutes, so a 60-second
    gap between samples can never move the value by more than max_delta_per_min.
  - Defaults assume a normally heated room: temp ~21.5 °C, humidity ~45 %.
  - Anomalies bypass the cap on purpose and jump to mean + anomaly_magnitude
    (that is what makes them anomalous).

Usage (from the docker/ directory on the Pi):
  docker compose --profile tools run --rm seeder [options]

Examples:
  # 10 000 rows over the last 30 days, default sensors, 0.5 % anomalies
  docker compose --profile tools run --rm seeder

  # large dataset spanning over 3 years to test the archive purge
  docker compose --profile tools run --rm seeder --rows 200000 --days 1100

  # custom sensors, heavier corruption (5 % of rows are anomalies)
  docker compose --profile tools run --rm seeder \\
      --sensors demo-temp-01,demo-temp-02,demo-humid-01 \\
      --rows 50000 --anomaly-percent 5

  # clean run with no anomalies at all
  docker compose --profile tools run --rm seeder --anomaly-percent 0

CLI options:
  --rows N                 total rows to insert                       (default 10000)
  --days N                 timestamp spread in days                   (default 30)
  --sensors a,b,c          comma-separated sensor IDs                 (default demo-temp-01,demo-humid-01)
  --anomaly-percent P      percentage of rows that are anomalies      (default 0.5, set 0 to disable)
  --anomaly-magnitude M    how far an anomaly deviates from mean,
                           in the sensor's unit (°C, % or unitless)   (default 15.0)
  --max-delta-per-min F    cap on per-minute change for temperature
                           sensors (humidity is scaled x6 internally) (default 0.25)

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

# Mean-reversion strength: each step pulls (mean - value) * REVERSION
# fraction back toward the mean before adding random noise. Low enough
# that walks meander, high enough that they don't drift away forever.
REVERSION = 0.05


def classify(sensor_id: str) -> tuple[str, float, float]:
    """Return (unit, normal_mean, noise_stddev_per_min) for a sensor ID.

    noise_stddev_per_min controls the size of the random step per minute
    before it is clamped by max_delta_per_min.
    """
    name = sensor_id.lower()
    if "temp" in name:
        return ("°C", 21.5, 0.05)
    if "humid" in name:
        return ("%", 45.0, 0.3)
    return ("u", 50.0, 0.5)


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


def generate_series(sensor_id, count, span_seconds, now,
                    anomaly_indices, anomaly_magnitude, base_delta_cap):
    """Yield (sensor_id, value, unit, recorded_at) tuples for one sensor.

    Walks chronologically forward from oldest to newest so that each value
    can depend on the previous one. anomaly_indices is a set of positions
    (0..count-1) where the value should be forced to mean + anomaly_magnitude.
    """
    unit, mean, noise_stddev_per_min = classify(sensor_id)
    delta_cap = delta_cap_for(sensor_id, base_delta_cap)

    # Evenly-spaced timestamps from oldest to newest. Tiny jitter so multiple
    # sensors don't all land on the exact same recorded_at.
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

    value = random.gauss(mean, noise_stddev_per_min * 10)  # seed somewhere near mean
    prev_ts = timestamps[0]

    for i, ts in enumerate(timestamps):
        if i in anomaly_indices:
            # Anomaly: snap to mean + magnitude, bypass the per-minute cap on
            # purpose. The next non-anomaly reading will start walking back
            # from this spike.
            value = mean + anomaly_magnitude
        else:
            dt_minutes = max((ts - prev_ts).total_seconds() / 60.0, 1e-6)
            # Mean-reverting random step, clamped to physical realism.
            # Noise scales with sqrt(dt) so variance grows linearly in time,
            # which is the standard Wiener-process behavior for a random walk.
            pull = (mean - value) * REVERSION
            noise = random.gauss(0.0, noise_stddev_per_min * math.sqrt(dt_minutes))
            step_value = pull + noise
            max_step = delta_cap * dt_minutes
            if step_value > max_step:
                step_value = max_step
            elif step_value < -max_step:
                step_value = -max_step
            value += step_value

        prev_ts = ts
        yield (sensor_id, round(value, 2), unit, ts)


def main():
    parser = argparse.ArgumentParser(description="Seed fake sensor data.")
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--sensors", type=str, default="demo-temp-01,demo-humid-01")
    parser.add_argument("--anomaly-percent", type=float, default=0.5,
                        help="percentage of rows that are anomalies; 0 disables anomalies")
    parser.add_argument("--anomaly-magnitude", type=float, default=15.0,
                        help="how far an anomaly deviates from the sensor mean, in its unit")
    parser.add_argument("--max-delta-per-min", type=float, default=0.25,
                        help="cap on per-minute change for temperature sensors "
                             "(humidity is scaled internally)")
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",") if s.strip()]
    if not sensors:
        parser.error("--sensors must contain at least one ID")
    if args.anomaly_percent < 0 or args.anomaly_percent > 100:
        parser.error("--anomaly-percent must be between 0 and 100")

    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=ARCHIVE_THRESHOLD_DAYS)
    span_seconds = args.days * 24 * 3600

    print(f"generating {args.rows} rows across {args.days} days "
          f"for sensors: {', '.join(sensors)} "
          f"(anomalies: {args.anomaly_percent}% @ +{args.anomaly_magnitude}, "
          f"max temp delta: {args.max_delta_per_min}/min)", flush=True)

    # Split row budget across sensors. Any remainder goes to the first sensors
    # so total stays exactly --rows.
    per_sensor = [args.rows // len(sensors)] * len(sensors)
    for i in range(args.rows % len(sensors)):
        per_sensor[i] += 1

    active_buf = []
    archive_buf = []
    counts = {"active": 0, "archive": 0, "anomalies": 0}

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                for sensor_id, count in zip(sensors, per_sensor):
                    if count == 0:
                        continue

                    n_anomalies = int(round(count * args.anomaly_percent / 100.0))
                    anomaly_indices = set(random.sample(range(count), n_anomalies)) if n_anomalies > 0 else set()
                    counts["anomalies"] += len(anomaly_indices)

                    for sid, value, unit, recorded_at in generate_series(
                        sensor_id, count, span_seconds, now,
                        anomaly_indices, args.anomaly_magnitude,
                        args.max_delta_per_min,
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
          f"{counts['archive']} into sensor_data_archive, "
          f"{counts['anomalies']} anomalies",
          flush=True)


if __name__ == "__main__":
    main()
