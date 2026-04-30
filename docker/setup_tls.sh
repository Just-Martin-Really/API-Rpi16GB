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
openssl x509 -req -in "$NGINX_DIR/backend.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$NGINX_DIR/backend.crt" \
    -days 825 -sha256 \
    -extfile <(printf "subjectAltName=DNS:%s,DNS:localhost,IP:192.168.50.30" "$DOMAIN")

echo "==> Generating MQTT broker cert"
openssl genrsa -out "$MQTT_DIR/broker.key" 2048
openssl req -new \
    -key "$MQTT_DIR/broker.key" \
    -subj "/CN=mosquitto/O=DHBW/C=DE" \
    -out "$MQTT_DIR/broker.csr"
openssl x509 -req -in "$MQTT_DIR/broker.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$MQTT_DIR/broker.crt" \
    -days 825 -sha256 \
    -extfile <(printf "subjectAltName=DNS:mosquitto,DNS:localhost,DNS:backend-server,DNS:backend-server.lab.local")
cp "$CA_DIR/ca.crt" "$MQTT_DIR/ca.crt"

echo "==> Copying CA cert to Docker secrets dir"
mkdir -p "$(dirname "$0")/secrets"
cp "$CA_DIR/ca.crt" "$(dirname "$0")/secrets/ca_cert.txt"

echo ""
echo "Done. Copy ca.crt to the Pico and to any client that connects to the broker."
echo "CA cert: $CA_DIR/ca.crt"
