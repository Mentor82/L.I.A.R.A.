# Liara Architektur - Entkoppelte KI-Plattform

> Aktueller, codebasierter Ist-Stand und Uebergabe:
> [`docs/00_index.md`](docs/00_index.md). LIARA ist lokal betriebsfaehig, aber
> aufgrund der dokumentierten Test-, Auth-, Konfigurations- und
> Scheduler-Luecken derzeit nicht production-ready.

## Betrieb (GUI)

Server-Management im Desktop-Stil:

```bash
python server_management_gui.py
```

Dokumentation:

- `docs/SERVER_MANAGEMENT_GUI.md`
- `docs/API_REFERENCE.md`
- `docs/09_reference/SYS_AUDIT.md`
- `docs/WSL_SESSION_RUNTIME.md`

Hinweis:

- Fuer die aktuelle native C/GTK4-Variante und Packaging-Details siehe `frontend/server-manager/README.md`.

## Ziel

Klare Trennung von:

- Frontend
- Backend (Orchestrierung)
- LLM/Inferenz

Ergebnis: skalierbares, modellunabhaengiges System.

## Architektur-Uebersicht (Target)

```text
Frontend
   ↓
liara-api (routers/)
   ↓
liara-orchestrator (submodules)
   ├── liara-tools
   ├── liara-memory (stores/)
   └── liara-inference-gateway
            ↓
        Model Worker (CPU / GPU / NPU)
            ↓
        liara-validator
            ↓
Frontend (Stream / Response)
```

## Komponenten

### liara-api

Kanonischer Eintrittspunkt (`services/api/`).

Endpoints (`services/api/routers/`):

```text
routers/system.py      (/health, /health/backends)
routers/chat.py        (/chat, /chat/stream, /history, /session)
routers/tools.py       (/tools, /tools/{name}/invoke)
routers/governance.py  (/tools/sys/governance/*)
routers/speech.py      (/speech/health, /speech/generate, /speech/stream)
routers/compute.py     (/compute/models, /compute/run, /compute/generate)
routers/operations.py  (/operations/heartbeat, /operations/self-observer, /operations/graph/subgraph)
routers/artifacts.py   (/files/upload, /files/artifact)
```

Regel: Keine Modell- oder Toollogik.

### liara-orchestrator

Zentrale Steuerung (`services/orchestrator/`).

Aufgaben:

- Intent-Erkennung & Input Profiling
- Query-Routing & Evidenz-Sammlung (Librarian)
- Reasoning-Steuerung & Metriken (Belief, Utility, Stability, Decision)
- Tool-Discovery, Execution & Web-Retreival
- Inferenz-Generierung, Response-Validierung & Judge-Traceability

Module:

```text
orchestrator.py        (Coordinator & Facade)
reasoning_control.py   (Phase 1-4 Reasoning-Metriken & Hybrid-Control)
librarian_pipeline.py  (History, Facts, Vector & Graph Context)
tool_discovery.py      (Tool-Selektion, Execution & Web-Discovery)
generation_pipeline.py (LLM-Inferenz, Prompting, Validierung & Judge-Log)
input_profiler.py      (Eingangssituation, Mood & Budget)
router.py / planner.py (Routing & Ablaufplanung)
```

### liara-inference-gateway

LLM-Entkopplungsschicht.

Aufgaben:

- Modellwahl
- Hardware-Routing (CPU / GPU / NPU)
- Provider-Abstraktion
- Streaming-Normalisierung

Struktur:

```text
providers/
  ollama.py
  openvino.py
  openai.py
  vllm.py

router.py
normalizer.py
```

### liara-tools

Aufgaben:

- Tool Registry
- Tool Execution

Beispiele:

```text
time.py
calendar.py
web.py
files.py
```

Tool-Schema:

```python
def run(input: dict) -> dict:
    return {"result": ...}
```

#### Native WSL-Test- und Simulationssessions

LIARA bleibt kanonisch lokal unter `C:\ai\LIARA`. Reale Tests,
Codeexperimente und Julia-Compute laufen bei Bedarf in einer temporaeren
nativen WSL-Session:

```text
lokaler LIARA-Root
-> gefilterter Snapshot
-> read-only source + veraenderbares work in WSL
-> direkte policy-gated /sys-Kommandos
-> Patch + Kandidat + Hashes
-> ai-validator / Governance
```

