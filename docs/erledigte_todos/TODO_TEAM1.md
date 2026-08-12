# TODO Team 1

Status: archival migration board, not the canonical current-status document.

## Einordnung

Diese Datei dokumentiert die abgeschlossene Team-1-Migrationswelle fuer Tool-Ausfuehrung,
Inference-Gateway und Orchestrator-Split. Sie ist bewusst als Archiv erhalten, soll aber
nicht mehr als laufendes Sprint-Board oder aktuelle Gesamtstatusquelle gelesen werden.

Kanonische Ist-Staende:

- `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md`
- `docs/IMPLEMENTATION_SPEC.md`
- `docs/BACKEND_ANALYSIS_2026-04-19.md`

## Historischer Scope

Team 1 deckte in dieser Phase vor allem diese Bereiche ab:

- `services/tools/coordinator.py`
- `services/inference/gateway.py`
- `services/tools/registry.py`
- `tests/unit/test_tool_coordinator.py`
- `tests/unit/test_inference_gateway.py`

## Was in dieser Migrationsrunde landete

- Orchestrator-Split in Router/Planner/Executor ohne API-Bruch
- Provider-Adapter fuer Inference (`ollama`, `openvino`)
- normalisierte Stream-/Final-Envelopes
- Invoker-Grenze fuer `direct` und queue-ready Pfade
- Redis-Streams-Transport fuer entkoppelte Inference-Ausfuehrung
- additive Trace-/Contract-Stabilisierung fuer direct/queue/service modes

## Einordnung des Ergebnisstands

- Die Team-1-Migrationspunkte M1-M7 gelten als abgeschlossen.
- Die frueheren Sprint-Zaehler in dieser Datei waren punktuelle Werte vom 2026-04-14 und
  sind nicht mehr die empfohlene Referenz fuer den aktuellen Repo-Zustand.
- Fuer die aktuelle, reproduzierbare Regression innerhalb dieses Themenblocks ist die
  Workspace-Task `liara-test-memory-and-team1` die bessere Referenz.

Zuletzt in dieser Doku-Runde verifiziert:

- `tests/unit/test_memory_stores.py`
- `tests/unit/test_tool_coordinator.py`
- `tests/unit/test_inference_gateway.py`
- `tests/integration/test_orchestrator_flow.py`
- Ergebnis: `93 passed`

## Historische Entscheidungen aus der Migrationsphase

- Redis Streams war die bevorzugte Queue-Option fuer den ersten entkoppelten Pfad.
- Team-uebergreifende Contracts sollten nur additiv erweitert werden.
- `TEAM_ENtscheidungshilfe.md` war und bleibt nur Entscheidungshilfe, nicht kanonische Spezifikation.

## Weiterfuehrende Referenzen

- Queue-/Inference-Stand: `docs/IMPLEMENTATION_SPEC.md`
- Backend-Ist-Analyse: `docs/BACKEND_ANALYSIS_2026-04-19.md`
- Aktueller Gesamtstand: `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md`
