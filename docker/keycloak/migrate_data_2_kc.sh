#!/bin/bash

# Bricht das Skript ab, falls ein Befehl fehlschlägt
set -e

echo "Starte Datenbank-Migration für Keycloak..."

# 1. Container-ID dynamisch finden
# Geht davon aus, dass das Skript aus dem Ordner ausgeführt wird, in dem die docker-compose.yml liegt
CONTAINER_ID=$(docker compose ps -q postgres)

if [ -z "$CONTAINER_ID" ]; then
    echo "Fehler: Postgres-Container läuft nicht."
    echo "Bitte starte die Container zuerst mit 'docker compose up -d'."
    exit 1
fi

echo "✓ Postgres Container gefunden: $CONTAINER_ID"

# 2. Den neuen Raum für Keycloak anlegen
echo "➤ Lege Keycloak-Datenbank an..."
# Wir verbinden uns mit der Standard-Datenbank 'postgres', um die neue DB anzulegen
docker exec -i "$CONTAINER_ID" psql -U postgres -d postgres < ./postgres/create_keycloak_db.sql

# 3. (Optional) Spezifische Daten-Migrationen durchführen
# Falls deine 'migrate_to_kc.sql' Tabellen verschiebt oder Rechte anpasst
if [ -f "./postgres/migrate_to_kc.sql" ]; then
    echo "➤ Führe spezifische Keycloak-Migrationen aus..."
    # Hier verbinden wir uns mit deiner Hauptdatenbank 'sensor' (passe den Namen an, falls er abweicht)
    docker exec -i "$CONTAINER_ID" psql -U postgres -d sensor < ./postgres/migrate_to_kc.sql
fi

echo "✓ Migration erfolgreich abgeschlossen!"
