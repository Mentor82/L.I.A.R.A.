# Runtime Reference

Stand: 2026-07-14

## Ports

| Komponente | Port |
| --- | --- |
| liara-api | `8010` |
| OpenAI Bridge | typischerweise `8011` laut Skriptnamen/Bridge-Kontext |
| liara-memory | `8020` |
| Embedding extern | typischerweise `8030` |
| OpenVINO NPU Helper | `8040` Default Base URL |
| LIARA Heartbeat Instance | `8050` |
| Frontend Web UI | `3001` |
| Frontend Web UI, Node-26-Testpfad | `3002` |
| llama.cpp Server | `8000` Default Base URL |
| Ollama Host | `11434` |
| LiNeP Embedding TCP | `8767` |
| LiNeP Heartbeat UDP | `8768` |
| Postgres | Host `5433`, Container `5432` |
| Redis | Host `6380`, Container `6379` |
| Qdrant HTTP | Host `6335`, Container `6333` |
| Qdrant gRPC | Host `6336`, Container `6334` |
| Neo4j Bolt | Host `7688`, Container `7687` |
| Neo4j HTTP | Host `7475`, Container `7474` |
| Chroma | Host `8001`, Container `8000` |

## Startbefehle

Infrastruktur:

```powershell
docker compose up -d liara-postgres liara-redis liara-qdrant liara-neo4j liara-chroma
```

Standard-Startpfad fuer lokale Entwicklung:

```powershell
docker compose up -d liara-postgres liara-redis liara-qdrant liara-neo4j liara-chroma liara-validator
.\.venv\Scripts\python.exe scripts\service_guard.py start --service memory --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service embedding --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service api --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service bridge --repo-root C:\ai\LIARA
```

API und Memory sollen lokal nicht ueber Docker Compose gestartet werden. Der
kanonische Pfad ist: Datenbanken/Validator in Docker, API/Memory/Embedding/Bridge
als Host-Services ueber `scripts\service_guard.py`.

Der alte Docker-Pfad fuer API/Memory ist im Compose-File nur noch unter dem
Profil `legacy-docker-app` erreichbar und sollte nur fuer gezielte
Kompatibilitaets-/Container-Tests verwendet werden.

Falls der Docker-Build bei `requirements-core.txt`, `requirements-db.txt` oder
`requirements-optional.txt` abbricht, muessen die API-/Memory-Dockerfiles alle
Requirements-Profile vor dem Installationsschritt kopieren:

```dockerfile
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt "uvicorn[standard]"
```

API lokal:

```powershell
python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8010
```

Memory lokal:

```powershell
python -m uvicorn services.memory.app:app --host 127.0.0.1 --port 8020
```

Embedding lokal, ausserhalb von Docker Compose:

```powershell
python -m uvicorn services.embedding.app:create_embedding_service_app --factory --host 127.0.0.1 --port 8030
```

Nativer Embedding-Primary:

```powershell
workers\embedding\exec\bin\LiaraEmbeddingService.exe --config=workers\embedding\exec\conf\embedding_config.toml
```

Empfohlener Guard-Start fuer den nativen Embedding-Primary, damit PATH, Lockfile
und Logs konsistent bleiben:

```powershell
.\.venv\Scripts\python.exe scripts\service_guard.py start --service embedding --repo-root C:\ai\LIARA
```

OpenAI Bridge:

```powershell
.\.venv\Scripts\python.exe scripts\service_guard.py start --service bridge --repo-root C:\ai\LIARA
```

Frontend Web UI:

```powershell
cd frontend\web-ui
node node_modules/next/dist/bin/next start -p 3001
```

Die Server-Management-GUI prueft die Web-UI typischerweise ueber
`http://127.0.0.1:3001/architecture`.

Paralleler Node-26-Testpfad:

```powershell
$env:NEXT_DIST_DIR = ".next-node26"
C:\ai\runtimes\node-v26.7.0-win-x64\npm.cmd run build
C:\ai\runtimes\node-v26.7.0-win-x64\node.exe node_modules/next/dist/bin/next start -p 3002
```

Der Servermanager setzt diese Umgebung pro Prozess. Node 24 auf Port `3001`
verwendet weiterhin `.next`; Node 26 auf Port `3002` verwendet
`.next-node26`.

Die API erlaubt beide lokalen Frontend-Origins standardmaessig per CORS. Wird
`LIARA_API_CORS_ALLOW_ORIGINS` gesetzt, muss die explizite Liste bei
Parallelbetrieb sowohl `http://127.0.0.1:3001` als auch
`http://127.0.0.1:3002` beziehungsweise die verwendeten `localhost`-Varianten
enthalten.

