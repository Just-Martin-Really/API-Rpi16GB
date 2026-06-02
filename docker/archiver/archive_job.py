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
    # Archive and purge in separate transactions. A long purge on the
    # multi-year archive table can block or fail without taking the
    # much smaller, much more time-critical archive step with it.
    # Each step prints inside its with-conn block after rowcount is known
    # but before the implicit commit on context exit, so a commit failure
    # propagates as the loop's "error: ..." line rather than as a misleading
    # "archived N rows" success message.
    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "WITH moved AS ("
                    " DELETE FROM sensor_data"
                    " WHERE recorded_at < NOW() - %s::interval"
                    " RETURNING id, sensor_id, value, unit, recorded_at"
                    ") "
                    "INSERT INTO sensor_data_archive "
                    "(id, sensor_id, value, unit, recorded_at) "
                    "SELECT id, sensor_id, value, unit, recorded_at FROM moved",
                    (ARCHIVE_AFTER,),
                )
                archived = cur.rowcount
                print(f"archived {archived} rows", flush=True)

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sensor_data_archive "
                    "WHERE archived_at < NOW() - %s::interval",
                    (PURGE_AFTER,),
                )
                purged = cur.rowcount
                print(f"purged {purged} rows", flush=True)
    finally:
        conn.close()


while True:
    try:
        run()
    except Exception as exc:
        print(f"error: {exc}", flush=True)
    time.sleep(INTERVAL)
