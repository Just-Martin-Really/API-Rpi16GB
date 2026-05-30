# Auflösung: Kernel-Hänger nach 5 Minuten

Diese Datei dokumentiert die Auflösung des in `boot-hang.md` beschriebenen Vorfalls. Sie sollte zusammen mit jenem Dokument gelesen werden, das die Symptomatik und die Diagnosekette enthält.

## Ursache

Das Betriebssystem auf dem 16GB-Pi war seit der Erstinstallation nicht aktualisiert worden. Beim ersten Lauf von `apt full-upgrade` am 2026-05-22 standen 188 Pakete (etwa 550 MB) zur Aktualisierung an. Drei Komponenten sind für den Hänger relevant:

- **Kernel:** Sprung von 6.12.75 auf 6.18.29, also sechs Minor-Versionen.
- **WLAN-Firmware** (`firmware-brcm80211`): Aktualisierung auf 1:20250410-2+rpt1.
- **EEPROM-Bootloader:** Stand vom 13. Juni 2025, aktualisiert auf den 8. Dezember 2025.

Damit lief der brcmfmac-Treiber, dessen Stack auf dem Pi 5 in mehreren Patch-Runden des letzten Jahres adressiert wurde, in einer Version, die ein internes Timer-Verhalten nicht stabil zurücksetzte. Die zeitlich präzise Reproduktion auf 5 min 3 s nach jedem Boot passt zu genau dieser Klasse von Treiber-Bugs: ein periodischer Vorgang im Treiber blockiert nach einem festen Intervall den SDIO-Bus, der WLAN und Teile der Pi-5-Southbridge teilt. Dadurch hängt der gesamte Kernel-Scheduler, auch SSH über `eth0` reißt mit ab.

## Fix

Auf dem 16GB-Pi am 2026-05-22 ausgeführt:

```
sudo systemctl stop packagekit
sudo apt update && sudo apt full-upgrade -y
sudo apt autoremove -y
sudo rpi-eeprom-update -a
sudo reboot
```

`packagekit` muss vor `apt update` gestoppt werden, sonst hält ein laufender `packagekitd` die Paketdatenbank-Sperre. Nach dem Neustart läuft `6.18.29+rpt-rpi-2712` mit aktueller Firmware und aktualisiertem Bootloader.

## Nachbereitung

Während der Diagnose wurde der `brcmfmac`-Treiber über `/etc/modprobe.d/blacklist-brcm.conf` (`blacklist brcmfmac`) deaktiviert, um den Hänger reproduzierbar zu umgehen. Nach dem oben beschriebenen Upgrade muss diese Datei wieder entfernt werden:

```
sudo rm /etc/modprobe.d/blacklist-brcm.conf
sudo reboot
```

Bleibt die Blacklist nach dem Upgrade bestehen, lädt der Treiber nicht, und `wlan0` taucht gar nicht erst als Gerät auf, weder in `nmcli device status` noch in `rfkill list`. Der 16GB-Pi bleibt damit auf `eth0` reduziert und ist nicht mehr unter `192.168.50.92` im Production-WLAN erreichbar. Pico und andere Sensorknoten erhalten beim MQTT-Connect dann `ECONNABORTED`, obwohl der Broker-Container selbst läuft. Die Diagnose ist in dem Fall irreführend, weil das Symptom auf einen Broker- oder TLS-Fehler hindeutet, die eigentliche Ursache aber das fehlende WLAN-Interface auf dem Broker-Host ist.

Nach dem Reboot mit entferntem Blacklist-Eintrag erscheint `wlan0` wieder. Falls das Verbindungsprofil nicht automatisch hochkommt, einmal manuell aktivieren:

```
sudo nmcli connection up Production
```

`ip -4 addr show wlan0` muss anschließend wieder eine Adresse aus `192.168.50.0/24` zeigen.

## Verifikation

Vor dem Upgrade trat der Hänger nach jedem Kaltstart auf 5 min 3 s zuverlässig auf. Nach dem Upgrade läuft das System über diesen Zeitpunkt hinaus stabil. Reproduktion war seither nicht möglich.

Das persistente Journal wurde im Rahmen dieses Vorgangs eingerichtet (`/var/log/journal/<machine-id>/`). `journalctl --list-boots` zeigt deshalb nur den aktuellen Boot, weil alle vorherigen Journals in tmpfs lagen und beim Reboot verloren gingen. Ein erneuter Hänger wäre über `journalctl -b -1 -k` direkt analysierbar.

## Lehre

Das eigentliche Problem war nicht ein einzelner Bug, sondern dass das System nie aktualisiert wurde. Ein frisch installiertes Raspberry Pi OS auf Bookworm-Basis trägt einen Brocken Treiber- und Firmware-Bugs, die in den anschließenden Updates ausgeräumt werden. Solange das System diese Updates nicht zieht, bleiben die Bugs vorhanden, mit dem hier beobachteten Verhalten als Konsequenz.

Wartungs-Upgrades sind ab sofort Routine, nicht Reaktion. Die analoge Aktualisierung des 2GB-AP wurde am selben Tag durchgeführt, damit die WLAN-Strecke nicht in einen vergleichbaren Zustand läuft.
