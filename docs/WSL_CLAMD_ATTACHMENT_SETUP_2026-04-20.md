# WSL Clamd Attachment Setup (2026-04-20)

## Ziel

LIARA scannt Dateianhaenge ueber `clamd` in der kanonischen Debian-WSL-Arbeitsumgebung.
Die API bleibt lokal auf Windows, arbeitet fuer den Scan aber mit kanonischen WSL-Sandbox-Pfaden.

## Umgesetzt

- Kanonische Sandbox auf WSL-Basis aktiviert.
- Attachment-Scanning auf `LIARA_ATTACHMENT_SCAN_MODE=wsl-clamd` vorbereitet.
- Fallback auf den eingebauten EICAR-Scanner bleibt aktiv, solange `clamd` nicht verfuegbar ist.
- Debian-WSL mit folgenden Paketen ausgestattet:
  - `clamav-daemon`
  - `clamav-freshclam`
  - `clamdscan`
- `systemd`-Dienste aktiviert:
  - `clamav-freshclam`
  - `clamav-daemon`

## Projektkonfiguration

Aktuelle LIARA-Umgebung in [.env](c:/ai/LIARA/.env):

```dotenv
LIARA_SANDBOX_MODE=wsl
LIARA_WSL_DISTRO=Debian
LIARA_WSL_SANDBOX_ROOT=/home/liara/workspace
LIARA_ATTACHMENT_SCAN_MODE=wsl-clamd
LIARA_ATTACHMENT_SCAN_COMMAND=clamdscan --no-summary --fdpass -- {path}
LIARA_ATTACHMENT_SCAN_TIMEOUT_SECONDS=30
LIARA_ATTACHMENT_SCAN_ALLOW_FALLBACK=true
```

## Debian-WSL Status

Stand der Installation:

- `clamav-freshclam`: installiert, aktiviert, laufend
- `clamav-daemon`: installiert und aktiviert, startet aber aktuell noch nicht
- `daily.cvd`: vorhanden
- `bytecode.cvd`: vorhanden
- `main.cvd`: fehlt aktuell noch

## Aktueller Blocker

`clamav-daemon` ist nicht durch LIARA blockiert, sondern durch fehlende ClamAV-Signaturen.
Der ClamAV-CDN-Download fuer `main.cvd` wurde rate-limited.

Beobachteter Zustand in Debian:

- `freshclam` meldet CDN-Cooldown
- `clamd` bleibt inaktiv wegen:

```text
ConditionPathExistsGlob=/var/lib/clamav/main.{c[vl]d,inc} was not met
```

Gemeldeter Cooldown-Endpunkt:

```text
2026-04-20 22:10:20
```

## Auswirkungen auf LIARA

Bis `main.cvd` verfuegbar ist:

- LIARA ist bereits auf WSL-`clamd` konfiguriert.
- Reale `clamd`-Scans sind noch nicht verfuegbar.
- Der eingebaute EICAR-Fallback bleibt aktiv und verhindert einen kompletten Ausfall des Upload-/Attachment-Pfads.

Sobald `main.cvd` vorhanden ist:

- `clamav-daemon` sollte sauber starten.
- `LIARA_ATTACHMENT_SCAN_MODE=wsl-clamd` scannt dann ueber den echten WSL-Daemon.

## Verifikation nach Cooldown

Empfohlene Pruefschritte in Debian WSL:

```sh
systemctl status clamav-freshclam --no-pager -l
systemctl restart clamav-daemon
systemctl status clamav-daemon --no-pager -l
ls -lah /var/lib/clamav
```

EICAR-Test gegen den echten Daemon:

```sh
tmp=$(mktemp /tmp/eicar.XXXXXX.txt)
printf 'X5O!P%%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > "$tmp"
clamdscan --no-summary --fdpass -- "$tmp"
rc=$?
rm -f "$tmp"
exit $rc
```

Erwartung:

- Exit-Code `1`
- Malware-Fund ueber `clamd`

## Relevante Code-Stellen

- [services/shared/attachment_security.py](c:/ai/LIARA/services/shared/attachment_security.py)
- [services/shared/sandboxing.py](c:/ai/LIARA/services/shared/sandboxing.py)
- [services/api/app.py](c:/ai/LIARA/services/api/app.py)
- [docs/API_REFERENCE.md](c:/ai/LIARA/docs/API_REFERENCE.md)

## Naechster Schritt

Nach Ende des CDN-Cooldowns den echten `clamd`-Pfad live pruefen und danach einen End-to-End-Uploadtest gegen API plus Bridge mit aktivem Daemon durchziehen.