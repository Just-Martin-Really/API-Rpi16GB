#!/bin/sh
# Run once after first container start to set DB and MQTT passwords from secret files.
# The passwords never appear in the repo or in docker inspect output.

set -e

WRITE_PW=$(cat ./secrets/db_write_password.txt)
READ_PW=$(cat ./secrets/db_read_password.txt)
MQTT_CTRL_USER=$(cat ./secrets/mqtt_controller_user.txt)
MQTT_CTRL_PASS=$(cat ./secrets/mqtt_controller_password.txt)

echo "==> Setting DB passwords"
docker compose exec postgres psql -U postgres -d sensor \
  -c "ALTER USER iot_write_user WITH PASSWORD '$WRITE_PW';"
docker compose exec postgres psql -U postgres -d sensor \
  -c "ALTER USER iot_read_user WITH PASSWORD '$READ_PW';"

echo "==> Generating MQTT passwd file"
docker compose exec mosquitto mosquitto_passwd -c -b /mosquitto/config/passwd "$MQTT_CTRL_USER" "$MQTT_CTRL_PASS"

echo "==> Restarting controller"
docker compose restart controller

echo "Done."
