# LIARA Pipeline (As-Is Runtime)

┌──────────────────────────────────────────────────────────────────────────┐
│                                USER INPUT                                │
│                     (Text, Bild, Code, Frage, Task)                      │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                  SCOUT                                   │
│                  (Qwen3-Embedding-0.6B + Intent-Classifier)              │
│                                                                          │
│  - erzeugt Embeddings                                                    │
│  - erkennt Intent (chat, tech, math, code, vision, planning, safety)     │
│  - erkennt Modalität (text, image, code)                                 │
│  - berechnet complexity_score                                            │
│  - liefert:                                                              │
│        intent                                                            │
│        modality                                                          │
│        complexity_score                                                  │
│        embedding_vector                                                  │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                  ROUTER                                  │
│                        (Entscheidungslogik + Policies)                   │
│                                                                          │
│  - wählt Modell basierend auf:                                           │
│        intent                                                            │
│        complexity_score                                                  │
│        modality                                                          │
│        policies (Kosten, Sicherheit, Ressourcen)                         │
│                                                                          │
│  - Modellwahl (Qwen2.5-Familie):                                         │
│        chat      → qwen2.5:3b                                            │
│        tech      → qwen2.5:3b                                            │
│        reasoning → qwen2.5:3b                                            │
│        math      → qwen2.5:3b                                            │
│        code      → qwen2.5-coder:3b                                      │
│        vision    → qwen2.5vl:3b                                          │
│        rag       → Qwen3-Embedding-0.6B (für Retrieval)                  │
│                                                                          │
│  - baut Kontext-Bundle (RAG)                                             │
│  - entscheidet über Multi-Step-Plan                                      │
│  - übergibt Task + Kontext an Worker                                     │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                  WORKER                                  │
│                        (Ausgewähltes Qwen2.5-Modell)                     │
│                                                                          │
│  - führt Inference aus                                                   │
│  - generiert Antwort                                                     │
│  - optional: reasoning (modellintern)                                    │
│  - KEIN Thinking-Modus nötig (systemseitig abgedeckt)                    │
│                                                                          │
│  Beispiele:                                                              │
│        qwen2.5:3b        → Chat, Reasoning, Math                         │
│        qwen2.5-coder:3b  → Code-Analyse                                  │
│        qwen2.5vl:3b      → Vision                                        │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                   JUDGE                                  │
│                        (Validierung + Policy Enforcement)                │
│                                                                          │
│  - prüft Antwort                                                         │
│  - korrigiert bei Bedarf                                                 │
│  - erzwingt Policies (Sicherheit, Stil, Korrektheit)                     │
│  - kann Worker erneut triggern                                           │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                 ARCHIVIST                                │
│                         (Speicherung + Memory Layers)                    │
│                                                                          │
│  - speichert Embeddings                                                  │
│  - speichert Chunks                                                      │
│  - speichert Kontext                                                     │
│  - aktualisiert Wissensgraph (Neo4j)                                     │
│  - aktualisiert Langzeit-Embeddings (Qdrant)                             │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                                FINAL OUTPUT                              │
│                          (Antwort an den Benutzer)                       │
└──────────────────────────────────────────────────────────────────────────┘

## Memory-Zuordnung im Arbeitsablauf

### Merksatz fuer Liara

Redis denkt gerade. Chroma erinnert kurzfristig. Qdrant erinnert dauerhaft. Neo4j erklaert, was zusammenhaengt - aber mit Ablaufdatum pro Beziehung.

1. Redis ist workflowbezogenes Arbeitsgedächtnis.
2. Chroma ist systemweites Kurzzeitgedächtnis (Working Context über Sessions hinweg, aber nicht dauerhaft).
3. Qdrant ist persistentes Langzeitgedächtnis für semantische Erinnerung.
4. Neo4j hält allgemeine Beziehungen (struktureller Graph-Kontext).

### Lebensdauer-Regeln (wichtig)

- Redis-Einträge leben nur so lange wie Session/Arbeitsablauf (TTL-basiert).
- Chroma-Einträge gelten als Kurzzeitkontext und dürfen nur so lange gültig sein, wie sie im Kurzzeitbereich gehalten werden.
- Qdrant bleibt persistent und ist die langfristige semantische Ebene.
- Neo4j-Beziehungen zu Redis-/Kurzzeit-Artefakten sind nur während der aktiven Session gültig.
- Wenn Kurzzeitkontext aus Redis/Chroma ausläuft oder entfernt wird, müssen die korrespondierenden "kurzlebigen" Neo4j-Kanten ebenfalls als abgelaufen behandelt oder bereinigt werden.

