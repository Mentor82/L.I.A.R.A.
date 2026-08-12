# 🧠 Liara – ToDo: Validator & Context Integration (v0.1.1)

## 🎯 Ziel

* falsche Antworten erkennen ❌
* unsichere Antworten markieren ⚠️
* Kontext automatisch nachladen 🔄
* System verlässlich machen ✅

## 🧭 Hinweis zur Umsetzung (Code-first)

Diese Datei ist eine **echte Umsetzungs-TODO**.  
Die Punkte unten muessen an den **realen Codepfaden** umgesetzt und mit Tests abgesichert werden.

Status-Legende fuer die technischen Aufgaben:

* `[ ]` offen
* `[-]` in Arbeit
* `[x]` umgesetzt

---

# 🚀 Phase 1 – Sichtbarkeit schaffen (Debug)

## ✅ 1. Context Mode anzeigen

**Ziel:** sehen, wie Liara entscheidet

**ToDo:**

* Ausgabe im CLI ergänzen:

```text
[MODE] NONE / CONTEXT / MEMORY
```

---

## ✅ 2. Context Quellen anzeigen

```text
[CTX] chroma: X
[CTX] qdrant: X
[CTX] postgres: X
```

---

## ✅ 3. Validator Ergebnis anzeigen

```text
[VAL] accept / warn / revise / block
```

---

# 🧪 Phase 2 – Minimaler Validator (MUSS)

## ✅ 4. Fast Check implementieren

**Datei:** `validator/fast_check.py`

Prüfen:

* Antwort leer?
* Länge sinnvoll?
* Format korrekt?

---

## ✅ 5. Consistency Check

**Datei:** `validator/consistency.py`

Prüfen:

* widerspricht sich Text?
* passt Antwort zur Frage?

👉 Quick Hack:

```python
if "keine imaginären" in answer and "komplex" in answer:
    return FAIL
```

---

## ✅ 6. Grounding Check (wichtig!)

**Datei:** `validator/grounding.py`

Prüfen:

* gibt es Kontext?
* wurde Memory genutzt?

```python
if ctx_items == 0 and question_type == "fact":
    return FAIL
```

---

## ✅ 7. Decision Engine

**Datei:** `validator/judge.py`

```python
if grounding == FAIL:
    return "block"

if consistency == FAIL:
    return "revise"

return "accept"
```

---

# 🔄 Phase 3 – Retry-Mechanismus (Gamechanger)

## ✅ 8. Block → Retry mit Context

**Datei:** `orchestrator/executor.py`

```python
if validation == "block":
    force_context = True
    retry_request()
```

---

## ✅ 9. Context erzwingen

```python
if force_context:
    mode = "CONTEXT"
```

---

# 🧠 Phase 4 – Context Strategy (Minimal)

## ✅ 10. einfache Klassifikation

**Datei:** `context_strategy.py`

```python
if "was ist" in input.lower():
    return "FACT"
```

---

## ✅ 11. Context laden

```python
if mode == "FACT":
    ctx = memory.context_search(input)
```

---

# 📦 Phase 5 – Memory Integration (leicht)

## ✅ 12. Dummy Context einbauen

👉 erstmal fake / testweise:

```python
return ["Definition: Gaußsche Zahlen = a + bi mit a,b ∈ Z"]
```

---

## ✅ 13. Context ins Prompt injecten

```python
prompt = f"""
Kontext:
{ctx}

Frage:
{input}
"""
```

---

# 🧪 Phase 6 – Testfälle (sehr wichtig!)

## ✅ 14. Testfragen definieren

```text
Was sind gaußsche Zahlen?
Was ist Ohmsches Gesetz?
Was ist 2+2?
```

---

## ✅ 15. Erwartetes Verhalten

| Fall             | Erwartung |
| ---------------- | --------- |
| kein Kontext     | block     |
| falsche Antwort  | revise    |
| richtige Antwort | accept    |
| unsicher         | warn      |

---

# 🔥 Phase 7 – CLI Verhalten

## ✅ 16. Sichtbares Verhalten

Beispiel:

```text
liara: Was sind gaußsche Zahlen?

[MODE] NONE
[VAL] block ❌

→ retry mit Kontext

[MODE] CONTEXT
[CTX] chroma: 1
[VAL] accept ✅

Antwort:
...
```

