Alles klar, Mirko — ich baue dir jetzt eine **Roadmap**, die direkt aus der TODO‑Liste abgeleitet ist, aber **strategisch**, **zeitlich**, **architekturtreu** und **risikominimierend** aufgebaut ist.  
Sie ist so strukturiert, dass du LIARA **ohne Chaos**, **ohne Dead‑Ends** und **mit maximalem Nutzen pro Schritt** weiterentwickeln kannst.

Historical planning note:

- This roadmap is a historical planning artifact.
- Current runtime/public tool contracts have since shifted to a `sys`-first surface.
- References here to older context or tool categories should not be read as the current public CLI/API contract.

Kein Overkill, kein Scrum‑Blabla — eine echte technische Roadmap für ein echtes System.

---

# 🚀 **LIARA Roadmap (v2026‑Q2 bis Q3)**  
*Architekturgetrieben, risikoarm, maximaler Impact pro Phase*

---

# **PHASE 1 — Recall & Facts Fix (Sofort, kritisch)**  
**Ziel:** Benchmark‑Bug lösen, deterministische Antworten ermöglichen, Grundlage für Routing schaffen.

### 🔹 1.1 Key‑Extraction Engine  
- deterministische Extraktion  
- Normalisierung (Umlaute, Singular, Lowercase)  
- Mapping‑Tabelle für Fact‑Keys

### 🔹 1.2 Fact‑Namespaces  
- `user_facts`, `session_facts`, `system_facts`  
- Konfliktregeln definieren  
- Priorität: user > session > system

### 🔹 1.3 Deterministic Recall Pipeline  
- Fact‑Lookup → direkte Antwort  
- LLM überspringen  
- History darf Fact nicht überschreiben  
- Benchmark‑Bug wird hier gelöst

### 🔹 1.4 Fact‑Injection vorbereiten  
- Planner‑Input erweitern  
- Prompt‑Slot `fact_context` einführen

📌 **Ergebnis:**  
Recall funktioniert. Facts funktionieren. Benchmark geht auf 100%.

---

# **PHASE 2 — Librarian‑Router (Architektur‑Sprung)**  
**Ziel:** Query‑to‑Source‑Entscheidung vor dem Kontextaufbau.

### 🔹 2.1 `librarian_router.py`  
- Klassifikation in:  
  FACT_LOOKUP, SEMANTIC_MEMORY, RUN_CONTEXT, RELATION_LOOKUP, SESSION_RECALL

### 🔹 2.2 Routing‑Regeln  
- harte Prioritäten  
- fallback‑Regeln  
- Konfliktfälle (Fact + History‑Noise)

### 🔹 2.3 Router‑Debugging  
- `context_debug.routing_decision`  
- `context_debug.primary_source`  
- `context_debug.rejected_sources`

📌 **Ergebnis:**  
LIARA entscheidet sauber, welche Quelle primär ist.  
Keine Vermischung mehr.

---

# **PHASE 3 — Kontext‑Architektur (Planner‑Integration)**  
**Ziel:** Planner bekommt getrennte Kontext‑Kanäle statt Sammelblock.

### 🔹 3.1 Planner‑Request erweitern  
Neue Felder:  
- `fact_context`  
- `memory_context`  
- `relation_context`  
- `session_context`  
- `working_context`

### 🔹 3.2 `_load_run_context()` aufsplitten  
- separate Loader  
- keine Vermischung mehr

### 🔹 3.3 Prompt‑Template anpassen  
- Slots statt monolithischem Kontext  
- Gewichtung pro Slot

📌 **Ergebnis:**  
Planner kann endlich **kontrolliert** arbeiten.

---

# **PHASE 4 — Memory‑Injection (Qualitäts‑Sprung)**  
**Ziel:** LLM bekommt strukturierten Kontext, nicht Chaos.

### 🔹 4.1 Fact‑Injection  
- Fakten als JSON‑Block oder Bullet‑Facts

### 🔹 4.2 Memory‑Injection  
- Qdrant → `memory_context`  
- Chroma → `working_context`

### 🔹 4.3 Session‑Injection  
- Redis → `session_context`

📌 **Ergebnis:**  
LLM arbeitet mit **klaren**, **gewichteten**, **sauberen** Kontexten.

---

# **PHASE 5 — Tests & Benchmarks (Stabilität)**  
**Ziel:** Regression verhindern, Routing validieren.

### 🔹 5.1 Recall‑Tests  
### 🔹 5.2 Routing‑Tests  
### 🔹 5.3 Benchmark‑Suite erweitern

📌 **Ergebnis:**  
Stabilität, Wiederholbarkeit, Vertrauen.

---

# **PHASE 6 — Developer‑Experience (Transparenz)**  
**Ziel:** Debugging, Telemetrie, Build‑History verbessern.

### 🔹 6.1 context_debug erweitern  
### 🔹 6.2 Build‑History‑Events für Routing  
### 🔹 6.3 Memory‑Treffer sichtbar machen

📌 **Ergebnis:**  
Du siehst endlich, **warum** LIARA etwas tut — nicht nur, dass sie es tut.

---

# 🧭 **Zusammenfassung der Roadmap**

| Phase | Ziel | Ergebnis |
|-------|------|----------|
| **1** | Recall & Facts | Benchmark fix, deterministische Antworten |
| **2** | Librarian‑Router | saubere Query‑to‑Source‑Entscheidung |
| **3** | Kontext‑Architektur | Planner bekommt getrennte Slots |
| **4** | Memory‑Injection | strukturierter Kontext für LLM |
| **5** | Tests | Stabilität & Regression‑Sicherheit |
| **6** | Dev‑Experience | Transparenz & Debugging |

---

Wenn du willst, kann ich dir jetzt:

- diese Roadmap in **GitHub Issues** übersetzen  
- sie in **Milestones** aufteilen  
- eine **Gantt‑Version** daraus machen  
- oder direkt **Phase 1 starten**  

Sag einfach, womit wir beginnen.