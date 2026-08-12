# TODO Team 2

Status: archival migration board, not the canonical current-status document.

## Einordnung

Diese Datei dokumentiert die abgeschlossene Team-2-Migrationswelle fuer Memory-Service,
Adapter-Grenzen und Integrations-Cutover. Sie bleibt als Nachweis der damaligen Arbeit im
Repo, ist aber kein laufendes Sprint-Board mehr.

Kanonische Ist-Staende:

- `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md`
- `docs/IMPLEMENTATION_SPEC.md`
- `docs/BACKEND_ANALYSIS_2026-04-19.md`
- `docs/MEMORY_SERVICE_CONTRACTS.md`

## Historischer Scope

Team 2 deckte in dieser Phase vor allem diese Bereiche ab:

- `services/memory/tier_store.py`
- `services/memory/app.py`
- `services/memory_adapter.py`
- `tests/unit/test_memory_stores.py`
- `tests/integration/test_orchestrator_flow.py`

## Was in dieser Migrationsrunde landete

- typed Memory-Service-Contracts fuer `history`, `facts`, `retrieval`, `embedding`
- Adapter-Grenze zwischen Orchestrator und Store-Implementierungen
- Health-/Degradation-Semantik fuer Memory-Backends
- HTTP-Service-Pfad fuer `liara-memory`
- Retrieval- und Embedding-Endpunkte im Service-Modus
- Qdrant-Cutover fuer semantische Retrieval-Pfade

## Einordnung des Ergebnisstands

- Die Team-2-Migrationspunkte M1-M5 gelten als abgeschlossen.
- Die frueheren Zaehler in dieser Datei stammen aus einer 2026-04-14-Migrationsaufnahme und
  sind nicht mehr die bevorzugte Quelle fuer den aktuellen Repo-Zustand.
- Das fruehere separate Handoff-Dokument wurde entfernt; die relevanten Aussagen sind in
  `docs/IMPLEMENTATION_SPEC.md`, `docs/BACKEND_ANALYSIS_2026-04-19.md` und hier verdichtet.

Zuletzt in dieser Doku-Runde verifiziert:

- `tests/unit/test_memory_stores.py`
- `tests/unit/test_tool_coordinator.py`
- `tests/unit/test_inference_gateway.py`
- `tests/integration/test_orchestrator_flow.py`
- Ergebnis: `93 passed`

## Historische Entscheidungen aus der Migrationsphase

- Service-Grenzen sollten vor Store-Details stabilisiert werden.
- Response-Schemata sollten additiv bleiben, damit Team-1- und Team-2-Pfade parallel lauffaehig bleiben.
- `TEAM_ENtscheidungshilfe.md` war und bleibt nur Entscheidungshilfe, nicht kanonische Spezifikation.

## Offener Rest aus heutiger Sicht

- Graph-/Pattern-Cutover bleibt ein separates Restthema.
- Live-Abdeckung fuer Degradation-/Backend-Failure-Szenarien kann weiter verbreitert werden.
- Die aktuelle Gesamtpriorisierung steht nicht mehr hier, sondern in den zentralen Status- und Fach-Dokus.

## Weiterfuehrende Referenzen

- Memory-Service-Contracts: `docs/MEMORY_SERVICE_CONTRACTS.md`
- Memory-Migrationsplan: `docs/MEMORY_MIGRATION_PLAN.md`
- Backend-Ist-Analyse: `docs/BACKEND_ANALYSIS_2026-04-19.md`
- Aktueller Gesamtstand: `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md`
