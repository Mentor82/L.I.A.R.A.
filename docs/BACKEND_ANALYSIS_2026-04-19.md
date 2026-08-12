# LIARA Backend - Vollständige Struktur & Ablaufkette Analyse
**Datum:** 2026-04-19  
**Status:** Ist-Zustand Analyse abgeschlossen

---

## 1) Wichtigste Backend-Module & ihre Rollen

| Modul | Pfad | Rolle | Status |
|-------|------|-------|--------|
| **API Layer** | `services/api/app.py` | HTTP-Einstiegspunkte (`/chat`, `/chat_stream`, `/status`, `/runs`, `/artifacts`) | ✅ Produktiv |
| **Orchestrator** | `services/orchestrator/orchestrator.py` | Zentrale Workflow-Steuerung: Intent-Erkennung, Routing, Kontextstrategie | ✅ M1-M7 |
| **Query Router** | `services/orchestrator/router.py` | Intent-basiertes Routing (Tool/LLM/Memory) | ✅ M1 |
| **Query Planner** | `services/orchestrator/planner.py` | Workflow-Planung und Sequenzierung | ✅ M1 |
| **Tool Executor** | `services/orchestrator/executor.py` | Tool-Ausführungsorkestrierung | ✅ M1 |
| **Context Strategy** | `services/orchestrator/context_strategy.py` | Tier-basiertes Memory-Routing (Scope-Filter) | ✅ Implementiert |
| **Librarian Router** | `services/orchestrator/librarian_router.py` | Memory-Klassifikation und Tier-Auswahl | ✅ Implementiert |
| **Tool Coordinator** | `services/tools/coordinator.py` | Parallele Tool-Ausführung mit Timeouts und Error-Handling | ✅ 111+ Tests |
| **Tool Registry** | `services/tools/registry.py` | Tool-Registrierung und Auto-Loading von Built-in Tools | ✅ Aktiv |
| **Inference Gateway** | `services/inference/gateway.py` | Provider-Abstraktionsschicht (Ollama/OpenVINO/OpenAI/vLLM) | ✅ M2 |
| **Inference Providers** | `services/inference/providers/` | Konkrete LLM-Provider: `ollama.py`, `openvino.py`, `llama_cpp.py` | ✅ M2 |
| **Inference Normalizer** | `services/inference/normalizer.py` | Stream-Normalisierung: Chunk/Event/Final Envelopes | ✅ M3 |
| **Inference Invoker** | `services/inference/invocation.py` | Direct vs. Queue-basierte Invocation mit Fallback | ✅ M4-M6 |
| **Memory Adapter** | `services/memory_adapter.py` | Abstraktionsschicht zu Memory-Stores (Service/In-Process) | ✅ M2 |
| **Memory Layer** | `services/memory/tier_store.py` | Tier-Routing (Postgres/Redis/Chroma/Qdrant/Neo4j) | ✅ Produktiv |
| **Memory Service** | `services/memory/app.py` | HTTP-Endpoints (`/history`, `/facts`, `/retrieval`, `/embedding`, `/health`) | ✅ P-T2-1/2/3 |
| **DB Service** | `services/db/postgres_adapter.py` | Postgres FactStore + SessionStore Datenbankzugriff | ✅ Produktiv |
| **Embedding Engine** | `services/embedding/engine.py` | Zentrale kanonische Embedding-Generierung | ✅ Kanalisiert |
| **Embedding Worker** | `workers/embedding-worker/worker.py` | Asynchrone Redis Streams-basierte Embedding-Verarbeitung | ✅ Entkoppelt |
| **Validator** | `services/validator/` | Fast/Semantic/Judge-Check-Ebenen | ✅ Judge integriert |

---

## 2) Konkrete Laufzeit-Ablaufkette für Chat/Orchestrierung

### Flow-Diagramm

