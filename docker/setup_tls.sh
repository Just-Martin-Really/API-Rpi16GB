#!/bin/sh
# Generates a local CA and TLS certs for nginx and the MQTT broker.
# Run once on the Raspberry Pi before 'docker compose up'.
# Output goes to /etc/ssl/backend/ (nginx) and docker/mosquitto/ssl/ (broker).
#
# Usage: sudo sh docker/setup_tls.sh

set -e

DOMAIN="backend.lab.local"
CA_DIR="/etc/ssl/backend/ca"
NGINX_DIR="/etc/ssl/backend"
MQTT_DIR="$(dirname "$0")/mosquitto/ssl"

mkdir -p "$CA_DIR" "$NGINX_DIR" "$MQTT_DIR"
echo "⚠ Warning: This script will overwrite existing certificates in:"
echo "  $CA_DIR"
echo "  $NGINX_DIR"
echo "  $MQTT_DIR"
echo "  $(dirname "$0")/secrets/ca_cert.txt"
echo "Press Ctrl+C within 10 seconds to abort..."
sleep 10

echo "==> Generating CA key and certificate"

openssl genrsa -out "$CA_DIR/ca.key" 4096
openssl req -x509 -new -nodes \
    -key "$CA_DIR/ca.key" \
    -sha256 -days 3650 \
    -subj "/CN=IoT-Lab-CA/O=DHBW/C=DE" \
    -out "$CA_DIR/ca.crt"

echo "==> Generating nginx (backend) cert"
openssl genrsa -out "$NGINX_DIR/backend.key" 2048
openssl req -new \
    -key "$NGINX_DIR/backend.key" \
    -subj "/CN=$DOMAIN/O=DHBW/C=DE" \
    -out "$NGINX_DIR/backend.csr"
NGINX_EXT=$(mktemp)
printf "subjectAltName=DNS:%s,DNS:localhost,DNS:nginx,IP:192.168.50.30" "$DOMAIN" > "$NGINX_EXT"
openssl x509 -req -in "$NGINX_DIR/backend.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$NGINX_DIR/backend.crt" \
    -days 825 -sha256 \
    -extfile "$NGINX_EXT"
rm -f "$NGINX_EXT"

echo "==> Generating MQTT broker cert"
openssl genrsa -out "$MQTT_DIR/broker.key" 2048
openssl req -new \
    -key "$MQTT_DIR/broker.key" \
    -subj "/CN=mosquitto/O=DHBW/C=DE" \
    -out "$MQTT_DIR/broker.csr"
MQTT_EXT=$(mktemp)
printf "subjectAltName=DNS:mosquitto,DNS:localhost,DNS:backend-server,DNS:backend-server.lab.local" > "$MQTT_EXT"
openssl x509 -req -in "$MQTT_DIR/broker.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$MQTT_DIR/broker.crt" \
    -days 825 -sha256 \
    -extfile "$MQTT_EXT"
rm -f "$MQTT_EXT"

cp "$CA_DIR/ca.crt" "$MQTT_DIR/ca.crt"

# mosquitto runs as a non-root user inside the container; the bind-mounted
# key file must be world-readable or the process gets EACCES on startup.
chmod 644 "$MQTT_DIR/broker.key" "$MQTT_DIR/broker.crt" "$MQTT_DIR/ca.crt"

echo "==> Copying CA cert to Docker secrets dir"
mkdir -p "$(dirname "$0")/secrets"
cp "$CA_DIR/ca.crt" "$(dirname "$0")/secrets/ca_cert.txt"

echo ""
echo "Done. Copy ca.crt to the Pico and to any client that connects to the broker."
echo "CA cert: $CA_DIR/ca.crt"
