# MEMORY ARCHITECTURE

# 🧠 Liara Memory – Datenbank-Nutzung (Copilot-Spec)

## 🎯 Ziel

Diese Spezifikation definiert **verbindlich**, wann welche Datenbank verwendet wird.

❗ Wichtig:

* Der **Orchestrator entscheidet den Use Case**
* Der **Memory-Service entscheidet die Datenbank**
* **Kein direkter DB-Zugriff außerhalb des Memory-Service**

---

# 🧩 Grundprinzip

Jede Datenbank hat genau eine Aufgabe:

| Datenbank     | Aufgabe     | Bedeutung          |
| ------------- | ----------- | ------------------ |
| Postgres      | Wahrheit    | Persistente Daten  |
| Redis         | Kurzzeit    | Temporärer Zustand |
| Qdrant        | Ähnlichkeit | Semantische Suche  |
| Neo4j         | Beziehungen | Graph-Kontext      |

---

# ⚙️ Regeln (STRICT)

## Regel 1

```text
KEIN Service greift direkt auf Datenbanken zu.
ALLES läuft über MemoryAdapter.
```

---

## Regel 2

```text
POSTGRES ist die einzige Quelle für persistente Wahrheit.
```

Speichert:

* Facts
* Chat History
* Events

---

## Regel 3

```text
QDRANT wird NUR für semantische Suche verwendet.
```

Speichert:

* Embeddings
* Textfragmente

---

## Regel 4

```text
REDIS speichert KEINE Wahrheit.
```

Nur:

* Session State
* Cache
* Queue

---

## Regel 5

```text
NEO4J wird NUR für Beziehungen verwendet.
```

Optional in Phase 1

---

# 🔄 Operationen

---

## 🟢 STORE

### Zweck:

Persistente Information speichern

### Ablauf:

```text
1. Speichere Daten in Postgres
2. Erzeuge Embedding
3. Speichere Embedding in Qdrant
4. (Optional) Speichere Relation in Neo4j
```

### Pseudocode:

```python
def store(data):
    id = postgres.insert(data)

    # Embedding is delegated to the liara-embedding HTTP service.
    # BackedMemoryServiceStore.generate_embedding() calls
    # POST {EMBEDDING_SERVICE_BASE_URL}/embedding/generate
    embedding = embedding_service.generate(data["content"])
    qdrant.upsert(id, embedding)

    if "relations" in data:
        neo4j.store(data["relations"])

    return id
```

---

## 🔵 QUERY

### Zweck:

Semantische Suche

### Ablauf:

```text
1. Suche in Qdrant
2. Hole IDs
3. Lade vollständige Daten aus Postgres
```

### Pseudocode:

```python
def query(query):
    ids = qdrant.search(query)

    results = postgres.load(ids)

    return results
```

---

## 🟣 CONTEXT

### Zweck:

LLM-Kontext bauen

### Ablauf:

```text
1. Qdrant → ähnliche Inhalte
2. Postgres → Fakten
3. (Optional) Neo4j → Beziehungen
4. Kombinieren
```

### Pseudocode:

```python
def build_context(query):
    similar = qdrant.search(query)
    facts = postgres.load(similar.ids)

    context = merge(similar, facts)

    return context
```

---

## 🟡 SESSION (Redis)

### Zweck:

Temporärer Zustand

### Nutzung:

```python
def get_session(user_id):
    return redis.get(user_id)
```

❗ Redis wird NICHT für persistente Daten verwendet.

---

# 🚫 VERBOTEN

```text
- Direkter Zugriff auf Postgres außerhalb Memory-Service
- Direkter Zugriff auf Qdrant außerhalb Memory-Service
- Speicherung von Wahrheit in Redis
- Vermischung von Embeddings und strukturierten Daten
```

---

# 🧠 Entscheidungslogik

## Orchestrator entscheidet:

```python
if query_type == "semantic":
    memory.query()

elif query_type == "fact":
    memory.load()

elif query_type == "context":
    memory.build_context()
```

