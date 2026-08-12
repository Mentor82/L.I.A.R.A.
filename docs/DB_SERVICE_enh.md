# 🧠 Liara Memory – Datenbank-Nutzung (v0.1.1)

## 🎯 Ziel

Diese Spezifikation definiert **verbindlich**, wie Liara Daten speichert, abruft und verarbeitet.

❗ Grundprinzip:

* Der **Orchestrator entscheidet den Use Case**
* Der **Memory-Service entscheidet die Datenhaltung**
* **Kein direkter Datenbankzugriff außerhalb des Memory-Service**

---

## 🧩 Architekturprinzip

Jede Datenbank erfüllt **genau eine klar definierte Rolle**.

| Datenbank | Aufgabe            | Bedeutung                                  |
| --------- | ------------------ | ------------------------------------------ |
| Postgres  | Wahrheit           | Persistente Daten (Fakten, Chats, Events)  |
| Redis     | Kurzzeit           | Temporärer Zustand (Session, Cache, Queue) |
| Chroma    | Kontextsuche       | Aktueller Denkraum (RAG, Query-Kontext)    |
| Qdrant    | Langzeitgedächtnis | Persistente semantische Erinnerung         |
| Neo4j     | Beziehungen        | Graph-Struktur und Kontextverknüpfung      |

---

## ⚙️ Regeln (STRICT)

**Merksatz fuer Liara:** Redis denkt gerade. Chroma erinnert kurzfristig. Qdrant erinnert dauerhaft. Neo4j erklaert, was zusammenhaengt - aber mit Ablaufdatum pro Beziehung.

### Regel 1 – Zugriff

```text
KEIN Service greift direkt auf Datenbanken zu.
ALLE Zugriffe erfolgen ausschließlich über den Memory-Service.
```

---

### Regel 2 – Wahrheit

```text
POSTGRES ist die einzige Quelle für persistente Wahrheit.
```

Speichert:

* Facts
* Chat History
* Events
* Metadaten

---

### Regel 3 – Kontext (Chroma)

```text
CHROMA dient ausschließlich der kontextnahen semantischen Suche.
```

Speichert:

* temporär relevante Embeddings
* kuratierte Wissensfragmente für RAG

Zweck:

* „Was ist JETZT relevant?“

Wichtig:

* Chroma beantwortet nicht nur „ähnlich“, sondern „ähnlich im richtigen Scope“.
* Ranking muss daher Metadaten berücksichtigen (z. B. `session_id`, `run_id`, `file`, `symbol`, `turn_index`, `time_decay`).
* Ohne Scope-Filter steigt das Risiko, semantisch ähnliche, aber falsche Fragmente zu ziehen.
* Chroma ist systemweites Kurzzeitgedaechtnis, nicht Langzeitarchiv.
* Empfohlene TTL fuer Short-Term-Kontext: z. B. 14 Tage.

---

### Regel 4 – Langzeit (Qdrant)

```text
QDRANT speichert semantische Langzeiterinnerung.
```

Speichert:

* persistente Embeddings
* historisierte Inhalte

Zweck:

* „Was weiß das System langfristig?“

---

### Regel 5 – Kurzzeit (Redis)

```text
REDIS speichert KEINE Wahrheit.
```

Speichert nur:

* Session State
* Cache
* Queue
* temporäre Flags

Erweiterung:

* Redis ist Working Memory pro `runtime_id` / `task_id`.
* TTL orientiert sich am aktiven Arbeitsablauf und endet spaetestens mit Task-/Run-Ende.

---

### Regel 6 – Beziehungen (Neo4j)

```text
NEO4J wird ausschließlich für Beziehungen verwendet.
```

Speichert:

* Relationen
* Abhängigkeiten
* Kontextverknüpfungen

Lebensdauerregel:

* Beziehungen zu Redis-/Chroma-Artefakten duerfen nur so lange aktiv sein wie das jeweilige Working-/Short-Term-Objekt.
* Stabile Beziehungen ohne TTL gehoeren nur an Long-Term-Artefakte in Qdrant bzw. bestaetigte Wissenselemente.

---

### Regel 7 – Tier-Zuordnung

| Tier | Schluesselraum | TTL | Backends |
| ---- | -------------- | --- | -------- |
| Working | `runtime_id`, `task_id` | Minuten bis Task-Ende | Redis + temporaere Neo4j-Kanten |
| Short-Term | `session_id`, `user_id`, `topic_id` | z. B. 14 Tage | Chroma + temporaere Neo4j-Kontextbeziehungen |
| Long-Term | `user_id`, `project_id`, `knowledge_id` | kein automatisches TTL | Qdrant + stabile Neo4j-Beziehungen |

Promotion-Regel:

* Nur validierter oder explizit gemerkter Inhalt darf von Short-Term nach Long-Term promoted werden.

