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

-- Dashboard users table (login credentials for the web dashboard)
CREATE TABLE IF NOT EXISTS dashboard_users (
    id              SERIAL       PRIMARY KEY,
    username        VARCHAR(64)  UNIQUE NOT NULL,
    password_sha256 CHAR(64)     NOT NULL
);

-- Default admin user. Password is 'changeme', update via SQL before going live:
-- UPDATE dashboard_users SET password_sha256 = encode(sha256('newpassword'::bytea), 'hex') WHERE username = 'admin';
INSERT INTO dashboard_users (username, password_sha256)
VALUES ('admin', encode(sha256('changeme'::bytea), 'hex'))
ON CONFLICT DO NOTHING;

-- Service account for the MQTT controller. Password must match secrets/api_password.txt.
INSERT INTO dashboard_users (username, password_sha256)
VALUES ('controller_service', encode(sha256('changeme'::bytea), 'hex'))
ON CONFLICT DO NOTHING;

GRANT SELECT ON TABLE dashboard_users TO iot_read_user;

-- Actuator commands written by the dashboard, consumed by controller.py
CREATE TABLE IF NOT EXISTS actuator_commands (
    id          BIGSERIAL    PRIMARY KEY,
    actuator_id VARCHAR(64)  NOT NULL,
    command     VARCHAR(64)  NOT NULL,
    issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_actuator_commands_unsent
    ON actuator_commands (issued_at)
    WHERE sent_at IS NULL;

GRANT INSERT, SELECT, UPDATE ON TABLE actuator_commands TO iot_write_user;
GRANT USAGE, SELECT ON SEQUENCE actuator_commands_id_seq TO iot_write_user;

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