---

# ⚡ Kurzfassung (Merksatz)

```text
Postgres = Wahrheit
Qdrant = Erinnerung
Redis = Jetzt
Neo4j = Verständnis
```

---

# 🧪 Entwicklungsstrategie

## Phase 1

* Postgres + Qdrant aktiv
* Redis für Session
* Neo4j optional

## Phase 2

* Fallback entfernen
* echte Embeddings
* echtes Retrieval

## Phase 3

* Neo4j aktivieren
* Graph-Kontext integrieren

---

# 🔥 Leitprinzip

```text
Datenbanken sind Implementierungsdetails.
Der Memory-Service ist die einzige Schnittstelle.
```


Four-tier memory system for different access patterns and lifespans.

## Tier 1: SESSION (Redis)
**Purpose:** Ephemeral request/response state  
**TTL:** Minutes (default 15 min)  
**Access:** Read/write per-request  
**Use Cases:**
- Active run states
- Tool execution results (in-flight)
- User preferences (current session)

**Schema:**
```
session:{session_id}:state → RunState
session:{session_id}:tools → {tool_name: status}
session:{session_id}:context → {query, tools_used, ...}
```

---

## Tier 2: PERSISTENT (Postgres)
**Purpose:** Permanent fact storage  
**TTL:** Infinite  
**Access:** Structured queries  
**Use Cases:**
- Message history
- User profiles
- Tool execution audit log
- System configuration

**Schema (Example):**
```sql
CREATE TABLE message_history (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES sessions(id),
    run_id UUID,
    message TEXT,
    response TEXT,
    tools_used TEXT[],
    created_at TIMESTAMP,
    metadata JSONB
);

CREATE TABLE tool_executions (
    id UUID PRIMARY KEY,
    run_id UUID,
    tool_name TEXT,
    status TEXT,
    output JSONB,
    execution_ms FLOAT,
    created_at TIMESTAMP
);
```

---

## Tier 3: RETRIEVAL (Qdrant)
**Purpose:** Semantic similarity search  
**TTL:** Configurable (typically indefinite with aging)  
**Access:** Vector queries  
**Use Cases:**
- RAG retrieval (context documents)
- Similar question matching
- Knowledge base lookup

**Schema:**
```
{
    document_id: str,
    embedding: [float],
    text: str,
    source: str,
    timestamp: int,
    metadata: {
        tool: str,
        session_id: str,
    }
}
```

---

## Tier 4: PATTERN (Neo4j)
**Purpose:** Tool-outcome relationship graphs  
**TTL:** Infinite with versioning  
**Access:** Graph queries + path finding  
**Use Cases:**
- "Which tools work best for this type of query?"
- "What's the success path for similar questions?"
- Decision tree building

**Schema (Cypher):**
```cypher
(Query)-[:USED]->(Tool)-[:PRODUCED]->(Output)
         ↓
    [SUCCESS_RATE]

(Topic)-[:COMMONLY_USED]->(ToolSet)
```

---

## Unified Access Interface

```python
memory_layer = MemoryLayer(
    session_store=SessionStore(),      # Redis
    fact_store=FactStore(),            # Postgres
    retrieval_index=RetrievalIndex(),  # Qdrant
    graph_store=GraphStore(),          # Neo4j
)

# Write to any tier
await memory_layer.set(
    tier=MemoryTier.SESSION,
    key="run:123:tools",
    value=["web_search", "current_time"],
    ttl_seconds=600  # 10 minutes
)

# Read from any tier
tools = await memory_layer.get(
    tier=MemoryTier.SESSION,
    key="run:123:tools",
    default=[]
)
```

---

## Migration + Flushing

- **Session → Persistent:** After run completes, archive to Postgres
- **Persistent → Retrieval:** Periodically vectorize documents for RAG
- **Retrieval → Pattern:** Batch analysis to discover tool-outcome patterns
- **Cleanup:** Old session data (>30d) flushed from Redis

