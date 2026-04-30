#!/bin/sh
# Run once after first container start to set DB and MQTT passwords from secret files.
# The passwords never appear in the repo or in docker inspect output.

set -e

WRITE_PW=$(cat ./secrets/db_write_password.txt)
READ_PW=$(cat ./secrets/db_read_password.txt)
MQTT_CTRL_USER=$(cat ./secrets/mqtt_controller_user.txt)
MQTT_CTRL_PASS=$(cat ./secrets/mqtt_controller_password.txt)
MQTT_SENSOR01_PASS=$(cat ./secrets/mqtt_sensor01_password.txt)

echo "==> Setting DB passwords"
docker compose exec postgres psql -U postgres -d sensor \
  -c "ALTER USER iot_write_user WITH PASSWORD '$WRITE_PW';"
docker compose exec postgres psql -U postgres -d sensor \
  -c "ALTER USER iot_read_user WITH PASSWORD '$READ_PW';"

echo "==> Generating MQTT passwd file"
# -c creates a fresh file with the first user; -b appends without prompting
docker compose exec -u root mosquitto mosquitto_passwd -c -b /mosquitto/config/passwd sensor01 "$MQTT_SENSOR01_PASS"
docker compose exec -u root mosquitto mosquitto_passwd -b /mosquitto/config/passwd "$MQTT_CTRL_USER" "$MQTT_CTRL_PASS"

echo "==> Restarting broker and controller"
docker compose restart mosquitto controller

echo "Done."
