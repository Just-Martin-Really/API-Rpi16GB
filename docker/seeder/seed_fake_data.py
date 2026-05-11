"""
seed_fake_data.py — fill sensor_data and sensor_data_archive with fake data.

Generates realistic temperature and humidity readings (plus optional anomalies
to simulate a malfunctioning sensor) and inserts them into both tables.

Smart split based on timestamp:
  - rows with recorded_at < NOW() - 7 days  →  sensor_data_archive
    (archived_at is set to recorded_at + 7 days, mirroring what the archiver
    would have done in production)
  - rows with recorded_at >= NOW() - 7 days →  sensor_data

Sensor IDs use a "demo-" prefix so fake data is trivial to clean up:
  DELETE FROM sensor_data         WHERE sensor_id LIKE 'demo-%';
  DELETE FROM sensor_data_archive WHERE sensor_id LIKE 'demo-%';

Usage (from the docker/ directory on the Pi):
  docker compose --profile tools run --rm seeder [options]

Examples:
  # 10 000 rows over the last 30 days, default sensors
  docker compose --profile tools run --rm seeder

  # large dataset spanning over 3 years to test the archive purge
  docker compose --profile tools run --rm seeder --rows 200000 --days 1100

  # custom sensors, denser anomalies
  docker compose --profile tools run --rm seeder \\
      --sensors demo-temp-01,demo-temp-02,demo-humid-01 \\
      --rows 50000 --anomaly-rate 100

CLI options:
  --rows N           total rows to insert            (default 10000)
  --days N           timestamp spread in days        (default 30)
  --sensors a,b,c    comma-separated sensor IDs      (default demo-temp-01,demo-humid-01)
  --anomaly-rate N   one anomaly every N rows        (default 500, set 0 to disable)

Environment (provided by docker-compose):
  DB_HOST            postgres hostname inside app-net
  DB_NAME            database name
  /run/secrets/db_write_password   password for iot_write_user
"""

import argparse
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values

BATCH_SIZE = 500
ARCHIVE_THRESHOLD_DAYS = 7


def classify(sensor_id: str) -> tuple[str, float, float, float]:
    """Return (unit, normal_mean, normal_stddev, anomaly_value) for a sensor ID."""
    name = sensor_id.lower()
    if "temp" in name:
        return ("C", 22.0, 1.2, 40.0)
    if "humid" in name:
        return ("%", 45.0, 4.0, 95.0)
    return ("u", 50.0, 10.0, 100.0)


def generate_value(sensor_id: str, is_anomaly: bool) -> tuple[float, str]:
    unit, mean, stddev, anomaly = classify(sensor_id)
    if is_anomaly:
        return (round(anomaly + random.uniform(-1.5, 1.5), 2), unit)
    return (round(random.gauss(mean, stddev), 2), unit)


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


def main():
    parser = argparse.ArgumentParser(description="Seed fake sensor data.")
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--sensors", type=str, default="demo-temp-01,demo-humid-01")
    parser.add_argument("--anomaly-rate", type=int, default=500,
                        help="one anomaly every N rows; 0 disables anomalies")
    args = parser.parse_args()

    sensors = [s.strip() for s in args.sensors.split(",") if s.strip()]
    if not sensors:
        parser.error("--sensors must contain at least one ID")

    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=ARCHIVE_THRESHOLD_DAYS)
    span_seconds = args.days * 24 * 3600

    print(f"generating {args.rows} rows across {args.days} days "
          f"for sensors: {', '.join(sensors)}", flush=True)

    active_buf = []
    archive_buf = []
    counts = {"active": 0, "archive": 0, "anomalies": 0}

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                for i in range(args.rows):
                    sensor_id = sensors[i % len(sensors)]
                    is_anomaly = (
                        args.anomaly_rate > 0
                        and i > 0
                        and i % args.anomaly_rate == 0
                    )
                    value, unit = generate_value(sensor_id, is_anomaly)
                    if is_anomaly:
                        counts["anomalies"] += 1

                    offset = random.uniform(0, span_seconds)
                    recorded_at = now - timedelta(seconds=offset)

                    if recorded_at < archive_cutoff:
                        archived_at = recorded_at + timedelta(days=ARCHIVE_THRESHOLD_DAYS)
                        archive_buf.append((sensor_id, value, unit, recorded_at, archived_at))
                    else:
                        active_buf.append((sensor_id, value, unit, recorded_at))

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
