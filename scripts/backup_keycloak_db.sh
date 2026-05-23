#!/usr/bin/env bash
# backup_keycloak_db.sh — pg_dump der Keycloak-Datenbank in docker/backups/
#
# Verwendung:
#   ./scripts/backup_keycloak_db.sh
#
# Voraussetzungen:
#   - keycloak-db-Container läuft (docker compose up -d)
#   - Skript wird aus einem beliebigen Verzeichnis im Repo aufgerufen
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker/docker-compose.yml"
BACKUP_DIR="$PROJECT_ROOT/docker/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/keycloak_${TIMESTAMP}.sql.gz"

# Backups-Verzeichnis anlegen falls nicht vorhanden
mkdir -p "$BACKUP_DIR"

# Prüfen ob keycloak-db läuft
if ! docker compose -f "$COMPOSE_FILE" ps keycloak-db --status running | grep -q "running"; then
    echo "Fehler: keycloak-db-Container läuft nicht." >&2
    echo "Bitte zuerst starten: docker compose -f docker/docker-compose.yml up -d keycloak-db" >&2
    exit 1
fi

echo "➤ Erstelle Backup der Keycloak-Datenbank..."
docker compose -f "$COMPOSE_FILE" exec -T keycloak-db \
    pg_dump -U keycloak keycloak | gzip > "$BACKUP_FILE"

SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "✓ Backup abgeschlossen: $BACKUP_FILE ($SIZE)"

# Alte Backups auflisten
BACKUP_COUNT="$(find "$BACKUP_DIR" -name 'keycloak_*.sql.gz' | wc -l | tr -d ' ')"
echo "  Gespeicherte Backups insgesamt: $BACKUP_COUNT"
