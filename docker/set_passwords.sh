#!/usr/bin/env bash
# Run once after first container start to set DB and MQTT passwords from
# secret files. The passwords never appear in the repo, in argv (ps),
# or in `docker inspect` output.

set -euo pipefail

# Always run relative to this file so the ./secrets/ paths and
# `docker compose` resolve regardless of where the script is invoked from.
cd "$(dirname "$0")"

WRITE_PW=$(cat ./secrets/db_write_password.txt)
READ_PW=$(cat ./secrets/db_read_password.txt)
EXPORTER_PW=$(cat ./secrets/db_exporter_password.txt)
GRAFANA_PW=$(cat ./secrets/db_grafana_password.txt)
MQTT_CTRL_USER=$(cat ./secrets/mqtt_controller_user.txt)
MQTT_CTRL_PASS=$(cat ./secrets/mqtt_controller_password.txt)
MQTT_SENSOR01_PASS=$(cat ./secrets/mqtt_sensor01_password.txt)

echo "==> Setting DB passwords"
# psql via stdin heredoc — passwords never appear on argv.
docker compose exec -T postgres psql -U postgres -d sensor <<SQL
ALTER USER iot_write_user        WITH PASSWORD '${WRITE_PW}';
ALTER USER iot_read_user         WITH PASSWORD '${READ_PW}';
ALTER USER postgres_exporter_user WITH PASSWORD '${EXPORTER_PW}';
ALTER USER grafana_read_user     WITH PASSWORD '${GRAFANA_PW}';
SQL

echo "==> Generating MQTT passwd file"
# mosquitto_passwd has no stdin mode for the password; -b puts it on argv.
# Workaround: write the passwd file in plaintext via stdin (so the
# plaintext never appears on argv), then `mosquitto_passwd -U` rewrites
# the file in place with hashes. The plaintext window is bounded to the
# few milliseconds between cat and -U, inside a root-owned 0600 file in
# a single-tenant container.
docker compose exec -T -u root mosquitto sh -c 'umask 077 && cat > /mosquitto/config/passwd' <<PASSWD
sensor01:${MQTT_SENSOR01_PASS}
${MQTT_CTRL_USER}:${MQTT_CTRL_PASS}
PASSWD
docker compose exec -u root mosquitto mosquitto_passwd -U /mosquitto/config/passwd

echo "==> Restarting broker and controller"
docker compose restart mosquitto controller

echo "Done."
