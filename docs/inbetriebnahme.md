# Meine Änderungen — Backend-Server (16GB Pi)

## Was ich gemacht habe und warum

Ich habe den Docker-Stack aufgebaut und in Betrieb genommen. Das umfasst die Konfiguration von mosquitto (TLS, Passwörter, ACL), PostgreSQL (Datenbank, Nutzer, Schema) und dem Controller (MQTT → DB). Das Zig-Backend und nginx waren bereits vorhanden.

### TLS für mosquitto

mosquitto braucht ein Broker-Zertifikat, das von einer eigenen CA signiert ist, damit der Pico die Verbindung verifizieren kann. Ich habe `setup_tls.sh` geschrieben, das die CA und das Broker-Zertifikat generiert. Das CA-Zertifikat (`ca.crt`) muss manuell auf den Pico kopiert werden, weil es kein automatisches Certificate Provisioning gibt.

Das Broker-Zertifikat enthält `backend-server.lab.local` als SAN — das ist der Hostname, den der Pico beim TLS-Handshake als `server_hostname` mitschickt, damit der Broker weiß, welches Zertifikat er präsentieren soll.

### Passwörter und ACL

`allow_anonymous false` zwingt alle Clients zur Authentifizierung. Die Passwort-Hashes werden über `set_passwords.sh` gesetzt — direkt im laufenden Container, damit sie nicht im Klartext im Repo landen. Die ACL-Datei beschränkt jeden Sensor auf sein eigenes Topic.

### mosquitto-Logs als Diagnosewerkzeug

Beim Debuggen der Pico-Verbindung waren die Broker-Logs das einzig verlässliche Diagnosewerkzeug, weil der Pico selbst keine aussagekräftigen Fehlermeldungen liefert. Zwei konkrete Meldungen haben uns auf Bugs im Pico-Code hingewiesen:

- **„CONNECT with incorrect protocol string length (0)"** — der Broker konnte das MQTT-CONNECT-Paket nicht parsen, weil ein Off-by-one-Fehler in der umqtt-Bibliothek ein zusätzliches Null-Byte vor den Variable Header schob. Dadurch las mosquitto die ersten zwei Bytes des Protocol Name Length Fields als `0x00 0x00` statt `0x00 0x04`.

- **„bad AUTH method"** — klingt nach einem Credential-Problem, war aber keins. Ein weiterer Indexfehler in derselben Bibliothek hat den Protocol-Level-Byte (der `0x04` für MQTT 3.1.1 sein muss) mit den Connect-Flags überschrieben. mosquitto hat das Protokoll dadurch nicht erkannt.

Beide Bugs lagen nicht in meinem Code, sondern in der umqtt-Bibliothek auf dem Pico (→ API-pico).