---

# 🧠 Phase 8 – Mini-Regeln (sofort wirksam)

## ✅ 17. Faktfragen erkennen

```python
if input.startswith(("was ist", "define", "what is")):
    question_type = "fact"
```

---

## ✅ 18. harte Regel

```python
if question_type == "fact" and ctx_items == 0:
    block()
```

---

# 🏁 Zielzustand

Dein System kann dann:

* ❌ falsche Antworten erkennen
* 🔄 automatisch nachbessern
* 🧠 Kontext gezielt nutzen
* 🛡️ sich selbst korrigieren

---

# ⚡ Kurzfassung

```text
Erkennen → Prüfen → Blocken → Kontext holen → Neu antworten
```

---

# 💬 Real Talk

Wenn du nur diese Schritte umsetzt:

👉 1–3 (Debug)
👉 4–7 (Validator)
👉 8–9 (Retry)

→ hast du schon **80% Verbesserung**

---

# 🚀 Nächster Schritt (optional)

* echter Chroma Anschluss
* echter Qdrant Anschluss
* Confidence Scoring
* Token Budget

---

# 🧠 Merksatz

```text
Nicht bessere Modelle machen das System gut.
Sondern bessere Entscheidungen.
```

---

# 🛠️ Technische Ergaenzung: Konkrete Code-Checkliste

## A) Validator-Kernlogik

### [x] A1. Fast-Check formalisieren

**Zieldatei:** `services/orchestrator/validator.py`

Muss pruefen:

* leere Antwort
* zu kurze / zu lange Antwort
* offensichtlicher Formatfehler (z. B. erwartetes JSON nicht parsebar, wenn JSON-Modus aktiv)

Akzeptanz:

* `decision` wird auf `revise` oder `block` gesetzt
* `checks["length"]` bzw. `checks["fast_check"]` ist nachvollziehbar

---

### [x] A2. Consistency-Check von Platzhalter auf echte Pruefung

**Zieldatei:** `services/orchestrator/validator.py`

Muss pruefen:

* Tool-Output widerspricht Antworttext
* Antwort verneint Treffer, obwohl Tool Treffer liefert (und umgekehrt)
* grobe Selbstwidersprueche im Text

Akzeptanz:

* bei Widerspruch mindestens `revise`, bei schwerem Widerspruch `block`
* Issue-Texte enthalten klaren Grund

---

### [x] A3. Grounding-Check implementieren

**Zieldateien:**

* `services/orchestrator/validator.py`
* `services/orchestrator/orchestrator.py` (Kontext-Metadaten durchreichen)

Muss pruefen:

* faktische Antwort ohne belastbare Evidenz
* Tool-/Memory-Kontext vorhanden, aber nicht genutzt

Akzeptanz:

* Faktfrage + kein Kontext + starke Behauptung => mindestens `warn`, je nach Risiko `block`
* Check-Status in `checks["grounding"]`

---

### [x] A4. Safety/Policy-Minicheck (Baseline)

**Zieldatei:** `services/orchestrator/validator.py`

Muss pruefen:

* offensichtliche Policy-/Safety-Verstoesse
* unzulaessige Offenlegung interner Systemdetails

Akzeptanz:

* Safety-Fail => `decision = block`
* `checks["safety"] = "fail"`

---

## B) Validator-Ergebnis sichtbar machen

### [x] B1. ValidationResult vollstaendig nach oben reichen

**Zieldateien:**

* `services/orchestrator/orchestrator.py`
* `services/api/app.py`

Muss enthalten:

* `decision`
* `checks`
* `issues`
* `confidence_score`

Akzeptanz:

* `/chat` und `/chat/stream` liefern Validator-Details in den Metadaten

---

### [x] B2. CLI-Ausgabe fuer Validatorstatus

**Zieldatei:** `services/cli/main.py`

Muss anzeigen:

* `[VAL] accept|warn|revise|block`
* optional compact: Confidence und Haupt-Issue

Akzeptanz:

* nach jeder Antwort im REPL sichtbar
* bei `warn`/`block` klar markiert

---

## C) Retry und Context-Strategie

### [x] C1. Block/Revise => kontrollierter Retry

**Zieldatei:** `services/orchestrator/orchestrator.py`

