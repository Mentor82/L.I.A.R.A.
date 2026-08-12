# 🧠 Liara Context Compression Strategy (v0.2 – ADAPTIVE)

## 🎯 Ziel

Diese Spezifikation definiert, wie Kontext zwischen mehreren Denkzyklen **verdichtet**, **ersetzt** und **begrenzt** wird.

Sie verhindert:

* unkontrolliertes Kontextwachstum
* doppelte Informationen
* Retrieval-Kaskaden
* „Fibonacci-Effekt“ durch Iteration
* übergroße Prompts bei mehrstufigem Reasoning

---

# ❗ Grundregel

```text
Iteration must not imply additive context growth.

Each reasoning step must transform context, not accumulate it.
```

---

# 🧩 Grundprinzip

Kontext darf in einer Schleife nicht einfach immer weiter angehängt werden.

## Verboten

```text
context_next = context_prev + new_context
```

## Erlaubt

```text
context_next = compress(context_prev, new_context)
```

---

# 🧱 Zielzustand

Jeder neue Denkzyklus arbeitet mit:

* relevantem Kontext
* dedupliziertem Kontext
* verdichtetem Kontext
* budgetkonformem Kontext

---

# ⚙️ Compression Trigger

Compression ist verpflichtend in folgenden Fällen:

## Trigger 1

```text
A new reasoning step adds additional retrieval results.
```

## Trigger 2

```text
A reasoning step produces a new summary or intermediate artifact.
```

## Trigger 3

```text
The current context exceeds the per-step token budget.
```

## Trigger 4

```text
Duplicate or semantically overlapping context entries exist.
```

---

# 🧠 Compression Ziele

## Ziel 1

```text
Remove duplicates
```

## Ziel 2

```text
Merge semantically equivalent items
```

## Ziel 3

```text
Replace raw retrieval sets with condensed summaries
```

## Ziel 4

```text
Preserve facts, reduce redundancy
```

## Ziel 5

```text
Keep the prompt within hard token budget
```

---

# 🧩 Context-Klassen

## RAW_CONTEXT

Unverdichtete Retrieval-Ergebnisse oder Rohdaten.

Beispiele:

* 5 ähnliche Dokumente
* 3 fast identische Fakten
* mehrere Relationen mit gleicher Aussage

### Regel

```text
RAW_CONTEXT must not be forwarded unchanged across reasoning steps.
```

---

## COMPRESSED_CONTEXT

Verdichteter Arbeitskontext für den nächsten Zyklus.

Beispiele:

* eine Zusammenfassung aus 5 Treffern
* eine bereinigte Faktliste
* eine reduzierte Relationenmenge

### Regel

```text
COMPRESSED_CONTEXT is the preferred input for subsequent reasoning steps.
```

---

## FINAL_CONTEXT

Der endgültige Kontext für den abschließenden Modellaufruf.

### Regel

```text
FINAL_CONTEXT must be compressed, deduplicated, and budget-checked.
```

---

# 🔀 Verdichtungsregeln

## Regel 1 – Deduplikation

```text
Exact duplicates must be removed.
```

---

## Regel 2 – Semantische Deduplikation

```text
Semantically equivalent context entries must be merged.
```

Beispiel:

```text
- "Memory uses Postgres for facts"
- "Facts are stored in Postgres"

→ merged:
"Memory uses Postgres as fact store"
```

---

## Regel 3 – Fact Preservation

```text
Facts must not be dropped if they are unique and relevant.
```

---

## Regel 4 – Replacement over Accumulation

```text
Compressed summaries replace raw source sets.
```

Beispiel:

```text
5 retrieval results
→ 1 compressed summary
→ original 5 results are removed from next-step context
```

---

## Regel 5 – Priority Preservation

Bei Budgetdruck gilt folgende Reihenfolge:

1. Session state
2. Validated facts
3. Current reasoning summary
4. Compressed retrieval memory
5. Relations
6. Raw retrieval fragments

