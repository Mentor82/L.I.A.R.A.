# 🧠 Liara Context Upsert Strategy (v0.1.1 – STRICT)

## 🎯 Ziel

Diese Spezifikation definiert, **wann neu entstandener Kontext** während eines Denk- oder Validierungsprozesses zurückgeschrieben werden darf, **wohin** er geschrieben wird und **unter welchen Bedingungen** er dauerhaft gespeichert werden darf.

Context Upsert ist nötig für:

* mehrstufiges Reasoning
* Validierungszyklen
* Kontextverdichtung
* Vermeidung doppelter Retrievals
* kontrolliertes Lernen innerhalb eines Laufs

---

# ❗ Grundregel

```text
Not every intermediate result may be persisted.

Default target for reasoning artifacts is temporary session storage.

Only validated and explicitly accepted facts may be persisted long-term.
```

---

# 🧩 Begriffsklärung

## Context Upsert

Upsert bedeutet hier:

* neuen Kontext speichern
* bestehenden Kontext ergänzen
* Session-Kontext aktualisieren

Context Upsert ist **kein automatisches Langzeitgedächtnis**.

---

# 🧱 Speicherziele

| Ziel             | Datenbank | Zweck                               | Haltbarkeit |
| ---------------- | --------- | ----------------------------------- | ----------- |
| TEMP             | Redis     | Laufzeitkontext / Zwischenschritte  | kurz        |
| WORKING_CONTEXT  | Chroma    | kontextnahe semantische Verdichtung | mittel      |
| FACT_STORE       | Postgres  | bestätigte Wahrheit                 | dauerhaft   |
| RELATION_STORE   | Neo4j     | bestätigte Beziehungen              | dauerhaft   |
| LONG_TERM_MEMORY | Qdrant    | bestätigte semantische Erinnerung   | dauerhaft   |

---

# ⚙️ Speicherklassen

## 1. TEMP

Nur für den aktuellen Lauf / die aktuelle Session.

Beispiele:

* Zwischenzusammenfassungen
* Validierungsergebnisse
* Verdichtete Retrieval-Treffer
* offene Hypothesen
* Reasoning Notes

### Ziel

```text
Redis
```

### Regel

```text
Default class for all intermediate context artifacts.
```

---

## 2. WORKING_CONTEXT

Kontext, der für ähnliche Anfragen innerhalb eines begrenzten Zeitraums nützlich sein kann, aber noch keine bestätigte Wahrheit ist.

Beispiele:

* laufende technische Analyse
* strukturierte Diskussion
* kontextnahe Arbeitszusammenfassung

### Ziel

```text
Chroma
```

### Regel

```text
Only allowed if artifact is useful beyond the current turn
but not yet permanent truth.
```

---

## 3. FACT

Bestätigte, stabile Information.

Beispiele:

* Architekturentscheidung
* bestätigte Systemregel
* explizit akzeptierter Merksatz

### Ziel

```text
Postgres
```

### Regel

```text
Only validated facts may be persisted as FACT.
```

---

## 4. RELATION

Bestätigte Beziehung oder Abhängigkeit.

Beispiele:

* Liara uses Redis
* Memory depends on Embedding Worker
* Context Engine hydrates Postgres results

### Ziel

```text
Neo4j
```

### Regel

```text
Only validated structural relationships may be persisted.
```

---

## 5. LONG_TERM_MEMORY

Semantisch relevante, bestätigte Information für zukünftiges Retrieval.

Beispiele:

* bestätigte Analysezusammenfassung
* stabiler Wissensbaustein
* explizit freigegebene Gedächtniseinträge

### Ziel

```text
Qdrant + Postgres hydration reference
```

### Regel

```text
Only persist if:
- validated
- semantically useful
- not session-only
```

---

# 🔒 Harte Regeln

## Regel 1

```text
Intermediate reasoning output must never be written directly to long-term memory by default.
```

## Regel 2

```text
Unvalidated model output must never be stored as fact.
```

## Regel 3

