import os
import time
import psycopg2

ARCHIVE_AFTER = "7 days"
PURGE_AFTER   = "3 years"
INTERVAL      = 24 * 60 * 60


def connect():
    password = open("/run/secrets/db_write_password").read().strip()
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user="iot_write_user",
        password=password,
    )


def run():
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    WITH moved AS (
                        DELETE FROM sensor_data
                        WHERE recorded_at < NOW() - INTERVAL '{ARCHIVE_AFTER}'
                        RETURNING id, sensor_id, value, unit, recorded_at
                    )
                    INSERT INTO sensor_data_archive (id, sensor_id, value, unit, recorded_at)
                    SELECT id, sensor_id, value, unit, recorded_at FROM moved
                """)
                archived = cur.rowcount

                cur.execute(f"""
                    DELETE FROM sensor_data_archive
                    WHERE archived_at < NOW() - INTERVAL '{PURGE_AFTER}'
                """)
                purged = cur.rowcount

        print(f"archived {archived} rows, purged {purged} rows", flush=True)
    finally:
        conn.close()


while True:
    try:
        run()
    except Exception as exc:
        print(f"error: {exc}", flush=True)
    time.sleep(INTERVAL)