Eigenstaendige Heartbeat-Instanz:

```powershell
.\scripts\start_heartbeat_instance.ps1
```

Alternativ direkt:

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.heartbeat.app:app --host 127.0.0.1 --port 8050
```

Server-Management-GUI:

```powershell
python server_management_gui.py
```

## Dependencies

Installationsquellen:

- `pyproject.toml` definiert das Paket `liara` und installiert `services*`.
- `requirements.txt` ist ein Aggregat aus:
  - `requirements-core.txt`
  - `requirements-db.txt`
  - `requirements-optional.txt`
- `requirements-dev.txt` erweitert Core um pytest, pytest-asyncio, black und mypy.

Aktuelle Besonderheit:

- `pyproject.toml` pinnt `fastapi==0.135.3`.
- `requirements-core.txt` pinnt `fastapi==0.136.0`.
- `pyproject.toml` verlangt `chromadb<1.0`, `requirements-db.txt` pinnt `chromadb==1.5.7`.
- `pyproject.toml` verlangt `pillow<12`, `requirements-optional.txt` verlangt `pillow>=12.2.0`.
- `pyproject.toml` begrenzt Black auf `<26`, `requirements-dev.txt` verlangt `black>=26.3.1`.
- Dockerfiles verwenden `requirements.txt`, also den Requirements-Pfad, nicht die `pyproject.toml`-Dependency-Liste.

Bis zur Konsolidierung muss bei Test-/Buildberichten immer genannt werden,
welcher Installationspfad verwendet wurde.

## Health Checks

```powershell
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/health/backends
curl http://127.0.0.1:8020/health
curl http://127.0.0.1:8020/health/backends
curl http://127.0.0.1:8030/health
curl http://127.0.0.1:8011/health
curl http://127.0.0.1:3001/architecture
```

Guard-Status fuer native/hostseitige Services:

```powershell
.\.venv\Scripts\python.exe scripts\service_guard.py status --repo-root C:\ai\LIARA
```

Hinweis: In einem Mischbetrieb koennen API und Memory aus Docker laufen,
waehrend Embedding und Bridge ueber `service_guard.py` laufen. Dann meldet der
Guard fuer API/Memory `connect_ok: true`, aber `lock_present: false`. Das ist
kein Fehler, sondern zeigt nur, dass diese Ports nicht vom Guard gestartet
wurden.

## Wichtige ENV-Werte

### API / Runtime

- `HOST`
- `PORT`
- `DEBUG`
- `MEMORY_MODE`
- `MEMORY_SERVICE_BASE_URL`
- `MEMORY_SERVICE_TIMEOUT_SECONDS`
- `DEFAULT_LLM_PROVIDER`

### Inference

- `LLAMA_CPP_BASE_URL`
- `LLAMA_CPP_MODEL`
- `LLAMA_CPP_TIMEOUT_SECONDS`
- `LLAMA_CPP_BUILD_BASE_DIR`
- `LLAMA_CPP_BUILD_VARIANT`
- `OLLAMA_HOST`
- `OLLAMA_PORT`
- `OLLAMA_MODEL`
- `OPENVINO_GENAI_MODEL_DIR`
- `OPENVINO_GENAI_DEVICE`
- `INFERENCE_BREAKER_ENABLED`

### Memory

- `POSTGRES_URL`
- `REDIS_URL`
- `QDRANT_URL`
- `QDRANT_COLLECTION`
- `QDRANT_VECTOR_SIZE`
- `CHROMA_HOST`
- `CHROMA_PORT`
- `NEO4J_URL`
- `NEO4J_AUTO_SCHEMA`
- `RELATION_EXTRACTION_ENABLED`

### Embedding

- `EMBEDDING_SERVICE_BASE_URL`
- `EMBEDDING_SERVICE_TIMEOUT_SECONDS`
- `EMBEDDING_MODEL_DIR`
- `EMBEDDING_MODEL_ID`
- `EMBEDDING_DEVICE`
- `EMBEDDING_BACKEND`
- `EMBEDDING_ALLOW_FALLBACK`
- `EMBEDDING_NATIVE_PRIMARY_ENABLED`
- `EMBEDDING_NATIVE_SERVICE_BASE_URL`
- `EMBEDDING_NATIVE_TIMEOUT_SECONDS`

### Orchestrator / Reasoning

- `MAX_REASONING_STEPS`
- `MAX_STEP_CONTEXT_TOKENS`
- `EVIDENCE_REASONING_STEPS`
- `SEMANTIC_ROUTING_ENABLED`
- `REWARD_ROUTING_ENABLED`
- `REWARD_JUDGE_ENABLED`
- `NPU_HELPER_OFFLOAD_ENABLED`

### Native WSL-Sessions

- `LIARA_WSL_DISTRO`
- `LIARA_WSL_SESSION_ROOT`
- `LIARA_WSL_SESSION_ARTIFACTS`
- `LIARA_WSL_SESSION_REGISTRY`
- `LIARA_WSL_SESSION_AUDIT`
- `LIARA_WSL_SESSION_MAX_SNAPSHOT_BYTES`
- `LIARA_WSL_SESSION_MAX_FILE_BYTES`
- `LIARA_WSL_SESSION_MAX_PATCH_BYTES`

### Workspace-Agent

- `LIARA_AGENT_WORKSPACE_ROOT`
- `LIARA_AGENT_VALIDATOR_WORKSPACE`
- `LIARA_AGENT_VALIDATOR_TIMEOUT_SECONDS`

### Workspace Artifact Store

- `LIARA_ARTIFACT_STORE_MODE` (`auto`, `wsl`, `local`; Default `auto`)
- `LIARA_ARTIFACT_WSL_ROOT` (Default `/home/liara/workspace`)
- `LIARA_ARTIFACT_WSL_WINDOWS_ROOT` (optionaler expliziter UNC-/Host-Readroot)
- `LIARA_WSL_DISTRO` (Default `Debian`)
- `LIARA_WSL_USER` (Default `liara`)

Im Windows-Hostbetrieb waehlt `auto` fuer den kanonischen POSIX-Workspace den
WSL-Modus. Schreibvorgaenge laufen policy-gated und auditiert ueber SYS. Bei
nicht erreichbarem WSL gibt es keinen lokalen Schreibfallback.
- `LIARA_AGENT_SYS_MAX_ATTEMPTS`
- `LIARA_AGENT_PLANNER_MAX_TOKENS`
- `LIARA_AGENT_DEPENDENCY_ALLOWLIST`
- `LIARA_AGENT_DEPENDENCY_TIMEOUT_SECONDS`
- `LIARA_AGENT_TEST_TIMEOUT_SECONDS`

Direkter Lifecycle:

```powershell
python scripts\wsl_session_cli.py plan
python scripts\wsl_session_cli.py create --label translator-test
python scripts\wsl_session_cli.py exec <session-id> -- julia --version
python scripts\wsl_session_cli.py collect <session-id>
python scripts\wsl_session_cli.py destroy <session-id>
```

`exec` verwendet intern den bestehenden `WslExecutorTool` und damit dessen
Command-/Argument-Policy. Details: `docs/WSL_SESSION_RUNTIME.md`.

## Projektstruktur

| Pfad | Bedeutung |
| --- | --- |
| `services/` | kanonischer Python-Runtime-Code |
| `tests/` | Unit- und Integrationstests |
| `scripts/` | Benchmarks, Audits, Live-Checks, Tooling |
| `infra/docker/` | Dockerfiles |
| `docker-compose.yml` | lokaler Stack |
| `frontend/WMTool-Liara/` | aktive native GTK UI |
| `workers/` | Worker-Prototypen fuer LLM/Embedding |
| `config/` | Runtime-Konfiguration, Prompts, Thresholds |
| `logs/` | Laufzeit- und Testergebnisse |
| `artifacts/wsl_sessions/` | exportierte WSL-Kandidaten, Patches und Collection-Metadaten |
| `backups/` | Projekt-/Code-Backups |
| `src/llama.cpp` | vendored/native llama.cpp Bereich; relevant fuer primaere Inference |
| `llama-builds-final/` | lokale llama.cpp Build-Varianten, unter anderem fuer den SYCL-Pfad |

## Gepruefter lokaler Profilstand 2026-07-14

- `.env`: `MEMORY_MODE=service`, `DEFAULT_LLM_PROVIDER=ollama`, `LIARA_SANDBOX_MODE=wsl`.
- Validator-Modus ist in `.env` nicht gesetzt; der Code-Default ist `worker`.
- SYS-Governance-Enforcement ist in `.env` nicht gesetzt und damit nicht hart aktiviert.
- NPU-Helper-Offload ist in `.env` nicht gesetzt; der Code-Default ist `true`, waehrend Port 8040 im Snapshot nicht erreichbar war.
- API und Memory liefen lokal per Uvicorn; Store-Backends und Validator liefen in Docker; nativer Embedding-Service, llama.cpp und Ollama liefen als Hostprozesse.
