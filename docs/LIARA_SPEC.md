# 🧠 Liara Architektur & Memory – Gesamtspezifikation (v0.1.1)

---

## 🎯 Ziel

Diese Spezifikation beschreibt die **komplette Architektur**, die **Memory-Logik** und die **Context Strategy Engine** von Liara.

👉 Ziel:

* klare Trennung von Verantwortung
* skalierbares System
* kontrollierte Kontextverarbeitung
* reproduzierbares Verhalten

---

## 🧱 Gesamtarchitektur

"""
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
"""

---

## 🧩 Komponenten

### 🟦 liara-api

**Aufgaben:**

* Auth
* Sessions
* SSE Streaming
* API-Endpunkte

❗ Keine Business-Logik

---

### 🟨 liara-orchestrator

***Zentrale Steuerung***

**Aufgaben:**

* Intent-Erkennung
* Routing
* Workflow-Planung
* Context Strategy
* Tool-/LLM-Entscheidung

---

### 🟪 liara-inference-gateway

***LLM-Abstraktion***

* Modellwahl
* Hardware-Routing (CPU/GPU/NPU)
* Provider-Handling
* Streaming-Normalisierung

---

### 🟩 liara-tools

***Deterministische Funktionen***

* Tool Registry
* Tool Execution

---

### 🟥 liara-validator

***Vertrauensschicht***

* Fast Check
* Semantic Check
* Judge

---

### 🟫 liara-memory

***Mehrschichtiges Wissenssystem***

---

## 🧠 Memory-Architektur

### 📦 Datenbanken & Rollen

| Datenbank | Aufgabe                 | Bedeutung                                          |
| --------- | ----------------------- | -------------------------------------------------- |
| Postgres  | Persistente Wahrheit    | Fakten, Chats, Events, Metadaten                   |
| Redis     | Kurzzeitzustand         | Session, Cache, Queue, TTL-State                   |
| Chroma    | Arbeitsgedächtnis / RAG | kuratierte semantische Suche für aktuellen Kontext |
| Qdrant    | Langzeit-Semantik       | persistente Embeddings und langfristige Erinnerung |
| Neo4j     | Beziehungen             | Graph-Kontext, Verknüpfungen, Struktur             |

---

### ⚙️ Memory-Regeln (STRICT)

**Merksatz fuer Liara:** Redis denkt gerade. Chroma erinnert kurzfristig. Qdrant erinnert dauerhaft. Neo4j erklaert, was zusammenhaengt - aber mit Ablaufdatum pro Beziehung.

```text
- Kein direkter DB-Zugriff außerhalb Memory-Service
- Postgres ist einzige Wahrheit
- Redis speichert keine Wahrheit
- Chroma ≠ Qdrant
- Kontext und Erinnerung dürfen nicht vermischt werden
- Chroma darf nicht den global ähnlichsten Treffer liefern
  → immer Scope-Filter (session_id / run_id / file / symbol) anwenden
  → Beispiel: fib(n-1) darf nicht fib(n-2) aus anderem Kontext matchen
- Redis = Working Memory pro runtime_id / task_id, TTL Minuten bis Task-Ende
- Chroma = Short-Term Memory pro session_id / user_id / topic_id, TTL z. B. 14 Tage
- Qdrant = Long-Term Memory pro user_id / project_id / knowledge_id, kein automatisches TTL
- Neo4j-Beziehungen zu Redis-/Kurzzeit-Artefakten duerfen nie laenger leben als das zugehoerige Working-/Short-Term-Objekt
- Promotion: nur validierter oder explizit gemerkter Short-Term-Kontext darf in Long-Term uebergehen
```

---

### 📦 Memory-Module

```text
facts.py
history.py
session.py

context_store.py    (Chroma)
memory_store.py     (Qdrant)

relations.py        (Neo4j)

embedding.py
adapter.py
```

---

### 🧠 Memory-Funktionen

```python
context_search(query, scope)   # scope: session_id, run_id, file, symbol, time_decay
memory_search(query)
fact_load(ids)
relation_expand(ids)
session_get(user_id)
```

---

## 🔄 Memory-Operationen

### STORE