---

# 📏 Token Budget

## Global hard rule

```text
Each reasoning step must operate within a bounded token budget.
```

## Example values

```text
MAX_STEP_CONTEXT_TOKENS = 4000
SAFETY_MARGIN_TOKENS = 1000
```

## Effective budget

```text
USABLE_CONTEXT_TOKENS = MAX_STEP_CONTEXT_TOKENS - SAFETY_MARGIN_TOKENS
```

---

# 🔁 Per-Step Context Policy

## Step 1

```text
Use initial context as selected by Context Strategy Engine.
```

## Step 2+

```text
Do not append raw context.
Use compressed context from previous step plus newly required compressed additions.
```

---

# 🧠 Compression Sources

Compression darf auf folgenden Eingängen arbeiten:

* Redis session artifacts
* Postgres facts
* Chroma retrieval results
* Qdrant retrieval results
* Neo4j relation results
* Reasoning summaries
* Validation results

---

# ❌ Verboten

```text
- raw retrieval accumulation across multiple steps
- storing every intermediate result in the next prompt
- recursive compression loops without stop condition
- forwarding duplicate facts across steps
- keeping raw and compressed versions at the same time
```

---

# 🔒 Stop-Regeln

## Regel 1

```text
If no new information was added, stop iteration.
```

## Regel 2

```text
If compression does not reduce context size meaningfully, stop iteration.
```

## Regel 3

```text
If the reasoning result is already sufficient, stop iteration.
```

## Regel 4

```text
Never exceed MAX_REASONING_STEPS.
```

Example:

```text
MAX_REASONING_STEPS = 3
```

---

# 🧠 Compression Output Schema

Jede Verdichtung muss ein strukturiertes Ergebnis liefern:

```json
{
  "summary": "Memory uses Postgres for facts and Qdrant for semantic retrieval.",
  "facts": [
    "Postgres stores structured facts",
    "Qdrant stores semantic memory"
  ],
  "relations": [
    "Memory -> uses -> Postgres",
    "Memory -> uses -> Qdrant"
  ],
  "dropped_items": 4,
  "token_estimate": 320
}
```

---

# 🧷 Metadaten (MANDATORY)

Jeder komprimierte Kontextblock muss Metadaten tragen:

```json
{
  "source": "compression_layer",
  "compression_level": "step_summary",
  "input_items": 6,
  "output_items": 3,
  "reasoning_step": 2,
  "validation_status": "derived"
}
```

---

# 🧠 Minimal-Pseudocode

```python
def compress_context(previous_context, new_context):
    merged = merge(previous_context, new_context)
    deduped = remove_duplicates(merged)
    reduced = merge_semantic_equivalents(deduped)
    prioritized = apply_priority_rules(reduced)
    bounded = enforce_token_budget(prioritized)

    return bounded
```

---

# 🔥 Leitprinzip

```text
Context may evolve.
Context must not explode.
```

---

# ⚡ Kurzfassung

```text
Do not append context.
Compress context.
Replace raw sets with summaries.
Enforce token budget every step.
Prefer replacement over accumulation.
```

---

# 🔄 Adaptive β + Context Pressure (v0.2)

> Erweiterung von v0.1.1: Das statische Token-Budget wird durch ein druckgesteuertes,
> schrittabhängiges Budget ersetzt.

---

## Motivation

Attention-Kosten skalieren quadratisch mit der Sequenzlänge:

$$\text{Cost} \sim O(n^2)$$

Ein Token im Step 1 kostet weniger als ein Token in Step 3 — weil der Kontext gewachsen ist.
Ein statischer Cutoff ignoriert das. Adaptives β bildet es ab.

---

## Context Pressure P

$$P = \frac{\text{current\_tokens}}{\text{MAX\_STEP\_CONTEXT\_TOKENS}} \in [0, 1]$$

