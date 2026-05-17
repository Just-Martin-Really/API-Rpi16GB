-- Run once on the live Pi to apply the archive table without reinitializing the DB.
-- docker exec -i $(docker compose ps -q postgres) psql -U postgres -d sensor < docker/postgres/migrate.sql

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

GRANT DELETE ON TABLE sensor_data TO iot_write_user;
GRANT SELECT, INSERT, DELETE ON TABLE sensor_data_archive TO iot_write_user;
GRANT SELECT ON TABLE sensor_data_archive TO iot_read_user;

-- Add issued_by to distinguish who created an actuator command.
-- 'user'    = manual command from dashboard/API
-- 'machine' = automatic command from LSTM/controller logic

ALTER TABLE actuator_commands
    ADD COLUMN IF NOT EXISTS issued_by VARCHAR(16) NOT NULL DEFAULT 'user';

ALTER TABLE actuator_commands
    DROP CONSTRAINT IF EXISTS chk_actuator_commands_issued_by;

ALTER TABLE actuator_commands
    ADD CONSTRAINT chk_actuator_commands_issued_by
    CHECK (issued_by IN ('user', 'machine'));