# LIARA AI-Validator Umgebungs-Setup

> Architekturhinweis (Stand 2026-07-14): Der ai-validator und sein Jobcontract
> sind nicht an Docker gebunden. Dieses Dokument beschreibt den aktuell
> implementierten `docker_compose`-Adapter. Weitere VM-, Remote-Worker- oder
> Container-Adapter implementieren `ValidatorExecutionBackend`.

## Übersicht

Der LIARA AI-Validator ist ein Docker-basierter Validierungsservice für Python, JavaScript, Bash, HTML, PHP, C/C++ und weitere Sprachen. Es gibt zwei Integrationsmöglichkeiten:

1. **Haupt-Compose-Stack** (Empfohlen) — Validator läuft dauerhaft als Service
2. **Worker-Compose** (Alternativ) — Validator startet on-demand für jeden Job

## 1. Haupt-Compose-Integration (Empfohlen)

Der Validator ist in `docker-compose.yml` definiert und läuft als Service im `liara_network`.

### Starten

```bash
# Nur Validator starten
docker compose up -d liara-validator

# Oder mit ganzen Stack (abhängig vom profil)
docker compose up -d
docker compose --profile app up -d
```

### Status prüfen

```bash
docker compose ps liara-validator
docker compose logs liara-validator

# Health-Check
docker exec liara-validator python3 -c "import sys; sys.exit(0)" && echo "✓ Validator healthy"
```

## 2. Worker-Compose (Alternative für Tests/CI)

Alternativ kann der Validator auch per On-Demand-Container gestartet werden:

```bash
cd workers/ai-validator

# Einmalig: Validator-Worker bauen
docker compose build

# Validator auf Workspace laufen lassen (mit docker compose run)
docker compose run --rm -e WORKSPACE_PATH=/path/to/code ai-validator validate
```

## 3. Umgebungs-Variablen

### Python-Services (API/Memory)

```bash
# Execution mode
LIARA_VALIDATOR_EXECUTION_MODE=worker|mock|stub
  # Default: worker (echte Validierung ueber das konfigurierte Backend)
  # mock: Schnelle Antworten ohne Docker (Entwicklung)

# Worker-Backend
LIARA_VALIDATOR_BACKEND=docker_compose
  # Aktuell gebundelt: docker_compose
  # Erweiterbar ueber services/memory/validator_execution.py

# Async-Betrieb
LIARA_VALIDATOR_ASYNC=1
  # Default: 1 (true) — Jobs laufen im Hintergrund
  # 0: Synchrone Ausführung (blockiert API)

# Validator Worker-Root
LIARA_VALIDATOR_WORKER_ROOT=./workers/ai-validator
  # Default: ./workers/ai-validator
  # Pfad zur Docker-Compose des Validators

# Job Timeout
LIARA_VALIDATOR_TIMEOUT_SECONDS=1800
  # Default: 1800 (30 Minuten)
  # Für lange Analysen erhöhen
```

### Docker Compose (Validator Service)

```yaml
environment:
  WORKSPACE: /workspace           # Zu validierender Code (read-only)
  REPORT_DIR: /reports            # Validierungs-Reports (persistent)
  STRICT_MODE: "false"            # Strikte Regeln? (true/false)
  PYTHONUNBUFFERED: 1             # Logs direkt ausgeben
  PYTHONDONTWRITEBYTECODE: 1      # Keine .pyc-Dateien
  NODE_ENV: production            # Node.js Umgebung
```

## 4. Vollständige Konfiguration

### Development (Lokal)

```bash
# .env oder environment export

# Python-Service
export LIARA_VALIDATOR_EXECUTION_MODE=mock
export LIARA_VALIDATOR_ASYNC=1

# Docker-Compose: Validator Service
# (wird automatisch mit docker compose up gestartet)
```

### Staging/Testing

```bash
# Mit echtem Worker, aber mit niedrigerem Timeout

export LIARA_VALIDATOR_EXECUTION_MODE=worker
export LIARA_VALIDATOR_ASYNC=1
export LIARA_VALIDATOR_TIMEOUT_SECONDS=300

# Oder im docker-compose.yml:
environment:
  STRICT_MODE: "true"  # Strikte Validierung
```

