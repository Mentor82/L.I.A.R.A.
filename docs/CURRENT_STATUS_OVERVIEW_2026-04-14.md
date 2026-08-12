# Current Status Overview (updated 2026-04-19)

## Gesamtstand

- Kanonische Runtime-Pfade liegen unter `services/*`.
- API startet ohne harte Reward-Model-Abhaengigkeit; optionale ML-Pfade werden lazy geladen.
- Judge v1 ist als echte Pipeline im Runtime-Pfad aktiv:
  - Pre-Action fuer `sys`, `compute.run`, `compute.generate`
  - Simulation-Mode Gate
  - Post-Result Validation
  - optionale Reward-Model-Verschaerfung in Judge und Routing
- Reward-Model-Workflow ist vorhanden:
  - Dataset-Generator
  - Trainer-API
  - CLI `scripts/train_reward_model.py`
  - Artefakt-Bundle unter `artifacts/reward_model/`
- WMTool-Liara wird ueber MSYS2 `mingw64` gebaut; Build- und Packaging-Wrapper sind vorhanden.

## Zuletzt verifizierte Punkte

- `liara-test-memory-and-team1`: am 2026-04-17 mit 87 Tests gruen verifiziert.
- Reward-Model-Unit-Suite: 27 Tests gruen nach Trainer-/Dataset-Workflow-Erweiterung.
- Judge-Engine-Reward-Integration: 8 Tests gruen.
- Orchestrator-Reward-Routing: fokussierte Unit-Tests gruen.
- API `/health`: nach Lazy-Import-Fix wieder erfolgreich erreichbar.
- Reward-Training-Smoke-Run:
  - alter generierter Datensatz: ca. 66 Samples, beobachtete Test-Accuracy ~0.43
  - aktueller generierter Datensatz: 188 deduplizierte Samples, beobachtete Test-Accuracy ~0.63

## Plattformstatus

### Backend / Orchestrator

- semantisches Tool-Routing ist implementiert und produktiv im optionalen Pfad.
- Librarian-/Context-Strategie fuer Facts, Session-Recall, Relations, Semantic Memory und Run Context ist vorhanden.
- Evidence-Engine mit Semantic Filtering und konfigurierbaren Thresholds ist aktiv.
- Inference-Invoker unterstuetzt direct/queue/service-Metadaten im Runtime-Trace.
- Memory-Zugriffe laufen ueber Adapter-/Service-Grenzen.

### Judge / Reward Model

- Judge-Contracts und Judge-Engine sind implementiert.
- Reward-Model ist nicht mehr nur "ready for integration", sondern in Judge und Orchestrator-Routing verdrahtet.
- Reward-Model bleibt optional: ohne Modellpfad oder ohne `scikit-learn` startet die Runtime weiterhin.
- Trainer-CLI und Artefaktpersistenz sind vorhanden.

### Frontend / WMTool-Liara

- Build erfolgt ueber `frontend/WMTool-Liara/build.ps1` mit korrekt gesetztem MSYS2-`mingw64`-Pfad.
- Packaging erfolgt ueber `frontend/WMTool-Liara/package.ps1`.
- Wenn `dist/` gelockt ist, faellt Packaging automatisch auf ein Zeitstempel-Verzeichnis zurueck.
- Dist-Layout ist bereinigt:
  - `bin/` nur fuer Executables
  - `lib/` fuer Projekt- und GTK-DLLs
  - `config/`, `cache/`, `logs/` getrennt

## Speicher / Backends

- Postgres, Redis, Qdrant, Chroma, Neo4j und Embedding-Service sind weiterhin die vorgesehenen Tiers bzw. Backends.
- Memory-Service-Architektur und Adapter-Grenzen bleiben gueltig.
- Graph-Tier-/Qdrant-Cutover sind weiterhin die klaren Restthemen.

## Offene Themen / Risiken

- Graph-Tier-Cutover ist noch offen.
- Retrieval laeuft noch mit Fallback-Semantik (Qdrant-Verdrahtung offen).
- Reward-Model-Metriken auf rein synthetischem Datensatz sind noch verbesserungsfaehig; aktueller Generator ist besser, aber noch kein Produktionsgoldstandard.
- Mehrere historische Reports im Repo waren bereits veraltet; die zentrale Statusquelle sollte diese Datei plus die fachspezifischen Dokus sein.

## Datenbanknutzung (vereinbart)

- Postgres: Wahrheit ("Was ist passiert?")
- Redis: Kurzzeitgedaechtnis ("Was ist gerade los?")
- Qdrant/Chroma: Aehnlichkeit ("Was passt dazu?")
- Neo4j: Beziehungen ("Was haengt zusammen?")

## Referenzdokumente

- Routing/Evidence/Embedding: `docs/ROUTING_EVIDENCE_EMBEDDING_UPDATE_2026-04-19.md`
- Reward Model: `docs/REWARD_MODEL.md`
- Judge: `docs/judge.md`
- Backend-Ist-Analyse: `docs/BACKEND_ANALYSIS_2026-04-19.md`