Der Session-Lifecycle wird durch das Tool `wsl_session` bereitgestellt. Die
Ausfuehrung selbst bleibt beim vorhandenen Tool `sys`; es entsteht kein zweiter
freier Shellpfad und kein automatischer Write-back in den lokalen Projektroot.

Direkter Einstieg:

```powershell
python scripts\wsl_session_cli.py plan
python scripts\wsl_session_cli.py create --label translator-test
python scripts\wsl_session_cli.py exec <session-id> -- julia --version
python scripts\wsl_session_cli.py collect <session-id>
python scripts\wsl_session_cli.py destroy <session-id>
```

Details: `docs/WSL_SESSION_RUNTIME.md`.

### liara-validator

Vertrauensschicht.

Stufen:

- Fast Check
- Semantic Check
- Judge/Critic

Module:

```text
fast_check.py
semantic_check.py
judge.py
```

#### Validator Execution Modes

Der AI-Validator bietet zwei Betriebsmodi:

**Mock-Modus** (für Entwicklung, CI ohne Docker)

```bash
export LIARA_VALIDATOR_EXECUTION_MODE=mock
# oder: LIARA_VALIDATOR_EXECUTION_MODE=stub|dry|simulate
```

- Schnelle Antworten ohne Docker-Worker
- Ideal für lokale Dev/Testing
- Gibt `execution_mode=mock` zurück

**Worker-Modus** (Standard, produktiv)

```bash
export LIARA_VALIDATOR_EXECUTION_MODE=worker
# oder: ungesetzt (default=worker)
```

- Nutzt echten `workers/ai-validator` Docker-Worker
- Vollständige Validierung (Lint, Type, Tests, Security)
- Produktionsmodus

Zusätzliche Optionen:

```bash
# Async Jobs (default=true, für schnelle API-Responses)
export LIARA_VALIDATOR_ASYNC=1

# Pfad zum Worker-Root (default=workers/ai-validator)
export LIARA_VALIDATOR_WORKER_ROOT=/path/to/ai-validator

# Job-Timeout in Sekunden (default=1800)
export LIARA_VALIDATOR_TIMEOUT_SECONDS=1800

# Proposals persistent speichern (default=logs/services/sys_governance_proposals.json)
export LIARA_SYS_GOVERNANCE_STORE_PATH=/path/to/proposals.json

# Governance-Events als JSONL (append-only, default=logs/services/sys_governance_events.jsonl)
export LIARA_SYS_GOVERNANCE_EVENTS_PATH=/path/to/events.jsonl
```

**Empfohlene Konfigurationen:**

Lokal/Entwicklung:
```bash
LIARA_VALIDATOR_EXECUTION_MODE=mock
LIARA_VALIDATOR_ASYNC=1
```

Staging/Testing:
```bash
LIARA_VALIDATOR_EXECUTION_MODE=worker
LIARA_VALIDATOR_ASYNC=1
LIARA_VALIDATOR_TIMEOUT_SECONDS=300
```

Produktion:
```bash
LIARA_VALIDATOR_EXECUTION_MODE=worker
LIARA_VALIDATOR_ASYNC=1
LIARA_VALIDATOR_TIMEOUT_SECONDS=1800
LIARA_SYS_GOVERNANCE_ENFORCE=1
```

### liara-memory

Kontext und Wissen.

Technologien:

- Postgres
- Redis
- Qdrant/Chroma
- Neo4j

Module:

```text
history.py
facts.py
retrieval.py
embedding.py
```

## Worker-System

### llm-worker

```text
worker.py

models/
  qwen.py
  llama.py
```

API:

```json
POST /infer

{
  "model": "qwen-small",
  "input": "...",
  "stream": true
}
```

## CLI statt Web-UI

Statt einer Web-Oberflaeche kann LIARA jetzt direkt im Terminal genutzt werden.

Beispiele:

```bash
python -m services.cli.main chat "Wie spaet ist es?"
python -m services.cli.main stream "Erklaer mir den aktuellen Status"
python -m services.cli.main repl

# Maschinenlesbar fuer Codex, Copilot und CI (globale Optionen vor dem Subcommand)
python -m services.cli.main --output json health
python -m services.cli.main --output json chat "Pruefe LIARA" --session-id codex-test
python -m services.cli.main --output json --fail-on-validation stream "Pruefe LIARA"
```