| P-Wert | Bedeutung | Aktion |
|--------|-----------|--------|
| `< 0.5` | entspannt | Kompression relaxiert, mehr RAW_CONTEXT toleriert |
| `0.5 – 0.8` | moderat | Standardkompression |
| `> 0.8` | Druck | aggressivere Kompression, niedrigere β-Schwelle |
| `= 1.0` | kritisch | Hard floor, nur Prio 1–3 behalten |

---

## Token-Retentions-Faktor β

β gewichtet, wie viel vom Budget pro Step verfügbar ist.
Er sinkt mit jedem weiteren Reasoning-Step (mehr Kontext bereits akkumuliert):

$$\beta(s) = \max(0.5,\ 1.0 - 0.15 \times (s - 1))$$

| Step s | β |
|--------|---|
| 1 | 1.00 |
| 2 | 0.85 |
| 3 | 0.70 |
| 4 | 0.55 |
| ≥ 5 | 0.50 |

---

## Adaptives Budget

Das effektive Budget pro Step kombiniert Basis-Budget, β und Pressure:

$$\text{budget\_adaptive}(s) = \max(128,\ \lfloor \text{base\_budget} \times \beta(s) \times (1 - 0.3 \times P_{\text{smoothed}}) \rfloor)$$

Dabei gilt:
- `base_budget = MAX_STEP_CONTEXT_TOKENS − SAFETY_MARGIN_TOKENS`
- Pressure-Faktor: reduziert Budget um bis zu 30 % bei vollem Druck
- Hard floor: 128 Tokens — darunter wird nie gekürzt

---

## Hysterese (Dämpfung)

Ohne Dämpfung kann der Regler oszillieren:

```
Druck hoch → compress → Druck sinkt → compress relaxiert → Druck steigt → ...
```

Lösung: exponentieller gleitender Mittelwert (EMA) über `token_estimate` der letzten Steps:

$$P_{\text{smoothed}}(s) = \alpha \cdot P(s) + (1 - \alpha) \cdot P_{\text{smoothed}}(s{-}1), \quad \alpha = 0.4$$

`P_smoothed` wird für die Budget-Berechnung verwendet, nicht das rohe `P(s)`.

---

## Implementierungs-Touchpoints

| Datei | Änderung |
|-------|----------|
| `services/orchestrator/context_compression.py` | `usable_budget` statisch → `_adaptive_budget(reasoning_step, P_smoothed)` |
| `services/orchestrator/context_compression.py` | `_pressure_ema` als Instanz-State für EMA-Dämpfung |
| `services/orchestrator/orchestrator.py` | Keine Änderung — `reasoning_step` wird bereits übergeben |

---

## Pseudocode

```python
def _compute_pressure(self, current_tokens: int) -> float:
    return min(1.0, current_tokens / self.max_step_context_tokens)

def _adaptive_budget(self, reasoning_step: int, p_smoothed: float) -> int:
    base = self.max_step_context_tokens - self.safety_margin_tokens
    beta = max(0.5, 1.0 - 0.15 * (reasoning_step - 1))
    pressure_factor = 1.0 - (0.3 * p_smoothed)
    return max(128, int(base * beta * pressure_factor))

# In compress():
raw_tokens = self._count_tokens("\n".join(merged_items))
p_raw = self._compute_pressure(raw_tokens)
self._pressure_ema = 0.4 * p_raw + 0.6 * self._pressure_ema  # EMA α=0.4
usable_budget = self._adaptive_budget(reasoning_step, self._pressure_ema)
```

---

## Stop-Regel-Erweiterung (Regel 2b)

Ergänzung zu bestehender Stop-Regel 2:

```text
Regel 2b:
If adaptive_budget(step+1) < adaptive_budget(step) AND no_new_information=True,
stop iteration immediately.
```

Das verhindert sinnlose weitere Steps, wenn Budget schrumpft ohne Informationsgewinn.
