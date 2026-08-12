# Embedding Query Strategy (Liara)

## Problem

Direktes Embedding des rohen User-Inputs fuehrt zu schlechter Retrieval-Qualitaet.

Beispiel:

User Input: "mach das kuerzer"

Folge:
- Semantisch unklar
- Kein stabiler Kontext
- Retrieval liefert irrelevante Ergebnisse

## Loesung

Embedding wird nicht auf dem rohen User-Input berechnet.

Stattdessen wird eine strukturierte Embedding Query erzeugt.

## Embedding Query Aufbau

```text
embedding_query =
  current_user_input
+ active_topic
+ session_summary
+ current_goal
+ constraints
```

## Beispiel

### Input

User: "mach das kuerzer"

### Session Context

- active_topic: "Liara User Override System"
- session_summary: "System Content, Session-State, Scout, Router, Validator"
- current_goal: "Antwortmodus compact/minimal umsetzen"

### Resultierende Embedding Query

"Liara User Override System Antwortmodus kuerzer minimal compact ohne Erklaerung"

## Pipeline Integration

```text
User Input
   ↓
Session Resolver
   ↓
Embedding Query Builder
   ↓
Embedding
   ↓
Memory Retrieval
   ↓
Scout Scoring
   ↓
Context Builder
   ↓
Prompt Builder
   ↓
LLM
```

## Ziel

- Stabile semantische Suche
- Bessere Relevanz im Retrieval
- Weniger Kontext-Rauschen
- Robust gegenueber kurzen User-Eingaben

## Grundprinzip

Embeddings arbeiten nicht auf dem Wortlaut, sondern auf der Bedeutung der aktuellen Aufgabe.

## Wichtige Regel

Raw User Input soll nie direkt eingebettet werden.

Immer:

User Input -> Kontext anreichern -> Embedding Query

## Vorteile

- Bessere Trefferqualitaet
- Stabilere Sessions
- Weniger Token-Verschwendung
- Konsistentere Antworten
