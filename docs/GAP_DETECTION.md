# 🧠 Liara Gap Detection Strategy (v0.1.1 – STRICT)

## 🎯 Ziel

Diese Spezifikation definiert, **wann ein Denkzyklus unvollständig ist**,
**welche Art von Lücke vorliegt** und **welcher Kontext gezielt nachgeladen werden darf**.

Gap Detection ist die zentrale Entscheidungsinstanz für:

* mehrstufiges Reasoning (Phase 3)
* gezieltes Nachladen von Kontext
* Vermeidung unnötiger Iterationen
* Verhinderung von Over-Retrieval
* Stabilisierung der Antwortqualität

---

# ❗ Grundregel

```text
Gap detection must be deterministic and structured.

No vague or implicit "needs more context" decisions are allowed.
```

---

# 🧩 Definition

## Gap

Ein Gap ist eine **konkret identifizierbare fehlende Information**,
die notwendig ist, um die Anfrage korrekt zu beantworten.

---

## Kein Gap

```text
- Unsicherheit ohne konkrete fehlende Information
- Wunsch nach "mehr Details"
- allgemeine Unschärfe ohne klare Ursache
```

---

# 🧠 Gap-Kategorien (STRICT)

Jede Lücke muss genau einer Kategorie zugeordnet werden:

| Typ          | Beschreibung                    | Quelle   |
| ------------ | ------------------------------- | -------- |
| NONE         | kein Gap                        | -        |
| FACT_GAP     | fehlender Fakt                  | Postgres |
| CONTEXT_GAP  | fehlender aktueller Kontext     | Chroma   |
| MEMORY_GAP   | fehlende semantische Erinnerung | Qdrant   |
| RELATION_GAP | fehlende Beziehung / Struktur   | Neo4j    |
| SESSION_GAP  | fehlender aktueller Verlauf     | Redis    |

---

# 🔀 Entscheidungslogik

## Klassifikationsfragen

```text
1. Fehlt ein konkreter Fakt?
2. Fehlt Kontext aus aktueller Diskussion?
3. Fehlt historisches Wissen?
4. Fehlt eine Beziehung oder Abhängigkeit?
5. Fehlt aktueller Session-Zustand?
```

---

# ⚙️ Gap Detection Output (MANDATORY)

Jeder Reasoning-Step muss strukturiert zurückgeben:

```json
{
  "gap_detected": true,
  "gap_type": "RELATION_GAP",
  "missing": [
    "relationship between Memory and Neo4j"
  ],
  "confidence": 0.78,
  "action": "LOAD_RELATIONS"
}
```

---

# 🧠 Gap → Action Mapping

| Gap-Typ      | Aktion         | Quelle   |
| ------------ | -------------- | -------- |
| NONE         | STOP           | -        |
| FACT_GAP     | LOAD_FACTS     | Postgres |
| CONTEXT_GAP  | LOAD_CONTEXT   | Chroma   |
| MEMORY_GAP   | LOAD_MEMORY    | Qdrant   |
| RELATION_GAP | LOAD_RELATIONS | Neo4j    |
| SESSION_GAP  | LOAD_SESSION   | Redis    |

---

# 🔒 Harte Regeln

## Regel 1

```text
Only one primary gap type per iteration.
```

---

## Regel 2

```text
No retrieval without a classified gap.
```

---

## Regel 3

```text
Gap detection must reference specific missing elements.
```

---

## Regel 4

```text
If gap_type == NONE → no further context loading allowed.
```

---

## Regel 5

```text
Repeated identical gaps across iterations must trigger STOP.
```

---

# 🔁 Gap im Reasoning Loop

## Flow

```text
1. Run reasoning step
2. Evaluate output
3. Detect gap
4. If gap:
      map to source
      load context
      continue
5. Else:
      finalize
```

---

# ⚠️ Stop-Bedingungen

## Regel 1

```text
gap_type == NONE → STOP
```

## Regel 2

```text
confidence < MIN_GAP_CONFIDENCE → STOP
```

Example:

```text
MIN_GAP_CONFIDENCE = 0.6
```

---

## Regel 3

```text
Same gap repeated twice → STOP
```

---

## Regel 4

```text
MAX_REASONING_STEPS reached → STOP
```

Example:

```text
MAX_REASONING_STEPS = 3
```

---

# 🧠 Anti-Pattern (VERBOTEN)

```text
- "I need more context"
- "More information required"
- "Not enough data"
```

Ohne konkrete Spezifikation ist das kein valider Gap.

---

# ✅ Gültige Beispiele

## FACT_GAP

```json
{
  "gap_type": "FACT_GAP",
  "missing": ["embedding dimension of Qwen3 model"]
}
```

---

## RELATION_GAP

```json
{
  "gap_type": "RELATION_GAP",
  "missing": ["dependency between Context Engine and Neo4j"]
}
```

---

## MEMORY_GAP

```json
{
  "gap_type": "MEMORY_GAP",
  "missing": ["previous decisions about embedding model"]
}
```

---

# 🧷 Metadaten (MANDATORY)

```json
{
  "reasoning_step": 2,
  "gap_type": "CONTEXT_GAP",
  "confidence": 0.74,
  "trigger": "missing detail for accurate answer"
}
```

---

# 🧠 Minimal-Pseudocode

```python
def detect_gap(result):
    if result.is_sufficient():
        return {"gap_type": "NONE"}

    if missing_fact(result):
        return {"gap_type": "FACT_GAP"}

    if missing_relation(result):
        return {"gap_type": "RELATION_GAP"}

    if missing_memory(result):
        return {"gap_type": "MEMORY_GAP"}

    if missing_context(result):
        return {"gap_type": "CONTEXT_GAP"}

    return {"gap_type": "NONE"}
```

---

# 🔥 Leitprinzip

```text
Only load context if you know exactly what is missing.
```

---

# ⚡ Kurzfassung

```text
Detect gap → classify gap → load exact source → continue
No gap → no retrieval
```