```text
Session-level upserts go to Redis first.
```

## Regel 4

```text
Long-term persistence requires validation or explicit acceptance.
```

## Regel 5

```text
Secrets, credentials, tokens, and authentication data must never be upserted into any context store.
```

---

# 🔀 Entscheidungslogik

## Klassifikationsfragen

Vor jedem Upsert muss beantwortet werden:

1. Ist das nur für den aktuellen Lauf relevant?
2. Ist das eine Hypothese oder bereits bestätigt?
3. Ist das für zukünftige semantische Suche nützlich?
4. Ist das eine stabile Tatsache?
5. Ist das eine Beziehung zwischen Objekten?

---

# 🧠 Upsert Mapping

| Artefakt                          | Ziel              |
| --------------------------------- | ----------------- |
| Zwischenzusammenfassung           | Redis             |
| Validierungsergebnis              | Redis             |
| Verdichteter Arbeitskontext       | Chroma            |
| bestätigte Tatsache               | Postgres          |
| bestätigte Relation               | Neo4j             |
| bestätigte semantische Erinnerung | Qdrant + Postgres |

---

# ✅ Erlaubte Beispiele

## TEMP

```text
"Current reasoning summary: retrieval results 2 and 4 are duplicates."
→ Redis
```

## WORKING_CONTEXT

```text
"Current architecture discussion summary for Memory-Service split."
→ Chroma
```

## FACT

```text
"Memory access is only allowed through MemoryAdapter."
→ Postgres
```

## RELATION

```text
"Liara Context Engine uses Chroma for current-context retrieval."
→ Neo4j
```

## LONG_TERM_MEMORY

```text
"Qwen3-Embedding-0.6B is the standard embedding model for multilingual retrieval."
→ Qdrant + Postgres reference
```

---

# ❌ Verbotene Beispiele

## Forbidden

```text
Raw chain-of-thought
Unvalidated intermediate guesses
Temporary failed answers
Credentials
Session secrets
Prompt fragments containing authentication data
```

---

# 🔁 Upsert im Reasoning Loop

## Flow

```text
1. Load initial context
2. Run reasoning step
3. Generate intermediate artifact
4. Classify artifact
5. Upsert to correct target
6. Continue reasoning with updated context
```

---

# 📏 Grenzen

## Redis

```text
Short TTL mandatory
Default TTL for temporary artifacts: 1h
```

## Chroma

```text
Working-context artifacts must be tagged and removable
```

## Postgres

```text
Only store validated structured facts
```

## Qdrant

```text
Only store semantically useful validated memory
```

## Neo4j

```text
Only store validated relations
```

---

# 🧷 Metadaten (MANDATORY)

Jeder Upsert-Eintrag muss Metadaten tragen:

```json
{
  "source": "reasoning_loop",
  "artifact_type": "temp_summary",
  "validation_status": "unvalidated",
  "scope": "session",
  "language": "de",
  "created_by": "liara",
  "reasoning_step": 2
}
```

---

# 🧠 Minimal-Pseudocode

```python
def upsert_context(artifact):
    if artifact.contains_secret:
        return reject()

    if artifact.type == "intermediate":
        return redis_store(artifact, ttl=3600)

    if artifact.type == "working_context" and artifact.validated:
        return chroma_store(artifact)

    if artifact.type == "fact" and artifact.validated:
        fact_id = postgres_store(artifact)
        return fact_id

    if artifact.type == "relation" and artifact.validated:
        return neo4j_store(artifact)

    if artifact.type == "long_term_memory" and artifact.validated:
        ref_id = postgres_store(artifact)
        return qdrant_store(ref_id, artifact.embedding)

    return redis_store(artifact, ttl=3600)
```

---

# 🔥 Leitprinzip

```text
Read context carefully.
Write context conservatively.
Persist truth only after validation.
```

---

# ⚡ Kurzfassung

```text
Default = Redis
Validated fact = Postgres
Validated relation = Neo4j
Validated semantic memory = Qdrant
Working context = Chroma
```
