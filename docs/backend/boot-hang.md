# Vorfall: Kernel-Hänger nach 5 Minuten

## Was passiert

Der Backend-Pi (RPi 5 16GB) friert nach jedem Kaltstart reproduzierbar nach etwa 5 Minuten und 3 Sekunden komplett ein. Die LEDs bleiben dauerhaft grün, das System reagiert weder auf SSH noch auf Ping, der Bildschirmausgang ist tot. Nur ein harter Neustart bringt das System zurück. Nach dem Reboot beginnt der Zähler von vorn.

Der Hänger ist nicht aktivitätsabhängig. Er tritt auch dann auf, wenn der Pi nach dem Boot nichts weiter tut als auf eine SSH-Session zu warten. Damit fällt jede Erklärung weg, die auf einer bestimmten Benutzeraktion, einem Request oder einem Container-Start während der Sitzung beruht.

## Umgebung

- Hardware: Raspberry Pi 5, 16GB RAM, SD-Karte SanDisk 32GB (Ultra High Speed SDR104)
- OS: Raspberry Pi OS (Bookworm-Basis), Kernel mit brcmfmac für BCM4345/6 WLAN
- Netzwerk: wlan0 als Client am Production-AP (192.168.50.92/24, Kanal 6, 2.4 GHz). eth0 mit IPv6 Link-Local für Direktverbindung zum Entwickler-Laptop, kein IPv4.
- Docker-Stack läuft beim Boot automatisch hoch: nginx, backend (Zig), webserver, postgres, mosquitto, controller, archiver, lstm.

## Was untersucht und ausgeschlossen wurde

Die Diagnostik lief in zwei Schritten. Erst die unmittelbaren Verdächtigen prüfen, dann einen Watcher mitlaufen lassen, der den Zustand bis zum Moment des Einfrierens protokolliert.

### Direkte Messwerte zum Zeitpunkt kurz vor dem Einfrieren

Ein Hintergrundprozess hat alle 15 Sekunden Speicher, Last, Netzverbindungen und Temperatur in `~/watch.log` geschrieben. Der letzte vollständige Snapshot lag 15 Sekunden vor dem Hänger:

| Metrik | Wert |
|--------|------|
| RAM benutzt | 1.1 GiB von 15 GiB |
| Swap benutzt | 0 B von 2 GiB |
| Load Average (1/5/15 min) | 0.15 / 0.21 / 0.10 |
| CPU-Temperatur | 49.9 °C |
| `vcgencmd get_throttled` | `0x0` |
| Aktive TCP-Verbindungen | 1 (SSH über eth0) |
| Docker-Container | 8 laufend, alle stabil |

Damit sind die typischen Hänger-Ursachen ausgeschlossen:

- **Speicherdruck oder OOM**: 14 GiB frei, kein Swap-Einsatz.
- **Thermal-Throttling**: 50 °C ist unter der Drosselgrenze (80 °C) und weit unter der Notabschaltung (85 °C).
- **Unterspannung oder Power-Throttling**: `get_throttled` liefert `0x0`, also weder aktuell noch seit Boot eine Drossel-Flag.
- **Lastspitze**: Load Average sinkt zum Zeitpunkt des Hängers (0.43 → 0.15), das System ist idle.
- **SD-Karten-I/O**: `dmesg` zeigt sauberen Mount, keinerlei I/O-Errors, keine MMC-Resets.

### Zeitliche Charakteristik

Der Hänger fällt mit hoher Reproduzierbarkeit auf 5 min 3 s nach Boot. Boot-Zeitstempel `04:03:29`, letzter Log-Eintrag `04:08:32`. Beim nächsten Boot dasselbe. Eine derart präzise Wiederholung zeigt auf einen Timer-getriebenen Auslöser auf Kernel- oder Systemd-Ebene, nicht auf eine wachsende Ressource oder ein zufälliges Hardware-Glitch.

### WLAN-Powersave als Test eliminiert

Wegen der bekannten Instabilität des brcmfmac-Treibers und der Meldung `bgscan simple: Failed to enable signal strength monitoring` in dmesg lag WLAN-Powersave als erster Verdacht nahe. `sudo iw dev wlan0 set power_save off` hat das Verhalten nicht geändert. Der nächste Hänger trat trotzdem nach 5 min 3 s auf. Powersave allein ist also nicht die Ursache.

## Offene Hypothesen

Drei Erklärungen passen weiterhin zum Befund:

