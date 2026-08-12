# ADR-001: Memory-Zugriffe nur ueber Adapter/Servicegrenze

Status: akzeptiert  
Datum: 2026-05-01  
Kontext: aktueller Ist-Stand aus `services/memory_adapter.py`, `services/memory/`, `docs/LIARA_SNAPSHOT_2026-04-28.md`

## Entscheidung

API und Orchestrator greifen nicht direkt auf Memory-Backends zu. Alle Memory-Operationen laufen ueber:

- `InProcessMemoryAdapter`
- `RemoteMemoryAdapter`
- `ensure_memory_service_adapter(...)`
- `liara-memory` HTTP-Service

## Begruendung

LIARA nutzt mehrere Speicherarten:

- Redis
- Postgres
- Qdrant
- Chroma
- Neo4j

Direkte Zugriffe aus mehreren Services wuerden Contracts, Policies und Fehlerverhalten duplizieren. Die Adapter-/Servicegrenze ermoeglicht:

- einheitliche Contracts
- remote und in-process Betrieb
- Health-/Backenddiagnose
- zentrale Context-Upsert-Policies
- Graph-v2-Persistenz ohne API-Datenbankwissen
- kuenftigen Austausch einzelner Backends

## Konsequenzen

Positiv:

- klare Servicegrenze
- testbare Contracts
- weniger Kopplung zwischen Orchestrator und Datenbanken
- einheitlicher Pfad fuer Graph-v2

Negativ:

- mehr Adaptercode
- bei Live-Tests muessen API und Memory-Prozess beide zum aktuellen Code passen
- Fehlersuche braucht Health-Pruefung auf mehreren Ebenen

## Durchsetzung

- Neue Memory-Funktionen zuerst als Contract in `services/contracts/service_boundaries.py` modellieren.
- Danach Adaptermethoden fuer in-process und remote ergaenzen.
- Danach Memory-Service-Endpunkt und Store-Implementierung verbinden.
- Orchestrator/API duerfen nur Adaptermethoden verwenden.

## Referenzen

- `services/memory_adapter.py`
- `services/memory/app.py`
- `services/memory/store.py`
- `docs/MEMORY_SERVICE_CONTRACTS.md`
- `docs/LIARA_SNAPSHOT_2026-04-28.md`
- `docs/V2_GRAPH_PERSISTENCE_VALIDATION.md`