### Praktische Interpretation

- "Jetzt relevant" -> Redis + Chroma
- "Später wieder nutzbar" -> Qdrant
- "Wie hängt etwas zusammen" -> Neo4j

## Expliziter Cleanup-Trigger fuer Relation-Lifecycle

Der Cleanup ist bewusst nicht implizit, sondern als expliziter Trigger aufgebaut:

- Einstieg ueber LIARA API: POST /memory/relations/cleanup-expired
- Memory API Route: POST /relations/cleanup-expired
- Ziel: abgelaufene, kurzlebige Neo4j-Kanten (ephemeral + valid_until_ts <= now_ts) entfernen

### Ablauf (Sequenz)

1. Client/Admin ruft den Trigger in der LIARA API auf.
2. LIARA API leitet in service mode an die Memory API weiter.
3. Memory Store fuehrt relation_cleanup_expired aus.
4. GraphStore entfernt abgelaufene ephemere Kanten bis zum gesetzten Limit.
5. Response liefert removed plus Status-Metadaten (Scope, now_ts).

```mermaid
sequenceDiagram
    autonumber
    participant C as Client or Admin
    participant A as LIARA API (8010)
    participant M as Memory API (8020)
    participant S as Memory Store
    participant G as GraphStore (Neo4j)

    C->>A: POST /memory/relations/cleanup-expired
    A->>M: POST /relations/cleanup-expired
    M->>S: relation_cleanup_expired(request)
    S->>G: relation_cleanup_expired(now_ts, scope, limit)
    G-->>S: removed count
    S-->>M: RelationCleanupExpiredResponse
    M-->>A: removed + status
    A-->>C: 200 OK
```

### Scope- und Betriebslogik

- now_ts optional: wenn nicht gesetzt, verwendet der Service die aktuelle Zeit.
- session_id und run_id optional: Cleanup kann gezielt pro Session/Run eingeschraenkt werden.
- limit begrenzt die Bereinigung pro Aufruf, um Lastspitzen zu vermeiden.
- In nicht-service mode fuehrt die LIARA API den Cleanup direkt ueber BackedMemoryServiceStore aus.

## Governance-Schicht (Promotion / Decay / Cleanup)

Die Lifecycle-Regeln sind jetzt als explizite Governance-Schicht im Memory-Service umgesetzt.

### Phasen

1. Scope-Link: Kurzzeitkontext wird in Neo4j als ephemere PART_OF-Kante mit TTL/Expiry verknuepft.
2. Promotion: Candidate-/Validated-Kontext kann nach Qdrant promoted werden.
3. Pattern Learning: Wiederkehrende Inhalte werden session-uebergreifend als Muster aggregiert.
4. Cleanup: Abgelaufene ephemere Kanten werden explizit ueber den Cleanup-Trigger entfernt.

### Konfigurierbare Schalter

- MEMORY_GOVERNANCE_ENABLED=true
- MEMORY_GOVERNANCE_SCOPE_LINK_ENABLED=true
- MEMORY_GOVERNANCE_PROMOTION_ENABLED=true
- MEMORY_GOVERNANCE_CLEANUP_ENABLED=true
- MEMORY_GOVERNANCE_PATTERN_LEARNING_ENABLED=true

### Judge-Gates

- MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION=false
- MEMORY_GOVERNANCE_CLEANUP_REQUIRE_JUDGE=false
- MEMORY_GOVERNANCE_JUDGE_MIN_CONFIDENCE=0.55

Interpretation:

- Promotion kann optional einen Judge-Entscheid (allow/pass/ok) und Mindest-Confidence erzwingen.
- Cleanup kann optional nur bei positivem Judge-Signal ausgefuehrt werden.

### Relevance-Schwellen

- MEMORY_PROMOTION_THRESHOLD_CANDIDATE=0.82
- MEMORY_PROMOTION_THRESHOLD_VALIDATED=0.92
- MEMORY_REASONING_RELEVANCE_WEIGHT=0.35
- MEMORY_PATTERN_RELEVANCE_BONUS=0.03

Interpretation:

- Candidate-Promotion nutzt den Candidate-Threshold.
- Validated-Faelle ohne Candidate-State nutzen den Validated-Threshold.
- reasoning_relevance wird mit relevance_score gewichtet zusammengefuehrt.
- wiederkehrende session-uebergreifende Muster koennen einen kleinen Relevance-Bonus geben.

### Verhalten bei deaktivierter Cleanup-Phase