```
HTTP POST /chat oder GET /chat/stream
    ↓
[services/api/app.py]
├─ JWT/Session Auth
├─ Request Validation
└─ Session Init
    ↓
[services/orchestrator/orchestrator.py] async def run()
├─ STEP 1: Context Retrieval
│  └─ memory_adapter.retrieve_context(session_id, scope=['team1', 'session'])
│     ├─ [RemoteMemoryAdapter] → POST /retrieval/query (HTTP zu liara-memory)
│     │  oder [LocalMemoryAdapter] → directly in-process
│     └─ [services/memory/tier_store.py]
│        ├─ SessionStore (Redis): Session-State laden
│        ├─ FactStore (Postgres): Kontextgrenzen
│        ├─ ContextStore (Chroma): Aktueller Denkraum
│        ├─ MemoryStore (Qdrant): Semantische Langzeit-Suche
│        └─ RelationStore (Neo4j): Graph-Kontext (optional)
│
├─ STEP 2: Message Building
│  └─ build_messages(context)
│     ├─ History aus Postgres
│     └─ Context aus Chroma/Qdrant (Scope-Filter)
│
├─ STEP 3: Intent Routing
│  └─ [services/orchestrator/router.py]
│     ├─ Tool-Path oder
│     ├─ LLM-Path oder
│     └─ Memory-Lookup-Path
│
├─ STEP 4a: Tool Execution (falls Tool-Intent)
│  └─ [services/tools/coordinator.py]
│     ├─ parallel execute(tools)
│     ├─ Timeout Handling
│     ├─ Error Envelope
│     └─ metadata: {execution_ms, status, tool_name}
│
├─ STEP 4b: Inference (falls LLM-Intent)
│  └─ [services/inference/gateway.py]
│     ├─ [M4-M6] Invoker-Selection: Direct oder Queue-Mode
│     ├─ Provider-Selection: Ollama / OpenVINO / Hybrid-Race
│     ├─ [services/inference/providers/] (ollama.py, openvino.py, llama_cpp.py)
│     ├─ [M3] Stream-Normalisierung via Normalizer
│     └─ Result: InferenceResult {status, content, metadata, error?}
│
├─ STEP 5: Validation
│  └─ [services/validator/]
│     ├─ fast_check (Regex/Keyword-Pattern)
│     ├─ semantic_check (LLM-basiert)
│     └─ judge (Multi-Kriterium)
│
└─ STEP 6: Memory Write
   └─ memory_adapter.write_memory(session_id, message)
      ├─ POST /history/upsert (liara-memory Service)
      └─ [services/memory/tier_store.py] upsert_tier()
         ├─ Postgres INSERT (Fact-Record + History)
         ├─ Redis SET (Session-State mit TTL)
         ├─ Qdrant UPSERT (Vector-Embedding)
         └─ Neo4j MERGE (Graph-Node, optional)

    ↓
[API Response Handler]
├─ SSE Stream (bei /chat/stream)
│  └─ InferenceStreamEvent Chunks
├─ JSON Response (bei /chat)
│  └─ OrchestratorResponse (Stable Contract)
└─ Return to Client
```

### Streaming-Spezial (/chat/stream)

- Nutzt `InferenceStreamNormalizer` (M3)
- Chunks: `InferenceStreamEvent` wrapping
- Final-Envelope enthält vollständige Response
- Telemetry: `ttft_ms` (Time-to-First-Token), `gen_ms`, `load_ms` (Ollama ns→ms)

---

## 3) Memory-Reads/Writes & verfügbare Stores

### Read-Pfade (context_strategy-gesteuert)

```python
# [services/orchestrator/context_strategy.py]
context = await memory_adapter.retrieve_context(
    session_id=session_id,
    scope=['team1', 'session'],  # Tier-Klassifikation
    query=user_query
)
```

**Interne Auflösung:**
- **RemoteMemoryAdapter**: `POST /retrieval/query` → liara-memory HTTP Service
- **LocalMemoryAdapter**: `services/memory/tier_store.py` direkt (in-process)

### Write-Pfade

