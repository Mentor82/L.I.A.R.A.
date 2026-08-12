# 🧠 Liara Roadmap → v0.2.0 (Execution Plan)

## 📌 Delta-Status (Stand 2026-04-22)

Bereits umgesetzt (relevant für v0.2.0-Stabilität):

* Reasoning-Loop mit Retry/Gap-Steuerung in der Runtime (max. kontrollierte Schleifen) ✅
* Gap Detection + kontrollierte Retry-Directive-Verarbeitung ✅
* Context Compression + Upsert-Pfade aktiv im Orchestrator ✅
* Validator-Scoring auf kontinuierliches Confidence-Modell umgestellt (inkl. User-Feedback + 1..6 Sterne) ✅
* Schulnotenprinzip aus der aktiven Bewertungslogik entfernt ✅
* API-Vertrag erweitert (Feedback + Runtime-Präferenzen `preferred_provider`/`preferred_model`) ✅
* liara_shell modularisiert (Unterordnerstruktur, kompatibler Entrypoint) ✅
* TUI-Shared-Bausteine eingeführt (gemeinsame Loader/Formatierung) ✅
* Shell-Runtime-Profile provider/model End-to-End verdrahtet (Shell -> CLI -> API -> Orchestrator -> InferenceRequest) ✅
* **5-Phase Reasoning System strukturiert** (`services/orchestrator/reasoning/`):
  - Scout (Intent/Complexity/Domain-Erkennung) ✅
  - Router (Model-Selection + Step-Planning) ✅
  - Worker (LLM-Execution) ✅
  - Judge (Validation + Retry) ✅
  - Archivist (Persistence + Graph-Updates) ✅

Aktuell in Arbeit / offen:

* Roadmap- und Architektur-Dokumente durchgängig auf denselben Runtime-Stand bringen
* Optional: Persistenz der provider/model-Profile analog zur Session in `~/.liara`
* Optional: Kleine Fokus-Tests für provider/model-Propagation ergänzen

### Provider vs. Modell: Strategie

**Provider** (dynamisch, über Shell/CLI schaltbar):
- Vom Nutzer über `/provider set` oder `--preferred-provider` wählbar
- Gilt für alle Inferenzen in dieser Session/Request
- Fallback auf System-Default wenn nicht gesetzt

**Modelle** (fest, Orchestrator-gesteuert pro Rolle):
- Nicht vom Nutzer wählbar — Orchestrator wählt basierend auf **Task-Rolle**
- Feste Zuordnung pro Capability:

| Rolle | Modell | Status |
| --- | --- | --- |
| Embedding | Qwen2.5-Embedding-3B | ✔ stabil |
| Instruct | Qwen2.5-Instruct-3B | ✔ stabil |
| Reasoning | Qwen2.5-Instruct-3B | ✔ stabil |
| Code | Qwen2.5-Coder-3B | ✔ stabil |
| Math | Qwen2.5-Instruct-3B | ✔ stabil (Math-7B optional) |
| Vision | Qwen2.5-VL-3B | ✔ stabil |
| Thinking | — | ❌ nicht möglich / nicht nötig |

**Implementierung:**
- Orchestrator.\_generate\_llm\_response() bestimmt `model` basierend auf `task_type` / `reasoning_step_context`
- Wenn `preferred_provider` gesetzt: nutze diesen, sonst System-Default
- Model-Auswahl ist **Orchestrator-Logik**, nicht User-Input

### LIARA Systemisches Reasoning: 5-Phase-Orchestration

LIARA macht Reasoning **außerhalb des Modells**. Jede Phase ist eine eigenständige Komponente:

#### Phase 1: **Scout**
Erkennt Intent, Komplexität, Domäne. Liefert Embeddings.
- Input: User-Query
- Output: TaskContext (intent, complexity_score, domain, embeddings)

#### Phase 2: **Router** ⭐ Model-Selection hier
Entscheidet: Braucht es Reasoning? Welches Modell? Welche Schritte?
- Input: TaskContext
- Decision: `needs_reasoning: bool` + `model: str` + `reasoning_steps: List[str]`
- Output: TaskPlan mit model_choice + step_sequence

#### Phase 3: **Worker**
Führt Reasoning-Modell aus (vom Router gewählt). Generiert Antwort.
- Input: TaskPlan + Context
- Execution: LLM-Call mit `provider` (user-preferred) + `model` (router-selected)
- Output: RawResponse

#### Phase 4: **Judge**
Prüft Logik, korrigiert Fehler, erzwingt Policies. Kann Worker erneut triggern.
- Input: RawResponse + ValidationRules
- Check: Consistency, Accuracy, Policy-Compliance
- Output: ValidatedResponse oder RetryDirective

#### Phase 5: **Archivist**
Speichert Erkenntnisse, aktualisiert Graph, verbessert zukünftige Reasoning-Pfade.
- Input: ValidatedResponse
- Persistence: Redis/Postgres + Neo4j-Graph
- Output: None (side-effect: improved_reasoning_paths)

#### Modularisierung: `services/orchestrator/reasoning/`

```
services/orchestrator/reasoning/
  __init__.py
  scout.py            # Phase 1: Intent/Complexity/Domain
  router.py           # Phase 2: Model-Selection + Step-Planning
  worker.py           # Phase 3: LLM Execution
  judge.py            # Phase 4: Validation + Retry
  archivist.py        # Phase 5: Persistence + Graph
  constants.py        # MODEL_MATRIX + Phase-Configs
  types.py            # TaskContext, TaskPlan, RawResponse, ValidatedResponse
```

