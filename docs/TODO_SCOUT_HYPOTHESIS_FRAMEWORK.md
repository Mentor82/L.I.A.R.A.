# TODO: Scout Hypothesis Framework Enhancement

**Status**: Documented & Baseline Implemented  
**Date Created**: 2026-04-22  
**Owner**: Team 1 (Orchestrator)

---

## Overview

Scout is now explicitly documented as a **hypothesis generator** within the Orchestrator routing phase. The implementation already exists in `services/orchestrator/router.py:_route_semantic()`, but several enhancement opportunities remain.

---

## Completed ✅

- [x] Scout role clarified in ARCHITECTURE.md as "Hypothesen-Generator"
- [x] Current implementation in `_route_semantic()` generates multiple hypotheses with scores:
  - `orientation` (0.0-1.0)
  - `conversation_recall_local` (0.0-1.0)
  - `sys` (0.0-1.0)
- [x] Confidence thresholds documented (strong=0.85, medium=0.70)
- [x] Conservative decision logic implemented (strong→commit, medium→confirm, low→fallback)
- [x] Metadata transparency: all scores exported in `RouterDecision.metadata.semantic_scores`

---

## Open Enhancements

### 0. Scout: Integration mit echten Embeddings (Reconciliation)
**Description**: Aktuell arbeitet Scout nur mit Token-Overlap-Heuristik (`router.py:_semantic_similarity()`), nutzt aber NICHT die verfügbare Qwen3-Embedding-0.6B aus `services/embedding/engine.py` (1024-D Vektoren via OpenVINO).

**Diskrepanz**:
- Pipeline.md dokumentiert: "SCOUT (Qwen3-Embedding-0.6B + Intent-Classifier)"
- router.py implementiert: Nur Jaccard-ähnlicher Token-Overlap, keine echten Vektoren
- engine.py bereitstellt: Echte 1024-D Embeddings mit Qwen3-Embedding-0.6B

**Optionen**:
- **Option A** (bessere Qualität): Scout nutzt Embedding-Service für echte Vektorsimilarität
  - Intent-Profile würden als 1024-D Vektoren vorberechnet
  - Query → embedding_vector → cosine_similarity gegen Profile-Vektoren
  - Aktivierbar über `SCOUT_USE_REAL_EMBEDDINGS=true`
  
- **Option B** (schneller Baseline): Pipeline.md anpassen, Scout bleibt keyword-only
  - Explizit dokumentieren: Scout ist bewusst lightweight für Latenz
  - Embedding-Service bleibt für retrieval/RAG vorbehalten

**Empfehlung**: Option A prüfen für nächste Sprint, wenn Embedding-Latenz akzeptabel ist.

**Impact**: Höhere Intent-Discrimination, weniger False-Positives in Medium-Band  
**Location**: `router.py:_route_semantic()`, new dependency on `embedding_service`  
**Effort**: Medium (embedding service integration, profile caching, latency tuning)

---

### 1. Expand Intent Profiles
**Description**: Current Scout has only 3 intent categories. Consider adding:
- `data_analysis` (queries requesting stats, charts, aggregates)
- `code_exploration` (symbol lookup, file search, refactoring queries)
- `debugging` (error traces, log parsing, failure analysis)

**Impact**: Richer hypothesis space → better disambiguation  
**Location**: `router.py:_intent_profiles` dict  
**Effort**: Low (add keywords, define profile set)

### 2. Confidence Persistence & Visualization
**Description**: Log all Scout hypothesis scores to a trace-accessible format for visualization in admin TUI or debug dashboards.

**Current State**: Logged in `metadata.semantic_scores` but not persisted to database.  
**Desired State**: Option to store Scout hypothesis snapshots in `decision_trace` for post-hoc analysis.

**Location**: `orchestrator.py:run()` → capture and store `router_decision.metadata.semantic_scores`  
**Effort**: Medium (trace schema update, persistence adapter)

### 3. Scout → Evidence Feedback Loop
**Description**: If Judge rejects a tool choice from a Scout hypothesis, feed back confidence penalty to update profile weights over time.

**Current State**: Profiles are static keyword sets.  
**Desired State**: Adaptive hypothesis profiles that learn from Judge feedback.

**Location**: Possible in `judge.py` → send confidence signals back to Router  
**Effort**: High (requires adaptive learning, test coverage)

### 4. Multi-Language Intent Profiles
**Description**: Expand German keywords in `_intent_profiles` to match breadth of English keywords.

**Current State**: German keywords present but fewer than English.  
**Desired State**: Balanced coverage for both languages.

**Location**: `router.py:_intent_profiles`  
**Effort**: Low (keyword additions, testing)

### 5. Documentation: Scout Hypothesis Flow Diagram
**Description**: Create a visual diagram (Mermaid/PlantUML) showing:
- Query input → Scout hypothesis generation
- Scoring logic → confidence thresholds
- Decision routing based on confidence band

**Location**: `docs/ARCHITECTURE.md` (new section or Pipeline.md)  
**Effort**: Low (diagram + 3-5 line explanation)

---

## Test Coverage

Current tests:
- `tests/unit/test_orchestrator_flow.py`: Basic routing assertions
- `tests/integration/test_chat_stream_memory_effect_live.py`: Live endpoint testing

**Recommended additions**:
- [ ] Unit: Scout hypothesis generation with edge-case queries
- [ ] Unit: Confidence threshold boundaries (0.849, 0.700, 0.701, 0.851)
- [ ] Unit: Multi-language intent matching (de-DE, en-US)
- [ ] Integration: Scout behavior with malformed/spam queries
- [ ] Integration: Scout hypothesis persistence in trace (once #2 done)

---

## Related Files

| File | Role |
|------|------|
| `services/orchestrator/router.py` | Scout hypothesis generation logic |
| `docs/ARCHITECTURE.md` | Scout role definition (updated 2026-04-22) |
| `services/orchestrator/orchestrator.py` | Scout integration point (line ~350) |
| `tests/unit/test_orchestrator_flow.py` | Test bed for routing decisions |
| `services/contracts.py` | RouterDecision schema (includes metadata.semantic_scores) |

---

## Dependency Chain

1. **Expand Intent Profiles** (#1) is independent
2. **Confidence Persistence** (#2) should precede Multi-Language (#4)
3. **Feedback Loop** (#3) depends on storage from #2
4. **Documentation Diagram** (#5) can be done anytime; recommended as first public-facing task

---

## Notes

Scout hypothesis framework is **working baseline**. The enhancements are quality/scale improvements, not bug fixes. Prioritize based on user-facing impact (Diagram > Multi-Language > Persistence > Feedback > Profiles).