```python
# Nach LLM-Antwort
await memory_adapter.write_memory(
    session_id=session_id,
    message={
        'role': 'assistant',
        'content': response,
        'eval_binary': True,
        'timestamp': datetime.now(UTC)
    }
)
```

### Verfügbare Stores (Tier-basiert)

| Store | Zweck | Fallback-Verhalten | Zustand |
|-------|-------|-------------------|---------|
| **Postgres** | Wahrheit: Facts, History, Struktur | RAM-Fallback (temporär) | ✅ FactStore+SessionStore |
| **Redis** | Session-Zustand, Kurzzeit-Cache | Keine (sessionlos bei Ausfall) | ✅ Produktiv |
| **Chroma** | Scope-basierter Kontext (RAG, aktueller Denkraum) | Memory-Store (Qdrant) | ✅ ContextStore |
| **Qdrant** | Langzeitgedächtnis (Vektoren, semantische Suche) | Postgres Full-Text Fallback | ✅ Retrieval-Cutover landed |
| **Neo4j** | Beziehungen/Graph-Kontext | Nicht verwendet wenn offline | ⏳ Deferred |

### Degradation-Semantik

(Aus `docs/MEMORY_DEGRADATION_SPEC.md`)

- **Postgres Down**: History-Writes fehlgeschlagen, Session-Kontext läuft über Chroma/Qdrant
- **Redis Down**: Session-State zurückgesetzt, Kontext-Abruf über Postgres/Qdrant
- **Qdrant Down**: Fallback auf Postgres Full-Text oder Chroma-only
- **Neo4j Down**: Graph-Expansion deaktiviert, lineares Retrieval bleibt

---

## 4) Worker-Integration (embedding-worker)

**Status: ASYNCHRON & ENTKOPPELT über Redis Streams**

### Execution Flow

```
Orchestrator (Main Request) [services/orchestrator/orchestrator.py]
    ↓ (non-blocking emit)
    emit_embedding_job(text, model, session_id)
    ↓
Redis Streams Queue [QUEUE_TRANSPORT=redis_streams]
├─ Stream: `embedding:jobs`
├─ Correlation ID: Job-tracking
└─ Message: {text, model, session_id, callback_url?}
    ↓
[workers/embedding-worker/worker.py]
async def process_embedding_job()
├─ 1. Read Job aus Redis Stream
├─ 2. Call [services/embedding/engine.py]
│  └─ Local Embedding Model
├─ 3. Upsert Result in Qdrant
├─ 4. Ack Stream Job
└─ (→ Orchestrator erhält Response NICHT blockiert)
```

### Wichtige Eigenschaften

- Worker läuft **parallel zur Chat-Response** (nicht im kritischen Pfad)
- Embedding ist **optional/Hintergrund**-Optimierung
- Falls Worker offline: Chat-Response weiterhin erfolgreich

### Queue-Transport (M6 implementiert)

- Implementation: `services/inference/queue.py`
- Client: `RedisStreamsInferenceQueueClient`
- Worker: `RedisStreamsInferenceWorker` (auch für Inference reusable)
- Test: `tests/integration/test_inference_redis_live.py` ✅ 1 passed

### Fallback-Strategie (M4)

- Orchestrator default: `DirectInferenceInvoker` (in-process)
- Queue-Fehler → Fallback zu local-direct
- Metadata-Tracking: `invocation_mode` in `execution_trace`

---

## 5) Relevante Tests

### Unit-Tests

| Datei | Fokus | Count |
|-------|-------|-------|
| `tests/unit/test_memory_stores.py` | FactStore, SessionStore, MemoryLayer Tier-Routing | 12 |
| `tests/unit/test_tool_coordinator.py` | Parallele Tool-Ausführung, Timeouts, Error-Handling | 8 |
| `tests/unit/test_inference_gateway.py` | Provider-Selection, Hybrid-Race, Telemetrie ns→ms | 10 |

### Integration-Tests

