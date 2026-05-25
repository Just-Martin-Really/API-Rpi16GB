-- Runs once on first container start against the 'sensor' database.

CREATE TABLE IF NOT EXISTS sensor_data (
    id          BIGSERIAL        PRIMARY KEY,
    sensor_id   VARCHAR(64)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        VARCHAR(16)      NOT NULL,
    recorded_at TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sensor_data_sensor_id   ON sensor_data (sensor_id);
CREATE INDEX IF NOT EXISTS idx_sensor_data_recorded_at ON sensor_data (recorded_at DESC);

-- Users are created without passwords here (safe to commit).
-- Passwords are set after first start via set_passwords.sh — never stored in the repo.

-- Write user: used by the backend to ingest sensor readings.
-- No UPDATE on sensor_data: ingest only inserts, archive only deletes;
-- rewriting historical readings should not be reachable from the API.
CREATE USER iot_write_user;
GRANT CONNECT ON DATABASE sensor TO iot_write_user;
GRANT USAGE  ON SCHEMA public TO iot_write_user;
GRANT SELECT, INSERT ON TABLE sensor_data TO iot_write_user;
GRANT USAGE, SELECT ON SEQUENCE sensor_data_id_seq TO iot_write_user;

-- Read user: used by the dashboard / reporting endpoints
CREATE USER iot_read_user;
GRANT CONNECT ON DATABASE sensor TO iot_read_user;
GRANT USAGE  ON SCHEMA public TO iot_read_user;
GRANT SELECT ON TABLE sensor_data TO iot_read_user;

-- Actuator commands written by the dashboard or by automated controllers,
-- consumed by controller.py. issued_by distinguishes the source.
CREATE TABLE IF NOT EXISTS actuator_commands (
    id          BIGSERIAL    PRIMARY KEY,
    actuator_id VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_by   VARCHAR(16)  NOT NULL DEFAULT 'user',
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ,

    CONSTRAINT chk_actuator_commands_issued_by
        CHECK (issued_by IN ('user', 'machine'))
);

CREATE INDEX IF NOT EXISTS idx_actuator_commands_unsent
    ON actuator_commands (issued_at)
    WHERE sent_at IS NULL;

GRANT INSERT, SELECT, UPDATE ON TABLE actuator_commands TO iot_write_user;
GRANT USAGE, SELECT ON SEQUENCE actuator_commands_id_seq TO iot_write_user;

-- Sensor data requests (e.g. emergency "read now") written by the dashboard or
-- watchdog, consumed by controller.py via the same drain pattern as actuators.
CREATE TABLE IF NOT EXISTS sensor_requests (
    id          BIGSERIAL    PRIMARY KEY,
    sensor_id   VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sensor_requests_unsent
    ON sensor_requests (issued_at)
    WHERE sent_at IS NULL;

GRANT INSERT, SELECT, UPDATE ON TABLE sensor_requests TO iot_write_user;
GRANT USAGE, SELECT ON SEQUENCE sensor_requests_id_seq TO iot_write_user;

-- Archive table: rows older than 7 days are moved here; purged after 3 years
CREATE TABLE IF NOT EXISTS sensor_data_archive (
    id          BIGINT           PRIMARY KEY,
    sensor_id   VARCHAR(64)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        VARCHAR(16)      NOT NULL,
    recorded_at TIMESTAMPTZ      NOT NULL,
    archived_at TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archive_sensor_id   ON sensor_data_archive (sensor_id);
CREATE INDEX IF NOT EXISTS idx_archive_recorded_at ON sensor_data_archive (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_archived_at ON sensor_data_archive (archived_at DESC);

-- Archiver needs DELETE on sensor_data to move rows out
GRANT DELETE ON TABLE sensor_data TO iot_write_user;
GRANT SELECT, INSERT, DELETE ON TABLE sensor_data_archive TO iot_write_user;
GRANT SELECT ON TABLE sensor_data_archive TO iot_read_user;

-- Prometheus exporter user: read-only, used by postgres_exporter to publish
-- DB-level metrics (connections, transactions, table sizes, replication lag).
-- pg_monitor is the canonical role for this purpose; it grants SELECT on
-- pg_stat_* views without giving access to row data.
CREATE USER postgres_exporter_user;
GRANT CONNECT ON DATABASE sensor TO postgres_exporter_user;
GRANT pg_monitor TO postgres_exporter_user;

-- Grafana read-only user: used by the Postgres datasource in Grafana to
-- query sensor_data and sensor_data_archive for the "Sensor-Daten" dashboard.
-- Kept distinct from iot_read_user so its credentials can be rotated
-- independently and so it cannot accidentally be granted write access.
CREATE USER grafana_read_user;
GRANT CONNECT ON DATABASE sensor TO grafana_read_user;
GRANT USAGE ON SCHEMA public TO grafana_read_user;
GRANT SELECT ON TABLE sensor_data TO grafana_read_user;
GRANT SELECT ON TABLE sensor_data_archive TO grafana_read_user;
