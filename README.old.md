# Liara Architektur - Entkoppelte KI-Plattform

## Ziel

Klare Trennung von:
- Frontend
- Backend (Orchestrierung)
- LLM/Inferenz

Ergebnis: skalierbares, modellunabhaengiges System.

## Architektur-Uebersicht (Target)

```
Frontend
   ↓
liara-api
   ↓
liara-orchestrator
   ├── liara-tools
   ├── liara-memory
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

Aufgaben:
- Auth
- Sessions
- Streaming (SSE)
- API-Endpunkte

Endpoints:
```
/chat
/chat/stream
/runs
/status
/artifacts
```

Regel: Keine Modell- oder Toollogik.

### liara-orchestrator

Zentrale Steuerung.

Aufgaben:
- Intent-Erkennung
- Routing
- Workflow-Planung
- Tool-/LLM-Entscheidung
- Validator-Auswahl

Module:
```
router.py
planner.py
executor.py
```

### liara-inference-gateway

LLM-Entkopplungsschicht.

Aufgaben:
- Modellwahl
- Hardware-Routing (CPU / GPU / NPU)
- Provider-Abstraktion
- Streaming-Normalisierung

Struktur:
```
providers/
  ollama.py
  openvino.py
  openai.py
  vllm.py

router.py
normalizer.py
```

### liara-tools

Deterministische Funktionen.

Aufgaben:
- Tool Registry
- Tool Execution

Beispiele:
```
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

### liara-validator

Vertrauensschicht.

Stufen:
- Fast Check
- Semantic Check
- Judge/Critic

Module:
```
fast_check.py
semantic_check.py
judge.py
```

### liara-memory

Kontext und Wissen.

Technologien:
- Postgres
- Redis
- Qdrant/Chroma
- Neo4j

Module:
```
history.py
facts.py
retrieval.py
embedding.py
```

## Worker-System

### llm-worker

```
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
```
Input -> Tool -> Validator -> Output
```

Komplex:
```
Input
 -> Orchestrator
 -> Memory
 -> Tool/LLM
 -> Validator
 -> Output
```

## Projektstruktur (Target)

```
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
|------|--------|
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

Der aktuelle Code in diesem Repo bildet das Zielbild bereits teilweise in einer
v1-Struktur ab:
- zentrale Service-Contracts unter src/contracts
- Orchestrierung unter src/core
- Inference-Gateway und Tool-Koordination unter src/services
- Memory-Layer unter src/memory

Die Entkopplung in eigenstaendige Service-/Worker-Pakete bleibt das
architektonische Ziel fuer die naechsten Phasen.

## LIARA Compose Stack

Fuer lokale Store-Anbindung laeuft LIARA als eigener Compose-Stack mit
separaten Containern, Volumes und Host-Ports. Damit kollidiert der Stack nicht
mit anderen Projekten auf derselben Maschine.

Start:

```powershell
docker compose up -d
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