| Datei | Fokus | Count | Status |
|-------|-------|-------|--------|
| `tests/integration/test_orchestrator_flow.py` | End-to-End Orchestrator + Memory | 18 | ✅ |
| `tests/integration/test_chat_stream_memory_effect_live.py` | Streaming + Memory-Persistierung | 8 | ✅ |
| `tests/integration/test_memory_live.py` | Real SessionStore + FactStore Roundtrips | 2 | ✅ |
| `tests/integration/test_memory_service_live.py` | RemoteMemoryAdapter → FastAPI → Backends | 4 | ✅ |
| `tests/integration/test_inference_live.py` | Real Ollama E2E (TTFTs, Hybrid-Fallback) | 11 | ✅ |
| `tests/integration/test_inference_redis_live.py` | Real Redis Streams Queue | 1 | ✅ |

### Gesamt-Summation (2026-04-14)

✅ **115 passed, 18 skipped**

### Safe Simulation Mode (Regressions-Check)

✅ **93 zusätzliche Tests grün** (keine Regressions)

---

## 6) Offene TODOs/Gaps zum aktuellen Zustand

### TEAM1 Scope (Abgeschlossen)

- [x] M1-M7 alle in Docs beschrieben + validiert
- [x] Queue-Transport-Empfehlung: **Redis Streams** für Phase-1
- [x] Migration Contracts stabil (direct/queue/service-mode)
- [x] Live Tests: Inference + Queue grün

Quelle: `docs/TODO_TEAM1.md`

### TEAM2 Scope (Abgeschlossen)

- [x] M1-M5 alle in Docs beschrieben + validiert
- [x] Memory Service Endpoints alle implementiert
- [x] Qdrant Retrieval-Cutover gelandet + validiert
- [x] Health + Degradation Spec dokumentiert
- [x] Live Tests: Service-Mode + Storage grün

Quelle: `docs/TODO_TEAM2.md`

### Noch offene Punkte (optional/Phase-2)

| Gap | Zustand | Grund | Dokumentation |
|-----|---------|-------|---|
| **Graph (Neo4j) Tier-Integration** | ⏳ Deferred | Pattern-Service Cutover noch pending | Deferred in TODO_TEAM2 |
| **Streaming Timeout-Handling** | ⏳ Backlog | Stream-Disconnect Robustheit | TODO_TEAM1 |
| **Memory Degradation Live-Tests (Full Coverage)** | ⏳ Partial | Nur MemoryServiceStatus; Full-Failure-Szenarien nicht alle covered | MEMORY_DEGRADATION_SPEC.md |
| **Worker Dead-Letter-Queue** | ⏳ Backlog | Queue-Retry gut, aber DLQ für Fehler-Tracking nicht implementiert | TODO_TEAM1 |
| **Multi-Provider Edge-Case Tests** | ✅ Covered | Hybrid-Race, Fallback, Timeouts | test_inference_live.py |

---

## Zusammenfassung Ist-Zustand (2026-04-19)

### ✅ Produktiv & Validiert

- API → Orchestrator → Tools/Inference → Memory Ablaufkette: **VOLLSTÄNDIG**
- Alle 6 Hauptkomponenten: **IMPLEMENTIERT**
- Memory-Abstraktionsschicht (Adapter-Pattern): **STABIL**
- Worker-Entkoppelung (Redis Streams): **FUNKTIONAL**
- Test-Evidenz: **115 bestätigt, 93 Regressions-frei**
- Health/Degradation: **DEFINIERT**

### ⏳ Phase-2 Backlog (nicht kritisch)

- Graph-Tier (Neo4j) Fertigstellung
- Streaming Robustheit für Edge-Cases
- Worker Dead-Letter-Queue

### Betriebsmodus

Backend läuft produktiv im **direkten (in-process)** Modus. Service-Mode & Queue-Transport sind **blueprints-ready** für optionale Phase-2 Skalierung.

---

**Analyse abgeschlossen:** 2026-04-19  
**Alle 6 geforderten Punkte mit Dateipfaden und aktuellen Status dokumentiert.**