Optionen:

- `--base-url` (default: `http://127.0.0.1:8010`)
- `--timeout` (default: `90` Sekunden, per `LIARA_HTTP_TIMEOUT` anpassbar)
- `--output human|json` (default: `human`; alternativ `LIARA_CLI_OUTPUT`)
- `--no-color` deaktiviert Rich-/ANSI-Farben im Human-Modus
- `--fail-on-validation` liefert Exitcode 4 bei `warn`/`revise` und 5 bei `block`
- REPL-Befehle: `/history`, `/session`, `/mode chat|stream`, `/sys <command> [args...]`, `/quit`

Weitere Exitcodes: `0` Erfolg, `2` CLI-Nutzungsfehler, `3` HTTP-/Transportfehler,
`130` Benutzerabbruch. Im JSON-Modus steht genau ein Dokument auf stdout;
Fehler werden als JSON auf stderr ausgegeben. Nach erneuter Paketinstallation
steht zusaetzlich der Einstieg `liara-cli` zur Verfuegung.

Audit und Analyse:

- `/sys`-Audit-Log und Traceability: `docs/09_reference/SYS_AUDIT.md`
- Audit-TUI: `python -m services.tui.sys_audit_tui --scope sys --limit 20`
- Interaktive Audit-TUI: `python -m services.tui.sys_audit_tui --scope sys --textual`

## Live Stream Demo

Fuer einen echten Live-Chat mit sichtbaren Fortschritts-Events und Session-Erinnerung:

1. API starten:

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe -m uvicorn services.api.app:app --host 127.0.0.1 --port 8010
```

1. Demo-Skript ausfuehren:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\live_chat_memory_demo.ps1
```

Oder Demo + Live-Pytest in einem Schritt:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_live_chat_memory_checks.ps1
```

Unter WSL/Linux gibt es dieselben Flows auch als Shell-Skripte:

```bash
bash ./scripts/live_chat_memory_demo.sh
bash ./scripts/run_live_chat_memory_checks.sh
```

Das Skript:

- sendet zwei Turns in derselben Session
- protokolliert SSE-Events wie `progress`, `heartbeat`, `chunk`, `final`, `done`
- prueft, ob im zweiten Turn ein `memory_effect_detected`-Signal auftritt
- schreibt die Auswertung nach `logs/demos/`

Typische Erfolgsmerkmale im Log:

- `orchestration_complete -> ... | mode=MEMORY`
- `memory_effect_detected -> Earlier session context influenced this answer`
- `[SUCCESS] Memory effect observed in second turn.`

## Python Dependency-Profile

LIARA nutzt jetzt profilbasierte Requirements, damit Sandbox-Setups klein bleiben
und DB/Inference-Pakete nur bei Bedarf installiert werden.

Schnellstart:

```bash
# Minimal (Sandbox/Core)
pip install -r requirements-sandbox.txt

# Core + Datenbanken/Vector/Graph
pip install -r requirements-core.txt -r requirements-db.txt

# Vollprofil (abwaertskompatibel)
pip install -r requirements.txt

# Development/Test
pip install -r requirements-dev.txt
```

Alternativ mit `pyproject.toml`-Extras:

```bash
# Core Runtime
pip install .

# DB-Backends
pip install .[db]

# Optionale AI/Utility Extras
pip install .[optional]

# Vollprofil
pip install .[all]

# Dev/Test
pip install .[dev]
```

## Kommunikation

- API -> Orchestrator: direkte Calls
- Orchestrator -> Tools: synchron
- Orchestrator -> Inference: asynchron empfohlen (Queue)

Optionen:

- Redis
- NATS
- RabbitMQ

## Datenfluss

Einfach:

```text
Input -> Tool -> Validator -> Output
```

Komplex:

```text
Input
 -> Orchestrator
 -> Memory
 -> Tool/LLM
 -> Validator
 -> Output
