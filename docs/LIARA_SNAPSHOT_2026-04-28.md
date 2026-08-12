# LIARA Snapshot (2026-04-28)

## Kurzfazit

- LIARA laeuft auf einer klar getrennten Service-Architektur: API -> Orchestrator -> Memory/Inference/Tools.
- Der v2-Graph-Persistence-Pfad ist jetzt end-to-end aktiv und mit 100/100 erfolgreichen Requests validiert.
- Die kanonischen Runtime-Pfade liegen weiter unter `services/*`; direkte DB-Zugriffe ausserhalb des Memory-Service bleiben ausgeschlossen.

## Systembild

| Ebene | Rolle | Aktueller Stand |
| ----- | ----- | --------------- |
| API | Einstiegspunkt fuer Chat und Runtime | FastAPI auf Port 8010, gesund |
| Orchestrator | Routing, Toolwahl, Persistenz-Trigger | produktiv, Graph-v2-Persistenz integriert |
| Memory Service | Service-Grenze fuer Speicherzugriffe | FastAPI auf Port 8020, gesund |
| Graph Tier | Neo4j fuer Beziehungen und Persistenzgraph | v2 aktiv und validiert |
| Weitere Memory-Tiers | Redis, Postgres, Qdrant, Chroma | weiterhin Teil der Zielarchitektur |

## Memory-Modell

- Arbeitsgedaechtnis: Redis und Session-State
- Kurzzeitgedaechtnis: Chroma / Session Recall / Run Context
- Langzeitgedaechtnis: Qdrant / Semantic Memory
- Beziehungen: Neo4j / Relation Lookup
- Explizite Fakten: Postgres

## Was im Chat umgesetzt wurde

| Bereich | Ergebnis |
| ------ | -------- |
| GraphStore | 9 v2-Methoden vorhanden und nutzbar |
| Adapter | 9 `graph_*`-Methoden in `InProcessMemoryAdapter` und `RemoteMemoryAdapter` ergaenzt |
| Contracts | 11 Request/Response-Modelle fuer Graph-v2 verdrahtet |
| Memory Store | Delegation an GraphStore komplett |
| API | 9 `/graph/*`-Routen aktiv |
| Orchestrator | automatische Post-Run-Persistenz eingebunden |

## Entscheidende Fixes

1. Adapter-Gap geschlossen: fehlende `graph_*`-Methoden in `services/memory_adapter.py` implementiert.
2. Pydantic-Literal repariert: `backend="graph-v2"` auf `backend="memory-service"` korrigiert.
3. Laufende Prozesse neu gestartet: erst danach war der neue Code im Live-Pfad wirksam.

## Validierter Ist-Stand

- Benchmark: 100 Fragen, 100 erfolgreich, 0 Fehler
- Session: `v2bench100_20260427_223147`
- Build-History-Eintrag: ID 86
- Validierter Persistenzpfad:

```text
API (8010)
-> Orchestrator
-> persist_run_to_graph_v2()
-> RemoteMemoryAdapter
-> Memory Service (8020)
-> GraphStore
-> Neo4j
```

## Gemessene Neo4j-Deltas

| Metrik | Vorher | Nachher | Delta |
| ------ | ------ | ------- | ----- |
| Task | 6 | 106 | +100 |
| Fact | 14 | 274 | +260 |
| Context | 6 | 7 | +1 |
| RelNonEntity | 57 | 1097 | +1040 |
| Paths | 520 | 1693 | +1173 |

## Betriebsrelevante Punkte

- Port 8010 bleibt der Standard-Einstiegspunkt fuer Live-Checks.
- Memory-Zugriffe sollen weiter nur ueber Service- und Adapter-Grenzen laufen.
- Nach Backend-Aenderungen muessen laufende API-/Memory-Prozesse bei Live-Validierung oft neu gestartet werden.
- `liara-smoke-all` ist unter Windows PowerShell weiterhin stoeranfaellig; fokussierte Smoke-Tasks sind derzeit der verlaesslichere Weg.

## Naechste sinnvolle Schritte

1. Graph-Queries und Traversals auf Last beobachten.
2. Neo4j-Indizes fuer haeufige Kanten gezielt nachziehen.
3. API-Referenz um die `/graph/*`-Route-Flaeche erweitern.
4. Retrieval- und Graph-Pfade kuenftig gemeinsam evaluieren statt isoliert.

## Referenzen

- Detailvalidierung: `docs/V2_GRAPH_PERSISTENCE_VALIDATION.md`
- Statusueberblick: `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md`
- Build-History-Tooling: `docs/BUILD_HISTORY.md`
