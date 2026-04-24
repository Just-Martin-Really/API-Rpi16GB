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

-- Write user: used by the backend to ingest sensor readings
CREATE USER iot_write_user;
GRANT CONNECT ON DATABASE sensor TO iot_write_user;
GRANT USAGE  ON SCHEMA public TO iot_write_user;
GRANT SELECT, INSERT, UPDATE ON TABLE sensor_data TO iot_write_user;
GRANT USAGE, SELECT ON SEQUENCE sensor_data_id_seq TO iot_write_user;

-- Read user: used by the dashboard / reporting endpoints
CREATE USER iot_read_user;
GRANT CONNECT ON DATABASE sensor TO iot_read_user;
GRANT USAGE  ON SCHEMA public TO iot_read_user;
GRANT SELECT ON TABLE sensor_data TO iot_read_user;