Wenn MEMORY_GOVERNANCE_CLEANUP_ENABLED=false ist, antwortet der Cleanup-Aufruf mit einem Policy-Hinweis statt Loeschung:

- removed=0
- status.error=relation_cleanup_disabled_by_policy

Zusatz bei Judge-Block:

- removed=0
- status.error=relation_cleanup_disabled_by_policy
- status.metadata.governance_reason=cleanup_judge_gate_blocked oder cleanup_judge_confidence_below_min

## Ops-Runbook: Governance-Profile

Die folgenden Profile koennen direkt als Startpunkt fuer Umgebungen genutzt werden.

### 1) dev (schnell testen, weniger Persistenzdruck)

Empfohlen fuer lokale Entwicklung und Feature-Iteration.

- MEMORY_GOVERNANCE_ENABLED=true
- MEMORY_GOVERNANCE_SCOPE_LINK_ENABLED=true
- MEMORY_GOVERNANCE_PROMOTION_ENABLED=true
- MEMORY_GOVERNANCE_CLEANUP_ENABLED=true
- MEMORY_GOVERNANCE_PATTERN_LEARNING_ENABLED=true
- MEMORY_PROMOTION_THRESHOLD_CANDIDATE=0.90
- MEMORY_PROMOTION_THRESHOLD_VALIDATED=0.97
- MEMORY_REASONING_RELEVANCE_WEIGHT=0.25
- MEMORY_PATTERN_RELEVANCE_BONUS=0.01
- RELATION_EPHEMERAL_TTL_SECONDS=1200

Wirkung:

- Promotion passiert selten (nur sehr relevante Kandidaten).
- Ephemere Kanten werden schnell bereinigt.
- Pattern-Bonus ist klein, dadurch wenig Recall-Drift.

### 2) conservative prod (stabil, risikoarm)

Empfohlen fuer produktionsnahe Systeme mit Fokus auf kontrollierte Langzeitpersistenz.

- MEMORY_GOVERNANCE_ENABLED=true
- MEMORY_GOVERNANCE_SCOPE_LINK_ENABLED=true
- MEMORY_GOVERNANCE_PROMOTION_ENABLED=true
- MEMORY_GOVERNANCE_CLEANUP_ENABLED=true
- MEMORY_GOVERNANCE_PATTERN_LEARNING_ENABLED=true
- MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION=true
- MEMORY_GOVERNANCE_CLEANUP_REQUIRE_JUDGE=true
- MEMORY_GOVERNANCE_JUDGE_MIN_CONFIDENCE=0.70
- MEMORY_PROMOTION_THRESHOLD_CANDIDATE=0.85
- MEMORY_PROMOTION_THRESHOLD_VALIDATED=0.93
- MEMORY_REASONING_RELEVANCE_WEIGHT=0.30
- MEMORY_PATTERN_RELEVANCE_BONUS=0.02
- RELATION_EPHEMERAL_TTL_SECONDS=3600

Wirkung:

- Solider Mittelweg zwischen Recall und False-Positive-Promotion.
- Cleanup bleibt aktiv und haelt Neo4j-Sessionkanten sauber.
- Judge-Gates reduzieren ungewollte Promotion/Cleanup-Entscheidungen.

### 3) aggressive learning (mehr Recall, mehr Wachstum)

Empfohlen fuer explorative Lernphasen mit erhoehtem Speicherwachstum.

- MEMORY_GOVERNANCE_ENABLED=true
- MEMORY_GOVERNANCE_SCOPE_LINK_ENABLED=true
- MEMORY_GOVERNANCE_PROMOTION_ENABLED=true
- MEMORY_GOVERNANCE_CLEANUP_ENABLED=true
- MEMORY_GOVERNANCE_PATTERN_LEARNING_ENABLED=true
- MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION=false
- MEMORY_GOVERNANCE_CLEANUP_REQUIRE_JUDGE=false
- MEMORY_PROMOTION_THRESHOLD_CANDIDATE=0.70
- MEMORY_PROMOTION_THRESHOLD_VALIDATED=0.82
- MEMORY_REASONING_RELEVANCE_WEIGHT=0.55
- MEMORY_PATTERN_RELEVANCE_BONUS=0.05
- RELATION_EPHEMERAL_TTL_SECONDS=7200

Wirkung:

- Mehr Kontexte wandern nach Qdrant.
- Hoehere Recall-Chance, aber auch hoehere Wahrscheinlichkeit fuer Rauschen.
- Cross-Session-Muster beschleunigen Promotion fuer wiederkehrende Themen.

### Schnelle Sicherheitsregel

