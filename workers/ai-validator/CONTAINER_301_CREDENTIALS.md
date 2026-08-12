# Container 301 (ai-validator) - Zugangsdaten

## Container Informationen
- **VMID**: 301
- **Name**: ai-validator
- **IP**: 192.168.178.150
- **Gateway**: 192.168.178.1
- **OS**: Ubuntu 24.04-2 LTS
- **Port**: 22 (SSH)

---

## Benutzer & Passwörter

### Root-Benutzer
```
Username: root
Password: lab-root-2025
```

### SSH-Benutzer (Recommended)
```
Username: ai-validator
Password: ai-validator-2025
Sudoers: Ja (NOPASSWD)
```

---

## SSH Verbindung

### Direkt vom Host
```bash
# Als ai-validator Benutzer
ssh ai-validator@192.168.178.150

# Oder als root
ssh root@192.168.178.150
```

### Über astraeus (Proxmox)
```bash
ssh root@192.168.178.92
pct exec 301 -- bash
```

---

## SCP/SFTP Upload

```bash
# Dateien hochladen (lokal)
scp -r ai-validator ai-validator@192.168.178.150:/opt/

# Oder mit root
scp -r ai-validator root@192.168.178.150:/opt/

# SFTP interaktiv
sftp ai-validator@192.168.178.150
# cd /opt
# put -r ai-validator
# quit
```

---

## Container Setup Status
- ✅ SSH aktiviert & konfiguriert
- ✅ Root-Passwort gesetzt
- ✅ SSH-Benutzer `ai-validator` angelegt
- ✅ Sudoers konfiguriert (NOPASSWD)
- ✅ AI-Validator Dateien hochgeladen zu `/opt/ai-validator/`
- ✅ AI-Policies im `/opt/ai-validator/ai/` Verzeichnis
- ✅ Dependencies installiert:
  - Python 3.12 + pylint, bandit
  - Node.js 18.19.1 + npm 9.2.0
  - Bash + shellcheck
  - jq für JSON-Processing

---

## Verfügbare Validatoren

### run-native-validators.sh (EMPFOHLEN)
Nutzt native Tools ohne Docker:
```bash
ssh root@192.168.178.92 "pct exec 301 -- /opt/ai-validator/run-native-validators.sh [COMMAND] [MODE]"
```

**Kommandos:**
- `quick` - Python + Bash Validierung
- `validate` - Vollständig (Python + Bash + Security + Config)
- `python` - Nur Python (pylint)
- `bash` - Nur Bash (shellcheck)
- `security` - Security Scan (bandit)
- `config` - Config-Dateien Validierung

**Beispiele:**
```bash
pct exec 301 -- /opt/ai-validator/run-native-validators.sh quick serial
pct exec 301 -- /opt/ai-validator/run-native-validators.sh validate serial
pct exec 301 -- /opt/ai-validator/run-native-validators.sh security serial
```

## Monitoring Setup

### Metrics-Server (Dashboard)

**Status:** ✅ Flask installiert & Server läuft auf Port 5000

**Starten:**
```bash
ssh root@192.168.178.92
pct exec 301 -- python3 /opt/ai-validator/metrics-server.py
```

**Zugriff via SSH Port-Forward (lokal):**
```bash
ssh -L 5000:192.168.178.150:5000 root@192.168.178.92
# Dann öffnen: http://localhost:5000
```

**API Endpoints:**
- `GET /` - Dashboard HTML
- `GET /api/metrics` - JSON Metriken
- `GET /api/summary` - Summary der letzten Reports
- `GET /api/workspaces` - Alle Workspaces
- `GET /metrics.json` - Prometheus-Format

### Cron-Jobs (Automatische Validierung)

**Täglich um 2 Uhr validieren:**
```bash
ssh root@192.168.178.92
pct exec 301 -- crontab -e

# Hinzufügen:
0 2 * * * /opt/ai-validator/run-native-validators.sh validate serial >> /var/log/ai-validator-cron.log 2>&1
```

### Reports aggregieren

**Nach jeder Validierung Reports zusammenfassen:**
```bash
ssh root@192.168.178.92
pct exec 301 -- /opt/ai-validator/aggregate-reports.sh
```

**Ausgabe:**
- `/tmp/ai-validator-reports-{TIMESTAMP}/SUMMARY.md`
- `/tmp/ai-validator-reports-{TIMESTAMP}/metrics.json`
- `/tmp/ai-validator-reports-{TIMESTAMP}/index.html`

## Deploy Vollständig ✅

**Heute deployment:**
- ✅ Container 301 (Ubuntu 24.04-2)
- ✅ SSH/SFTP konfiguriert
- ✅ AI-Validator Scripts
- ✅ Native Validators (Python, Node, Bash)
- ✅ Metrics-Server (Flask) laufen
- ✅ Monitoring Setup dokumentiert

---

**Erstellt**: 2025-12-24
**Status**: Production Ready
