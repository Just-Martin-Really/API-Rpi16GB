# Phase 6 – Reverse Proxy, TLS und Hostname-Wechsel

## Ziel

Anpassung der nginx-Konfiguration für Keycloak sowie Umstellung des öffentlichen Hostnamens von `backend.lab.local` auf `www.lab.local`.

---

## Durchgeführte Änderungen

### nginx Reverse Proxy

#### Keycloak

Route:

```text
/auth/*
```

Weiterleitung:

```text
http://keycloak:8080/auth/*
```

Der `/auth`-Prefix bleibt erhalten, da Keycloak mit:

```text
KC_HTTP_RELATIVE_PATH=/auth
```

konfiguriert ist.

Zusätzlich wurden die benötigten Proxy-Header gesetzt:

```nginx
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header Host $host;
```

Diese werden von Keycloak benötigt, da:

```text
KC_PROXY_HEADERS=xforwarded
```

aktiv ist.

---

#### Backend API

Route geändert von:

```text
/api/*
```

auf:

```text
/api/v1/*
```

Weiterleitung auf:

```text
http://backend:8080
```

---

#### Dashboard

Die Route

```text
/
```

bleibt für das statische Dashboard bestehen.

Der verwendete Dashboard-Pfad wurde mit Stefan abgestimmt.

---

## TLS

TLS wird ausschließlich an nginx terminiert.

Interne Kommunikation erfolgt weiterhin per HTTP:

```text
nginx -> keycloak:8080
nginx -> backend:8080
```

Es wurde kein zusätzliches Keycloak-Zertifikat eingeführt.

---

## Hostname-Wechsel

### Hinweis zur Zertifikats-Erneuerung

Vor dem nächsten `docker compose up` muss `setup_tls.sh` erneut ausgeführt werden, damit das nginx-Zertifikat den neuen Hostnamen `www.lab.local` als CN/SAN enthält.

Falls noch das alte Zertifikat unter `/etc/ssl/backend/backend.crt` verwendet wird, ist es weiterhin auf `backend.lab.local` ausgestellt und passt nicht zum neuen Hostnamen.

```text
backend.lab.local
```

zu:

```text
www.lab.local
```

Angepasste Bereiche:

- nginx SERVER_NAME
- Docker Network Alias
- setup_tls.sh
- Controller API_BASE_URL
- LSTM API_BASE_URL
- LSTM KEYCLOAK_TOKEN_URL
- Dokumentation

---

## Prüfung

Durchgeführt:

```bash
grep -R "backend.lab.local" . \
  --exclude=reverse-proxy-hostname-update.md
```

Ergebnis:

```text
Keine Treffer
```

Zusätzlich:

```bash
docker compose config
```

Ergebnis:

```text
Konfiguration erfolgreich validiert
```

---

## Ergebnis

Die nginx-Konfiguration unterstützt nun Keycloak korrekt hinter einem Reverse Proxy und verwendet durchgängig den neuen öffentlichen Hostnamen:

```text
www.lab.local
```