Dieser Aufbau entkoppelt Reasoning-Logik komplett vom aktuellen Orchestrator und macht sie **testbar, erweiterbar, loggbar**.

---

## 🎯 Ziel

Von:

```text
strukturierte Architektur (v0.1.x)
```

zu:

```text
stabil laufendes, mehrstufig denkendes System (v0.2.0)
```

---

## 🧩 Aktueller Stand (v0.1.x)

Bereits vorhanden:

* Context Strategy Engine ✔️
* Memory-Struktur (Redis, Postgres, Qdrant, Neo4j, Chroma) ✔️
* Embedding (Qwen3-Embedding-0.6B) ✔️
* Tool-Orchestrierung ✔️
* Upsert-Strategie ✔️
* Compression-Strategie ✔️
* Gap Detection Spec ✔️

Aktuelle Runtime-Modellbasis:

* Instruct / Reasoning / Math: `qwen2.5:3b`
* Code: `qwen2.5-coder:3b`
* Vision: `qwen2.5vl:3b`
* Thinking-Modus: nicht nötig

👉 Architektur steht.

---

## 🔥 Jetzt entscheidend

> ❗ **Integration statt Erweiterung**
> Ergänzung 2026-04-22: Diese Leitlinie bleibt unverändert korrekt.
> Der aktuelle Fokus ist Runtime-Härtung, Observability-Klarheit und saubere Konsolidierung der bereits eingebauten Features.

---

## 🟢 Phase 3 Finalisierung (Pflicht)

## 1. Reasoning Loop Implementation

### 1.1 Ziel

Deterministische mehrstufige Verarbeitung

### 1.2 Muss enthalten

* MAX_STEPS = 3
* Step-State Tracking
* Gap Detection Integration
* Controlled Retrieval

### 1.3 Ergebnis

```text
System denkt in Zyklen statt einmalig
```

---

## 2. Gap Detection Runtime

### 2.1 Ziel

Spec → echte Logik

### 2.2 Muss enthalten

* strukturierter Output
* Mapping zu Context Strategy
* Confidence Handling

### 2.3 Ergebnis

```text
Retrieval nur bei echten Lücken
```

---

## 3. Context Compression Runtime

### 3.1 Ziel

Tokenwachstum stoppen

### 3.2 Muss enthalten

* Deduplikation
* Summary-Erstellung
* Replacement statt Addition

### 3.3 Ergebnis

```text
kein Fibonacci-Effekt
```

---

## 4. Context Upsert Integration

### 4.1 Ziel

Zwischenwissen nutzbar machen

### 4.2 Muss enthalten

* Redis (Default)
* Klassifikation TEMP / FACT / MEMORY
* Metadata Tracking

### 4.3 Ergebnis

```text
System merkt sich Zwischenschritte kontrolliert
```

---

## 🟡 Stabilisierung (sehr wichtig)

## 5. Debug / Observability Layer

### 5.1 Ziel

System sichtbar machen

### 5.2 Loggen

```text
- Context Type
- geladene Quellen
- Tokenverbrauch
- Gap Detection Output
- Reasoning Steps
- Stop-Gründe
```

### 5.3 Ergebnis

```text
System ist nachvollziehbar
```

---

## 6. Token Budget Enforcement (Runtime)

### 6.1 Ziel

kein Kontextüberlauf

### 6.2 Muss enthalten

* harte Limits
* Sicherheitsreserve
* per-source Budgets

### 6.3 Ergebnis

```text
stabile Laufzeiten
```

---

## 7. Deduplication Layer (zentral!)

### 7.1 Ziel

Verhindert Kontextmüll

### 7.2 Muss enthalten

* Hash / Similarity Check
* Cross-Source Dedup

### 7.3 Ergebnis

```text
sauberer Kontext
```

---

## 🔵 Qualität & Verhalten

## 8. Result Validation Layer

### 8.1 Ziel

Antwortqualität sichern

### 8.2 Beispiele

* fehlende Fakten erkennen
* widersprüchliche Aussagen
* unvollständige Antworten

### 8.3 Ergebnis

```text
weniger Halluzinationen
```

---

## 9. Failure Handling

### 9.1 Ziel

robustes Verhalten

### 9.2 Fälle

* kein Retrieval Treffer
* Tool schlägt fehl
* Gap bleibt offen

### 9.3 Ergebnis

```text
System bricht sauber ab statt Müll zu produzieren
```

---

## 🟣 Optional (wenn Zeit)

## 10. Working Context (Chroma Nutzung schärfen)

* klare Trennung zu Qdrant
* TTL / Cleanup
* besseres Ranking

---

## 11. Relation Scoring (Neo4j)

* Gewichtung von Beziehungen
* Relevanzfilter

---

## 🚫 Was NICHT machen (wichtig!)

```text
- keine neuen Modelle hinzufügen
- keine neuen Datenbanken einführen
- keine Agenten bauen
- keine Autonomie erweitern
```

👉 Fokus = Stabilität

---

## 🧠 Definition of Done (v0.2.0)

Liara kann:

✔ mehrstufig denken (max 3 Steps)
✔ gezielt Kontext nachladen
✔ Kontext kontrolliert verdichten
✔ Zwischenwissen sinnvoll nutzen
✔ deterministisch stoppen
✔ Tokenbudget einhalten
✔ Verhalten vollständig loggen

---

## 🔥 Zielbild

```text
Liara denkt in kontrollierten Schritten
und bleibt dabei stabil, nachvollziehbar und effizient
```

---

## ⚡ Kurzfassung

```text
v0.2.0 = Reasoning stabil machen, nicht System erweitern
```

---