```text
1. Postgres speichern
2. Embedding erzeugen
3. Qdrant speichern
4. Neo4j optional
5. Chroma optional
```

#### Zielklassen

| Klasse | Primärschlüssel | TTL | Ziel |
| ------ | --------------- | --- | ---- |
| Working | `runtime_id`, `task_id` | Minuten bis Task-Ende | Redis + temporaere Neo4j-Kanten |
| Short-Term | `session_id`, `user_id`, `topic_id` | z. B. 14 Tage | Chroma + temporaere Neo4j-Kontextbeziehungen |
| Long-Term | `user_id`, `project_id`, `knowledge_id` | kein automatisches TTL | Qdrant + stabile Neo4j-Beziehungen |

Regel:
Kurzlebige Neo4j-Kanten muessen zusammen mit dem jeweiligen Working-/Short-Term-Artefakt auslaufen, entfernt oder als inaktiv markiert werden.

---

### CONTEXT_SEARCH (Chroma)

**Ablauf:**

```text
1. Query embedden
2. Scope auflösen (session_id / run_id / file / symbol / time_decay)
3. Chroma: search(query, where=scope_filter)
4. Re-Ranking nach Kontextsignalen
5. Ergebnis zurückgeben
```

**Pseudocode:**

```python
def context_search(query, scope):
    embedding = embed(query)
    scope_filter = resolve_scope(scope)  # → {session_id, run_id, ...}
    candidates = chroma.search(embedding, where=scope_filter, top_k=20)
    return rerank_with_context_signals(candidates, scope)
```

❗ Ohne Scope-Filter → falsche semantische Matches aus anderen Sitzungen

---

### MEMORY_SEARCH (Qdrant)

* Langzeitwissen

---

### BUILD_CONTEXT

```text
Chroma + Qdrant + Postgres + Neo4j
→ zusammenführen
```

---

## 🧠 Context Strategy Engine

### 🎯 Ziel

Bestimmt:

* ob Kontext benötigt wird
* welche Quellen verwendet werden
* wie viel Kontext geladen wird
* wann gestoppt wird

---

### 🧩 Kontextmodi

| Modus        | Beschreibung         |
| ------------ | -------------------- |
| NONE         | kein Kontext         |
| SESSION      | aktueller Zustand    |
| FACT         | strukturierte Fakten |
| CONTEXT      | aktueller Denkraum   |
| MEMORY       | Langzeitwissen       |
| RELATIONAL   | Beziehungen          |
| FULL_CONTEXT | alles kombiniert     |

---

### 🔀 Entscheidungslogik

```python
def decide_context_mode(req):
    if req.tool_only:
        return "NONE"
    if req.simple:
        return "SESSION"
    if req.fact:
        return "FACT"
    if req.context:
        return "CONTEXT"
    if req.memory:
        return "MEMORY"
    if req.relation:
        return "RELATIONAL"
    return "FULL_CONTEXT"
```

---

## ⚖️ Gewichtung

| Quelle   | Gewicht |
| -------- | ------: |
| Redis    |     1.0 |
| Postgres |     1.0 |
| Chroma   |     0.9 |
| Qdrant   |     0.8 |
| Neo4j    |     0.7 |

---

## 📦 Kontextbudget

### Regeln

```text
- begrenzte Tokenanzahl
- Fakten vor Ähnlichkeit
- Duplikate vermeiden
- harte Cutoffs
```

---

### Beispiel

| Quelle   |    Limit |
| -------- | -------: |
| Redis    |        5 |
| Postgres |       10 |
| Chroma   |        8 |
| Qdrant   |        8 |
| Neo4j    | begrenzt |

---

## 🧹 Deduplizierung

* gleiche IDs entfernen
* ähnliche Inhalte zusammenführen
* Fakten priorisieren

---

## 🔒 Grenzen

| Regel              | Wert |
| ------------------ | ---: |
| max_context_rounds |    2 |
| max_memory_hops    |    2 |
| max_relation_depth |    2 |

> Diese Limits sind kein Zufall — sie folgen aus dem **Fibonacci-Prinzip** (siehe unten).

---

## 🧮 Fibonacci-Wächter

### 💡 Warum Fibonacci?

Naive Agenten-Denkketten wachsen exponentiell — wie die rekursive Fibonacci-Berechnung ohne Cache:

```math
fib(n) = fib(n-1) + fib(n-2)
```

| Rekursionstiefe | Pfade (naiv) |
| --------------- | -----------: |
| 1               |            2 |
| 2               |            4 |
| 5               |           32 |
| 10              |         1024 |
| 20              |        ~1 Mio|

➡️ Das ist genau das Problem mit "sturem" LLM-Denken:
je tiefer das Modell rekursiv denkt, desto mehr Pfade explodieren unkontrolliert.

---

### 🛡️ Lösung: Cache + Struktur + Tiefenlimit

```math
Fibonacci-Wächter = max_depth + cache_required + duplicate_detection + cost_budget
```

| Maßnahme            | Liara-Entsprechung                        |
| ------------------- | ----------------------------------------- |
| Max Rekursionstiefe | `max_context_rounds=2`, `max_memory_hops=2` |
| Cache Pflicht       | Redis (Session-Cache, kurzfristig)        |
| Duplicate Detection | Deduplizierung vor Kontextaufbau          |
| Cost Budget         | Token-Limit pro Anfrage (Kontextbudget)   |

---

### 🔗 Verbindung zu Chroma Scope

Das `fib(n-1)`-Beispiel im Chroma-Scope-Filtering ist kein Zufall:

> Wenn `fib(n-1)` in Chroma gesucht wird, darf **nicht** `fib(n-2)` aus einem anderen
> Kontext matchen — auch wenn semantisch ähnlich.
>
> Ohne Scope-Filter → falsche Pfad-Assoziationen → explodierende Denkkosten.

Scope-Filter = Fibonacci-Memoization auf Kontextebene.

---

### ⚡ Fazit

```text
Naive KI-Denkkette  →  exponentieller Pfadbaum  →  💥
Liara mit Wächter   →  gecachte, begrenzte Tiefe  →  ✅
```

> Cache + Struktur sind Pflicht.
> Grenzen sind keine Einschränkung — sie sind Architektur.

---

## 🔄 Kontextaufbau

```list
1. Session
2. Fakten
3. Chroma
4. Qdrant
5. Neo4j
6. dedupe
7. gewichten
8. budget
```

---

## 🤖 Worker-System

## LLM Worker

```text
POST /infer
```

---

### Embedding Worker

```text
POST /embed
```

---

## 🔌 Kommunikation

| Verbindung            | Typ   |
| --------------------- | ----- |
| API → Orchestrator    | sync  |
| Orchestrator → Memory | sync  |
| Orchestrator → Tools  | sync  |
| Orchestrator → LLM    | async |

---

## 🧠 Rollenmodell

| Rolle     | Aufgabe          |
| --------- | ---------------- |
| Scout     | Klassifikation   |
| Router    | Entscheidung     |
| Worker    | Generierung      |
| Judge     | Validierung      |
| Archivist | Speicherung      |
| Librarian | Kontextsteuerung |

---

## ⚙️ Routing-Logik

```python
if tool_needed:
    use_tool()
elif need_context:
    scope = resolve_scope(session_id, run_id, file, symbol)
    use_chroma(scope)
elif need_memory:
    use_qdrant()
else:
    use_llm()
```

---

## 🚀 Entwicklungsphasen

### Phase 1 (v0.1.1)

* Postgres
* Redis
* Qdrant
* einfache Context Strategy

---

### Phase 2

* Chroma aktiv
* Embedding Worker
* Scope Resolver + Metadaten-Filter
* Re-Ranking für Kontexttreffer

---

### Phase 3

* Neo4j aktiv
* Graph-Kontext
* adaptive Gewichtung

---

## 🔥 Design-Prinzipien

1. Modelle sind austauschbar
2. Tools sind deterministisch
3. Orchestrator entscheidet
4. Validator ist Pflicht
5. Memory ist mehrschichtig
6. Kontext ≠ Erinnerung

---

## ⚡ Leitprinzip

> Frontend zeigt.
> Orchestrator denkt.
> Memory liefert Kontext.
> LLM generiert.
> Validator prüft.

---

## 🧠 Kurzform

```text
Erkennen → Entscheiden → Kontext holen → Denken → Prüfen → Antworten
```