### Production

```bash
export LIARA_VALIDATOR_EXECUTION_MODE=worker
export LIARA_VALIDATOR_ASYNC=1
export LIARA_VALIDATOR_TIMEOUT_SECONDS=1800
export LIARA_VALIDATOR_WORKER_ROOT=/var/lib/liara/ai-validator

# Mit Governance
export LIARA_SYS_GOVERNANCE_ENFORCE=1
export LIARA_SYS_GOVERNANCE_STORE_PATH=/var/lib/liara/governance/proposals.json
export LIARA_SYS_GOVERNANCE_EVENTS_PATH=/var/lib/liara/governance/events.jsonl
```

## 5. Execution-Flow

### Worker-Modus (Empfohlen)

```
Client (API)
  ↓
/validator/submit → InMemoryStore or BackedStore
  ↓
JobState: "queued"
  ↓ [async via asyncio.to_thread()]
_execute_validator_job()
  ↓
ValidatorExecutionBackend.execute(request)
  -> [aktueller Adapter: docker_compose]
docker compose run --rm ai-validator <command>
  ↓
JobState: "running" → "completed|failed"
  ↓
Client GET /validator/status → Job-Result
```

### Mock-Modus

```
Client (API)
  ↓
/validator/submit → InMemoryStore or BackedStore
  ↓
JobState: "queued" → immediately "completed"
  ↓
Summary: execution_mode=mock (no Docker needed)
  ↓
Client GET /validator/status → Mock-Result
```

## 6. Reports und Artefakte

Reports werden in das persistent Volume `liara_validator_reports` geschrieben:

```bash
# Volume anschauen
docker volume inspect liara_validator_reports

# Reports aus dem Container holen
docker compose exec liara-validator ls -la /reports

# Oder von Host aus (Falls gemounted)
cat liara_validator_reports/*/reports/*
```

## 7. Troubleshooting

### Validator startet nicht

```bash
# Logs prüfen
docker compose logs liara-validator

# Build neu starten
docker compose down liara-validator
docker compose build --no-cache liara-validator
docker compose up -d liara-validator
```

### Docker nicht gefunden (Worker-Modus)

```bash
# docker CLI installieren und in PATH
which docker
docker --version

# Oder auf Worker-Compose switchen
export LIARA_VALIDATOR_EXECUTION_MODE=mock
```

### Timeout bei langen Jobs

```bash
# Timeout erhöhen
export LIARA_VALIDATOR_TIMEOUT_SECONDS=3600

# Oder im docker-compose.yml anpassen
# Aber auch Ressource-Limits beachten:
# limits:
#   cpus: '4'
#   memory: 4G
```

### Validator ist "stuck" in running

```bash
# Process killen
docker compose down liara-validator

# Oder nur den Container killen
docker kill liara-validator

# Neu starten
docker compose up -d liara-validator
```

## 8. Integration mit API/Memory Service

Die Validator-Jobs sind über die Memory-Service API verfügbar:

### Jobs erstellen

```bash
curl -X POST http://localhost:8020/validator/submit \
  -H "Content-Type: application/json" \
  -d '{
    "workspace": ".",
    "scope": "validate",
    "checks": ["syntax", "lint", "type", "test"],
    "strict_mode": false
  }'
```

### Status prüfen

```bash
curl -X POST http://localhost:8020/validator/status \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "<returned-job-id>"
  }'
```

### Ergebnisse abrufen

```bash
curl -X POST http://localhost:8020/validator/result \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "<returned-job-id>"
  }'
```

## 9. Siehe auch

- [README.md — Validator Execution Modes](README.md#validator-execution-modes)
- `workers/ai-validator/README.md` — Worker-spezifische Dokumentation
- `tests/unit/test_memory_service_app.py` — Integration Tests
- `tests/unit/test_api_app.py` — API Tests