```

## Projektstruktur (Target)

```text
liara/
├── services/
│   ├── api/
│   ├── orchestrator/
│   ├── inference/
│   ├── tools/
│   ├── validator/
│   ├── memory/
│
├── workers/
│   ├── llm-worker/
│   ├── embedding-worker/
│   ├── vision-worker/
│
├── shared/
│   ├── schemas/
│   ├── contracts/
│   ├── utils/
│   ├── config/
│
├── frontend/
│   ├── qt-ui/
│   ├── web-ui/
│
├── infra/
│   ├── docker/
│   ├── compose/
```

## Rollenmodell

| Rolle | Aufgabe |
| --- | --- |
| Scout (NPU) | Klassifikation |
| Router (CPU) | Entscheidung |
| Worker (GPU) | Generierung |
| Judge | Validierung |
| Archivist | Speicherung |

## Routing-Logik (Beispiel)

```python
if tool_need and complexity == "low":
    use_tool()

elif complexity == "low":
    use_small_model()

else:
    use_gpu_model()
```

## Entwicklungsphasen

### Phase 1

- API
- Orchestrator
- 1 Modell
- 1 Tool
- Basic Validator

### Phase 2

- Tool Registry
- Memory
- Streaming

### Phase 3

- Multi-Worker
- GPU/NPU Routing
- Advanced Validator

## Design-Prinzipien

1. Modelle sind austauschbar.
2. Tools sind deterministisch.
3. Orchestrator entscheidet alles.
4. Validator ist Pflicht.
5. Kommunikation laeuft ueber Schemas.

## Leitprinzip

Frontend zeigt Zustaende. Backend steuert Ablaeufe. LLM liefert nur Inferenz.

## Kurzform

NPU erkennt -> CPU entscheidet -> GPU denkt -> Validator prueft.

## Aktueller Repo-Stand (Ist)

Der Code in diesem Repo ist vollstaendig auf die kanonische `services/`-Struktur
migriert:

- zentrale Service-Contracts unter `services/contracts`
- Orchestrierung unter `services/orchestrator`
- Inference-Gateway und Tool-Koordination unter `services/inference` und `services/tools`
- Memory-Layer unter `services/memory`

Die Entkopplung in eigenstaendige Service-/Worker-Pakete bleibt das
architektonische Ziel fuer die naechsten Phasen.

## LIARA Compose Stack

Fuer lokale Store-Anbindung laeuft die LIARA-Infrastruktur als eigener
Compose-Stack mit separaten Containern, Volumes und Host-Ports. Damit
kollidiert der Stack nicht mit anderen Projekten auf derselben Maschine.
API und Memory werden im lokalen Entwicklungsbetrieb als Host-Services ueber
`scripts\service_guard.py` gestartet, nicht ueber Docker Compose.

Start:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts\service_guard.py start --service memory --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service embedding --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service api --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service bridge --repo-root C:\ai\LIARA
```

Zugangspunkte:

```env
# POSTGRESQL
POSTGRES_USER=liara
POSTGRES_PASSWORD=liara2026
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=liara_memory
POSTGRES_URL=postgresql://liara:liara2026@127.0.0.1:5433/liara_memory

# REDIS
REDIS_HOST=127.0.0.1
REDIS_PORT=6380
REDIS_PASSWORD=liara2026
REDIS_DB=0
REDIS_URL=redis://:liara2026@127.0.0.1:6380/0

# QDRANT
QDRANT_HOST=127.0.0.1
QDRANT_PORT=6335
QDRANT_GRPC_PORT=6336
QDRANT_URL=http://127.0.0.1:6335
QDRANT_COLLECTION=liara_retrieval

# CHROMA
CHROMA_HOST=127.0.0.1
CHROMA_PORT=8001

# NEO4J
NEO4J_HOST=127.0.0.1
NEO4J_PORT=7688
NEO4J_HTTP_PORT=7475
NEO4J_USER=neo4j
NEO4J_PASSWORD=liara2026

# OLLAMA (lokaler Dienst, nicht Teil des Compose-Stacks)
OLLAMA_HOST=127.0.0.1
OLLAMA_PORT=11434
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Namenskonvention im Stack:

- Services/Container: `liara-*`
- Persistente Volumes: `liara_*`
- Primäre Postgres-Datenbank: `liara_memory`
- Ollama läuft lokal auf dem Host und ist bewusst nicht im LIARA-Compose enthalten
