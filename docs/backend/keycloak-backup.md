# Keycloak — Backup & Restore

Die Keycloak-Datenbank (`keycloak-db`) enthält den kompletten Realm-Zustand:
Benutzer, Rollen, Client-Konfigurationen, Sessions und gespeicherte Secrets.
Ein Verlust des Volumes ohne Backup bedeutet, dass der gesamte IAM-Stand
neu aufgebaut werden muss (Realm-Import startet zwar automatisch, aber
manuell angelegte User und Passwortänderungen gehen verloren).

---

## Backup

### Manuell (on-demand)

```sh
# aus dem Repo-Root
./scripts/backup_keycloak_db.sh
```

Das Skript:
1. prüft ob `keycloak-db` läuft
2. führt `pg_dump` im Container aus
3. komprimiert die Ausgabe mit `gzip`
4. schreibt das Ergebnis nach `docker/backups/keycloak_<YYYYMMDD_HHMMSS>.sql.gz`

Beispielausgabe:
```
➤ Erstelle Backup der Keycloak-Datenbank...
✓ Backup abgeschlossen: docker/backups/keycloak_20260523_143012.sql.gz (48K)
  Gespeicherte Backups insgesamt: 3
```

### Automatisiert (Cron auf dem Pi)

Tägliches Backup um 03:00 Uhr:

```sh
crontab -e
```

Eintrag:
```
0 3 * * * /home/<user>/API-Rpi16GB/scripts/backup_keycloak_db.sh >> /var/log/keycloak-backup.log 2>&1
```

### Backup-Dateien

```
docker/backups/
├── .gitignore                       ← Backups sind gitignored
├── keycloak_20260523_143012.sql.gz
└── keycloak_20260524_030001.sql.gz
```

> **Hinweis:** `docker/backups/` ist per `.gitignore` ausgeschlossen.
> Backups müssen manuell auf ein externes Medium oder einen Remote-Speicher
> übertragen werden (z. B. `scp`, `rsync`, S3).

---

## Restore

### Vorbereitung

```sh
# Stack stoppen (Keycloak muss offline sein, DB darf laufen)
cd docker
docker compose stop keycloak

# Datenbank leeren (alle Keycloak-Tabellen entfernen)
docker compose exec keycloak-db psql -U keycloak keycloak \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

### Einspielen

```sh
# Backup-Datei wählen (neueste zuerst)
ls -lh docker/backups/keycloak_*.sql.gz

# Einspielen
gunzip -c docker/backups/keycloak_<TIMESTAMP>.sql.gz | \
  docker compose exec -T keycloak-db psql -U keycloak keycloak
```

### Keycloak neu starten

```sh
docker compose start keycloak
docker compose logs -f keycloak
# Warten bis: "Keycloak 26.1.x started"
```

### Verify

```sh
# Realm muss wieder vorhanden sein
curl -s http://localhost:8080/realms/iot | jq .realm
# Ausgabe: "iot"

# Benutzer prüfen (mit Admin-Token)
curl -s http://localhost:8080/realms/iot/protocol/openid-connect/token \
  -d "grant_type=password" \
  -d "client_id=dashboard-client" \
  -d "username=iotuser01" \
  -d "password=Test1234!" | jq .access_token
```

---

## Notfall: Volume komplett verloren

Wenn `keycloak_db_data` verloren gegangen ist und kein Backup existiert:

```sh
# Stack komplett neu starten — keycloak-db legt leeres Volume an,
# Keycloak importiert iot-realm.json automatisch beim ersten Start
docker compose up -d keycloak-db keycloak
docker compose logs -f keycloak
```

Dabei gehen verloren:
- Manuell angelegte Benutzer (außer `iotuser01`)
- Passwortänderungen aller Benutzer
- Aktive Sessions

Erhalten bleibt:
- Alle Konfiguration, die in `iot-realm.json` definiert ist
  (Realm, Rollen, Clients, `iotuser01`)