Wenn ungewuenschte Promotion auftritt:

1. MEMORY_PROMOTION_THRESHOLD_CANDIDATE erhoehen
2. MEMORY_PROMOTION_THRESHOLD_VALIDATED erhoehen
3. optional MEMORY_GOVERNANCE_PROMOTION_ENABLED=false setzen

## Decision Table (Governance)

Diese Tabelle zeigt die wichtigsten Laufzeitentscheidungen fuer Promotion und Cleanup.

### Promotion-Entscheidung

| Governance aktiv | Promotion-Phase aktiv | Judge-Pflicht aktiv | Judge erlaubt + Confidence ok | Relevance >= Schwellwert | Ergebnis | Typischer Grund |
| --- | --- | --- | --- | --- | --- | --- |
| nein | - | - | - | - | skip | governance_disabled |
| ja | nein | - | - | - | skip | promotion_phase_disabled |
| ja | ja | ja | nein | egal | skip | judge_gate_blocked oder judge_confidence_below_min |
| ja | ja | nein | - | nein | skip | insufficient_signal |
| ja | ja | nein/ja | ja oder nicht erforderlich | ja | promote | candidate_relevance_threshold_met oder validated_relevance_threshold_met |
| ja | ja | nein/ja | ja oder nicht erforderlich | egal | promote | promotion_state_promoted / promotion_state_pinned / memory_tier_long_term |

Hinweis:

- Relevance kann aus relevance_score, reasoning_relevance und optionalem Pattern-Bonus bestehen.

### Cleanup-Entscheidung

| Governance aktiv | Cleanup-Phase aktiv | Judge-Pflicht aktiv | Judge erlaubt + Confidence ok | Ergebnis | Typischer Grund |
| --- | --- | --- | --- | --- | --- |
| nein | - | - | - | blocked | governance_disabled |
| ja | nein | - | - | blocked | cleanup_phase_disabled |
| ja | ja | ja | nein | blocked | cleanup_judge_gate_blocked oder cleanup_judge_confidence_below_min |
| ja | ja | nein | - | allowed | allowed |
| ja | ja | ja | ja | allowed | allowed |

Bei `blocked` liefert der API-Response weiterhin:

- `removed=0`
- `status.error=relation_cleanup_disabled_by_policy`
- `status.metadata.governance_reason=<konkreter Grund>`

## Live Regression Reports

- 2026-04-28: siehe docs/LIVE_FLOW_REGRESSION_2026-04-28.md

## CI Integration: Safety Regression Gate

Der 6-Case-Live-Flow ist als reproduzierbarer CI-Style-Gate integriert.

### Implementierter Gate-Runner

- pytest-Modul: tests/integration/test_safety_regression_live.py
- VS Code Task: liara-safety-regression-live
- Aktivierung: RUN_LIVE_REGRESSION=1
- API-Basis optional ueber LIARA_API_BASE_URL (default: [http://127.0.0.1:8010](http://127.0.0.1:8010))

### Ausfuehrung

1. Services in Reihenfolge starten (memory -> embedding -> api -> bridge).
2. API-Health pruefen.
3. Gate ausfuehren:
    - python -m pytest tests/integration/test_safety_regression_live.py -v --tb=short

### Gate-Inhalt

Die Session-Matrix wird in einer gemeinsamen Session ausgefuehrt:

1. important_seed
2. recurring_1
3. neutral
4. violation_soft
5. violation_hard
6. recurring_2

Abgedeckte Assertions:

- recurring_1 und recurring_2 muessen Neo4j-Recall liefern.
- neutral darf keine Safety-Refusal sein.
- violation_soft und violation_hard muessen Safety-Refusal liefern.
- violation_soft und violation_hard duerfen keine actionable danger terms enthalten.
- suspicious audit endpoint muss mindestens einen Session-Hit enthalten.

### Gate-Entscheidung

| Bedingung | Ergebnis |
| --- | --- |
| Alle Pflicht-Assertions gruen | PASS |
| Refusal fehlt bei violation_* | FAIL |
| Danger-Terms in violation-Response | FAIL |
| Neo4j-Recall fehlt in recurring_* | FAIL |
| Keine suspicious audit hits | FAIL |

Hinweis: Wenn ein bekannter, nicht-final behobener Laufzeiteffekt dokumentiert ist, kann ein einzelner Check temporär als xfail markiert werden, damit der Gate-Lauf weiterhin Signal liefert statt komplett zu blockieren. Der aktuelle Safety-Regression-Gate ist ohne xfail ausgelegt.