---

## 🔄 Operationen

---

### 🟢 STORE

#### Zweck

Persistente Information speichern

#### Ablauf

```text
1. Daten in Postgres speichern
2. Embedding erzeugen
3. Embedding in Qdrant speichern
4. (Optional) Beziehungen in Neo4j speichern
5. (Optional) Kontext in Chroma vorbereiten
```

#### Pseudocode

```python
def store(data):
    id = postgres.insert(data)

    embedding = embed(data["content"])
    qdrant.upsert(id, embedding)

    if "relations" in data:
        neo4j.store(data["relations"])

    return id
```

---

### 🔵 CONTEXT_SEARCH (Chroma)

#### Zweck

Kontext für aktuelle Anfrage bestimmen

Beispiel-Frage:

* „Nach dem Punkt: Welche `fib(n-1)` brauche ich?“

Bedeutung:

* Es kann mehrere `fib(n-1)`-Treffer geben (anderer Scope, alte Iteration, anderer Dateikontext).
* Chroma muss den Treffer liefern, der zum aktuellen Ausführungskontext passt, nicht den global ähnlichsten.

#### Ablauf

```text
1. Query embedding erzeugen
2. Scope-Filter aus aktuellem Zustand ableiten (Session, Run, Datei, Symbol, Zeitfenster)
3. Suche in Chroma mit Filter + Similarity
4. Re-Ranking mit Kontextsignalen (Nähe im Dialog/Codepfad, Frische, Symbol-Match)
5. Rückgabe relevanter Fragmente
```

#### Pseudocode

```python
def context_search(query, scope):
    # 1) semantische Kandidaten im passenden Scope holen
    candidates = chroma.search(
        query,
        where={
            "session_id": scope.session_id,
            "run_id": scope.run_id,
            "file": scope.file,
            "symbol": scope.symbol,
        },
    )

    # 2) nach Scope-Naehe und Frische neu ranken
    ranked = rerank_with_context_signals(candidates, scope)
    return ranked[:scope.k]
```

---

### 🔷 MEMORY_SEARCH (Qdrant)

#### Zweck

Langzeitwissen abrufen

#### Ablauf

```text
1. Query embedding erzeugen
2. Suche in Qdrant
3. IDs zurückgeben
4. Daten aus Postgres laden
```

#### Pseudocode

```python
def memory_search(query):
    ids = qdrant.search(query)
    return postgres.load(ids)
```

---

### 🟣 BUILD_CONTEXT

#### Zweck

LLM-Kontext erstellen

#### Ablauf

```text
1. Chroma → aktueller Kontext
2. Qdrant → Langzeitwissen
3. Postgres → Fakten
4. Neo4j → Beziehungen (optional)
5. Zusammenführen
```

#### Pseudocode

```python
def build_context(query):
    context_fragments = chroma.search(query)
    memory_ids = qdrant.search(query)

    facts = postgres.load(memory_ids)

    relations = neo4j.expand(memory_ids)

    return merge(context_fragments, facts, relations)
```

---

### 🟡 SESSION (Redis)

#### Zweck

Temporären Zustand verwalten

#### Pseudocode

```python
def get_session(user_id):
    return redis.get(user_id)
```

---

## 🚫 VERBOTEN

```text
- Direkter Zugriff auf Postgres außerhalb Memory-Service
- Direkter Zugriff auf Chroma außerhalb Memory-Service
- Direkter Zugriff auf Qdrant außerhalb Memory-Service
- Speicherung von Wahrheit in Redis
- Vermischung von Kontext (Chroma) und Langzeitwissen (Qdrant)
```

---

## 🧠 Entscheidungslogik (Orchestrator)

```python
if query_type == "context":
    memory.context_search()

elif query_type == "memory":
    memory.memory_search()

elif query_type == "fact":
    memory.load()

elif query_type == "full_context":
    memory.build_context()
```

---

## ⚡ Merksatz

```text
Postgres = Wahrheit
Redis = Jetzt
Chroma = aktueller Denkraum
Qdrant = Langzeit-Erinnerung
Neo4j = Beziehungen
```

---

## 🧪 Entwicklungsstrategie

### Phase 1 (v0.1.1)

* Postgres aktiv
* Redis aktiv
* Qdrant aktiv
* Chroma optional (nur für RAG)
* Neo4j optional

---

### Phase 2

* Chroma aktiv für Kontextselektion
* klare Trennung Kontext vs. Memory
* optimiertes Retrieval

---

### Phase 3

* Neo4j aktiv
* Graph-Kontext integriert
* Kontext + Beziehungen kombiniert

---

## 🔥 Leitprinzip

```text
Datenbanken sind Implementierungsdetails.
Der Memory-Service ist die einzige Wahrheitsschicht.
```
