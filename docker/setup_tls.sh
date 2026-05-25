#!/usr/bin/env bash
# Generates a local CA and TLS certs for nginx and the MQTT broker.
# Run once on the Raspberry Pi before 'docker compose up'.
# Output goes to /etc/ssl/backend/ (nginx) and docker/mosquitto/ssl/ (broker).
#
# Usage:
#   sudo bash docker/setup_tls.sh             # refuses to overwrite an existing CA
#   sudo bash docker/setup_tls.sh --force-new-ca   # regenerates the CA (invalidates every client cert in circulation)

set -euo pipefail

cd "$(dirname "$0")"

DOMAIN="www.lab.local"
CA_DIR="/etc/ssl/backend/ca"
NGINX_DIR="/etc/ssl/backend"
MQTT_DIR="./mosquitto/ssl"

# Mosquitto eclipse-mosquitto:2 runs as UID 1883. The broker.key is
# bind-mounted into the container read-only, so it must be readable by
# that UID. chown'ing on the host to 1883:1883 lets us keep 0600 perms
# instead of 0644 (which used to be world-readable on the Pi).
MQTT_UID=1883

FORCE_NEW_CA=0
if [[ "${1:-}" == "--force-new-ca" ]]; then
    FORCE_NEW_CA=1
fi

mkdir -p "$CA_DIR" "$NGINX_DIR" "$MQTT_DIR"

# CA regeneration is gated by --force-new-ca because the CA cert is
# distributed to every client (Pico, dashboards, scripts) and overwriting
# it silently breaks all of them. Without the flag the script reuses the
# existing CA and only refreshes the server certs.
if [[ -f "$CA_DIR/ca.crt" && $FORCE_NEW_CA -eq 1 ]]; then
    echo "==> --force-new-ca specified, regenerating CA (existing client trust will break)"
fi

if [[ $FORCE_NEW_CA -eq 1 ]]; then
    echo "==> Regenerating CA (existing client trust will break)"
    openssl genrsa -out "$CA_DIR/ca.key" 4096
    openssl req -x509 -new -nodes \
        -key "$CA_DIR/ca.key" \
        -sha256 -days 3650 \
        -subj "/CN=IoT-Lab-CA/O=DHBW/C=DE" \
        -out "$CA_DIR/ca.crt"
elif [[ ! -f "$CA_DIR/ca.key" ]]; then
    echo "==> Generating CA key and certificate (first run)"
    openssl genrsa -out "$CA_DIR/ca.key" 4096
    openssl req -x509 -new -nodes \
        -key "$CA_DIR/ca.key" \
        -sha256 -days 3650 \
        -subj "/CN=IoT-Lab-CA/O=DHBW/C=DE" \
        -out "$CA_DIR/ca.crt"
else
    echo "==> Reusing existing CA at $CA_DIR/ca.crt"
fi

echo "==> Generating nginx (backend) cert"
openssl genrsa -out "$NGINX_DIR/backend.key" 2048
openssl req -new \
    -key "$NGINX_DIR/backend.key" \
    -subj "/CN=$DOMAIN/O=DHBW/C=DE" \
    -out "$NGINX_DIR/backend.csr"
NGINX_EXT=$(mktemp)
trap 'rm -f "$NGINX_EXT"' EXIT
printf "subjectAltName=DNS:%s,DNS:localhost,DNS:nginx,IP:192.168.50.30" "$DOMAIN" > "$NGINX_EXT"
openssl x509 -req -in "$NGINX_DIR/backend.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$NGINX_DIR/backend.crt" \
    -days 825 -sha256 \
    -extfile "$NGINX_EXT"
rm -f "$NGINX_EXT"
trap - EXIT

echo "==> Generating MQTT broker cert"
openssl genrsa -out "$MQTT_DIR/broker.key" 2048
openssl req -new \
    -key "$MQTT_DIR/broker.key" \
    -subj "/CN=mosquitto/O=DHBW/C=DE" \
    -out "$MQTT_DIR/broker.csr"
MQTT_EXT=$(mktemp)
trap 'rm -f "$MQTT_EXT"' EXIT
printf "subjectAltName=DNS:mosquitto,DNS:localhost,DNS:backend-server,DNS:backend-server.lab.local" > "$MQTT_EXT"
openssl x509 -req -in "$MQTT_DIR/broker.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$MQTT_DIR/broker.crt" \
    -days 825 -sha256 \
    -extfile "$MQTT_EXT"
rm -f "$MQTT_EXT"
trap - EXIT

cp "$CA_DIR/ca.crt" "$MQTT_DIR/ca.crt"

# Permissions:
# - Certificates (.crt) are public material → 0644.
# - Private keys (.key) → 0600. The nginx master process starts as root
#   inside the container and reads its key before dropping privileges, so
#   leaving it root-owned with 0600 is enough.
# - The mosquitto broker process runs as UID 1883 inside the container,
#   so the bind-mounted broker.key needs that ownership; otherwise the
#   broker fails to read it and the listener silently never comes up.
chmod 644 "$MQTT_DIR/broker.crt" "$MQTT_DIR/ca.crt" "$NGINX_DIR/backend.crt"
chmod 600 "$MQTT_DIR/broker.key" "$NGINX_DIR/backend.key" "$CA_DIR/ca.key"
chown "$MQTT_UID:$MQTT_UID" "$MQTT_DIR/broker.key" "$MQTT_DIR/broker.crt" "$MQTT_DIR/ca.crt"

echo "==> Copying CA cert to Docker secrets dir"
mkdir -p ./secrets
cp "$CA_DIR/ca.crt" ./secrets/ca_cert.txt

echo ""
echo "Done. Copy ca.crt to the Pico and to any client that connects to the broker."
echo "CA cert: $CA_DIR/ca.crt"
