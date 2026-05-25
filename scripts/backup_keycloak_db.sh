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
LOCK_FILE="$BACKUP_DIR/.backup.lock"

# Wieviele Tage Backups behalten werden. Älter wird beim nächsten Lauf gelöscht.
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

# Single-instance Schutz via flock. Zweiter paralleler Aufruf (z.B. cron läuft
# länger als das Intervall) bricht direkt ab statt sich mit dem ersten Lauf
# in den .sql.gz zu schreiben.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Backup läuft bereits (lock: $LOCK_FILE). Abbruch." >&2
    exit 1
fi

# Prüfen ob keycloak-db läuft. Über docker inspect, nicht über die
# menschlich-lesbare ps-Ausgabe, damit ein lokales-Image-Tag mit dem Wort
# "running" im Namen das grep nicht fälscht.
if ! docker compose -f "$COMPOSE_FILE" ps -q keycloak-db | grep -q .; then
    echo "Fehler: keycloak-db-Container läuft nicht." >&2
    echo "Bitte zuerst starten: docker compose -f docker/docker-compose.yml up -d keycloak-db" >&2
    exit 1
fi

echo "➤ Erstelle Backup der Keycloak-Datenbank..."
docker compose -f "$COMPOSE_FILE" exec -T keycloak-db \
    pg_dump -U keycloak keycloak | gzip > "$BACKUP_FILE"

SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "✓ Backup abgeschlossen: $BACKUP_FILE ($SIZE)"

# Retention: alte Backups löschen.
DELETED="$(find "$BACKUP_DIR" -name 'keycloak_*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete | wc -l | tr -d ' ')"
if [[ "$DELETED" -gt 0 ]]; then
    echo "  Gelöschte Backups (älter als ${RETENTION_DAYS} Tage): $DELETED"
fi

BACKUP_COUNT="$(find "$BACKUP_DIR" -name 'keycloak_*.sql.gz' | wc -l | tr -d ' ')"
echo "  Gespeicherte Backups insgesamt: $BACKUP_COUNT"
