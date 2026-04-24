#!/bin/sh
# Run once after 'docker compose up' to set DB user passwords from secret files.
# The passwords never appear in the repo or in docker inspect output.

set -e

WRITE_PW=$(cat ./secrets/db_write_password.txt)
READ_PW=$(cat ./secrets/db_read_password.txt)

docker compose exec postgres psql -U postgres -d sensor \
  -c "ALTER USER iot_write_user WITH PASSWORD '$WRITE_PW';"

docker compose exec postgres psql -U postgres -d sensor \
  -c "ALTER USER iot_read_user WITH PASSWORD '$READ_PW';"

echo "Passwords set."