1. **brcmfmac-Treiberabsturz im Kernel.** Der Treiber kann auch ohne aktive Sessions intern Operationen ausführen (Scans, Firmware-Polls, SDIO-Bus-Verkehr). Wenn der Treiber den SDIO-Bus blockiert oder ein Watchdog im Treiber falsch greift, hängt der Kernel komplett, weil der Bus auf demselben SoC liegt. Die SSH-Verbindung über eth0 reißt dann mit, weil der gesamte Scheduler steht, nicht weil die Verbindung selbst betroffen wäre. Powersave-Off allein verhindert das nicht zwingend, da Scans und Firmware-Heartbeats unabhängig davon laufen.
2. **Firmware- oder PMIC-Bug im Pi 5.** Es gibt dokumentierte Pi 5 Hänger, die mit dem RP1-Southbridge oder dem Power-Management-IC zusammenhängen und sich erst nach einigen Minuten Laufzeit zeigen. Eine zu schwache USB-PD-Quelle führt manchmal nicht zur Drossel-Flag, sondern direkt zum Komplett-Stall.
3. **Eine Systemd-Unit oder ein Docker-Healthcheck mit `start_period` von etwa 5 Minuten**, der in einen Fehlerpfad läuft. Möglich, aber weniger wahrscheinlich, da ein Container-Crash normalerweise nicht den Kernel mitnimmt.

Wahrscheinlichste Ursache: Punkt 1. Die Korrelation mit dem brcmfmac-Treiber, der bekanntermaßen instabilen WLAN-Implementierung auf dem Pi und die zeitlich konstante Wiederholung passen am besten zu einem Treiber-internen Timer.

## Was bisher als Mitigation versucht wurde

- WLAN-Powersave abgeschaltet (keine Wirkung).
- Persistentes Journal eingerichtet: Konfiguration `Storage=persistent` in `/etc/systemd/journald.conf`, manuelle Anlage des Machine-ID-Unterverzeichnisses in `/var/log/journal/`. Bis zum letzten Sessionende war die Umstellung nicht vollständig wirksam, das Verzeichnis blieb leer. Ohne persistentes Journal sind die Kernel-Logs des Boots, der den Hänger erzeugt hat, beim nächsten Boot verloren. Das muss zuerst stabil laufen.
- Watcher-Skript läuft via `nohup` aus dem Home-Verzeichnis und überlebt den SSH-Drop. Die Daten daraus sind die Grundlage der bisherigen Analyse.

## Nächste Schritte

In dieser Reihenfolge:

1. **Persistentes Journal verifizieren.** `ls /var/log/journal/$(cat /etc/machine-id)/` muss `.journal`-Dateien enthalten, `journalctl --list-boots` muss nach dem nächsten Reboot mindestens zwei Einträge zeigen.
2. **Kernel-Log nach dem nächsten Hänger lesen.** Direkt nach dem Wieder-Hochfahren: `journalctl -b -1 -k --no-pager | tail -200`. Gesucht wird nach `brcmfmac`, `cfg80211`, `SDIO`, `hung_task`, `BUG:`, `Kernel panic` in den letzten Sekunden vor dem Boot-Ende.
3. **WLAN-Modul testweise deaktivieren.** `sudo rfkill block wifi` direkt nach dem Boot, dann Hänger-Zeit messen. Wenn das System ohne aktives WLAN über 5 Minuten 3 Sekunden hinaus läuft, ist brcmfmac als Ursache bestätigt. Für den Test reicht die eth0-Direktverbindung zum Entwickler-Laptop. Der Pico verliert während des Tests die Verbindung, das ist akzeptabel.
4. **Firmware aktualisieren.** `sudo rpi-eeprom-update -a` und Kernel-Pakete prüfen. Pi 5 Firmware-Bugs sind in mehreren Releases adressiert worden.
5. **Container-Stack testweise herunterfahren.** `sudo systemctl stop iot-backend.service` direkt nach dem Boot. Wenn der Hänger weiterhin auf 5 min 3 s fällt, ist der Docker-Stack als Ursache ausgeschlossen.

## Anhang: Watch-Log-Auszug

Aufzeichnung des Watchers im laufenden Boot, gekürzt auf die letzten Iterationen vor dem Einfrieren. Die letzte Zeile `=== Mon 18 May 04:08:32 CEST 2026 ===` ist mitten in einer Iteration abgebrochen: der Zeitstempel wurde noch geschrieben, der nachfolgende `free -h`-Aufruf nicht mehr. Das markiert den Moment des Kernel-Stalls auf eine 15-Sekunden-Genauigkeit.

```
=== Mon 18 May 04:08:01 CEST 2026 ===
Mem:   15Gi  1.0Gi  13Gi  ...
load average: 0.09, 0.21, 0.09
temp=48.3'C
=== Mon 18 May 04:08:17 CEST 2026 ===
Mem:   15Gi  1.1Gi  13Gi  ...
load average: 0.15, 0.21, 0.10
temp=49.9'C
=== Mon 18 May 04:08:32 CEST 2026 ===
[abrupter Abbruch, kein weiterer Output]
```