Regel:

* `block`: Retry mit erzwungenem Kontext
* `revise`: ein begrenzter Retry mit strengeren Prompt-Instruktionen

Akzeptanz:

* max. Retry-Limit (z. B. 1-2)
* Trace zeigt Erstversuch + Retry

---

### [x] C2. Context-Mode transparent markieren

**Zieldateien:**

* `services/orchestrator/orchestrator.py`
* `services/cli/main.py`

Muss anzeigen:

* `[MODE] NONE|CONTEXT|MEMORY`
* `[CTX]` Quellenzaehler (chroma/qdrant/postgres)

Akzeptanz:

* Debugbar im CLI ohne extra Logs

---

## D) Tests (Pflicht fuer Merge)

### [x] D1. Unit Tests Validator

**Zieldatei:** `tests/unit/test_validator.py`

Faelle:

* source attribution fail
* consistency fail
* grounding fail
* safety fail => block
* confidence-basierte warn/block Grenze

---

### [x] D2. Integrationsnahe Orchestrator-Tests

**Zieldateien:**

* `tests/unit/test_orchestration_split.py`
* ggf. neuer Test: `tests/unit/test_orchestrator_validator_flow.py`

Faelle:

* `decision` wird in API-Metadaten weitergereicht
* retry wird bei block/revise korrekt ausgeloest
* retry-limit verhindert Endlosschleifen

---

## E) Definition of Done (Validator v0.1.1)

`DoD` erreicht, wenn:

* alle Checks (`fast_check`, `consistency`, `grounding`, `safety`) implementiert sind
* `decision/checks` bis CLI sichtbar sind
* Retry-Mechanismus stabil ist
* Tests gruen sind und die kritischen Pfade abdecken

---

## F) CLI Command Priority (Pflicht fuer CLI)

### [x] F1. Slash-Commands vor Orchestrator/LLM abfangen

**Zieldateien:**

* `services/cli/main.py`
* ggf. `services/orchestrator/orchestrator.py`

Regel:

* Eingaben mit fuehrendem `/` werden immer zuerst als Command interpretiert
* keine Weitergabe an LLM
* keine semantische Session-Interpretation

Akzeptanz:

* `/status` wird deterministisch ausgefuehrt
* `/satus` erzeugt `command_error` oder Vorschlag
* kein LLM-Fallback bei Slash-Input

---

### [x] F2. Unknown Command Handler

**Zieldatei:** `services/cli/main.py`

Muss liefern:

* strukturierte Fehlermeldung bei unbekanntem Command
* optional Fuzzy-Vorschlag

Beispiel:

* `Unknown command: /satus`
* `Did you mean: /status?`

Akzeptanz:

* unbekannte Commands fuehren nie zu freier Assistenzantwort

---

### [x] F3. Validator-Regel fuer Command/Response-Mismatch

**Zieldateien:**

* `services/orchestrator/validator.py`
* `tests/unit/test_validator.py`

Regel:

* wenn Input mit `/` beginnt, darf Ausgabe kein freier LLM-Text sein

Akzeptanz:

* Slash-Input + `llm_text` => mindestens `block`
* Testfall deckt diesen Fall ab

---

### [x] F4. Testfaelle fuer CLI-Commands

**Zieldateien:**

* erweiterte `tests/unit/test_cli.py`

Faelle:

* `/status` => command_result
* `/satus` => command_error oder Suggestion
* `/help` => command_result
* Slash-Input wird nicht an LLM weitergereicht

Akzeptanz:

* alle Slash-Pfade deterministisch
* keine semantische Session-Fortsetzung bei Commands

---

## G) Stream-Stabilitaet / Watchdog

### [x] G1. GTK-UI Heartbeat/Watchdog nachziehen

**Zieldateien:**

* `frontend/gtk-ui/src/liara_api.c`
* `frontend/gtk-ui/src/liara_window.c`

Regel:

* `event: heartbeat` aus `/chat/stream` verarbeiten
* Watchdog im GTK-Client (kein Event > N Sekunden => klare UI-Fehlermeldung + Reset)

Akzeptanz:

* lange Streams laufen stabil ohne stilles Haengen
* bei Verbindungsabbruch sichtbarer Status statt unbegrenztem Spinner

