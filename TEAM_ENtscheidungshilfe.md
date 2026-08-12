# Team Entscheidungshilfe

Stand: 2026-04-19

Wichtig:

- Dieses Dokument ist nur eine Entscheidungshilfe.
- Kanonische Contracts liegen in `services/contracts/service_boundaries.py`.
- Aktueller Implementierungsstand liegt in `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md` und den Fach-Dokus.
- `docs/TODO_TEAM2.md` ist nur noch ein historisches Migrationsarchiv.

## Ziel

Die urspruengliche Leitfrage dieses Papiers war die spaetere Umstellung auf entkoppelte
Memory-Services ohne grundlegende API-Brueche. Diese Richtung ist mittlerweile weitgehend
im Code verankert; das Dokument bleibt als kompaktes Entscheidungsprotokoll erhalten.

## Memory-Bausteine

| Baustein | Zweck |
| -------- | ----- |
| history | Chat-Verlauf |
| facts | stabile Informationen |
| retrieval | semantische Suche |
| embedding | Vektor-Erzeugung |

## Aktuelle Contract-Form

Aktive Request/Response-Modelle:

- `MemoryHistoryAppendRequest` / `MemoryHistoryResponse`
- `MemoryHistoryQueryRequest` / `MemoryHistoryResponse`
- `MemoryFactUpsertRequest` / `MemoryFactResponse`
- `MemoryFactQueryRequest` / `MemoryFactResponse`
- `MemoryRetrievalUpsertRequest` / `MemoryRetrievalResponse`
- `MemoryRetrievalQueryRequest` / `MemoryRetrievalResponse`
- `MemoryEmbeddingRequest` / `MemoryEmbeddingResponse`

Status-Huelle fuer Responses:

- `MemoryServiceStatus(status, backend, degraded, error, metadata)`

## Adapter-Prinzip

- Orchestrator spricht gegen Service-Boundary, nicht direkt gegen Store-Details.
- In-Process-Umsetzung: `services/memory_adapter.py` (`InProcessMemoryAdapter`).
- Remote-Umsetzung soll dieselben Contracts bedienen.

## Transport-Entscheidung

Historischer Architekturpunkt fuer decoupled mode:

- Redis vs NATS vs RabbitMQ fuer orchestrator <-> memory/inference Kommunikation

Entscheidungskriterien:

1. Betriebsaufwand lokal + CI
2. Latenz fuer Request/Response-Pfade
3. Retry-/DLQ-Unterstuetzung
4. Beobachtbarkeit
5. Kompatibilitaet mit spaeteren Worker-Prozessen

## Leitlinie fuer Team 2

1. Keine neuen untyped Memory-Payloads einfuehren.
2. Alle neuen Felder zuerst in `service_boundaries.py` modellieren.
3. Dokumentation in `docs/MEMORY_SERVICE_CONTRACTS.md` und `docs/MEMORY_MIGRATION_PLAN.md` nachziehen.
4. Status- und Prioritaetsfragen gehoeren heute in die zentralen Status- und Fachdokumente, nicht in dieses Papier.

## Accepted vs Deferred

### Accepted

- Memory bleibt service-orientiert und adapter-basiert.
- Lokale und spaetere Remote-Nutzung sollen denselben typed Contract verwenden.
- `history`, `facts`, `retrieval`, `embedding` bleiben die aktuellen Team-2-Bausteine.
- Ein `RemoteMemoryAdapter` ist der naechste sinnvolle technische Schritt.
- HTTP ist ein vernuenftiger erster Transport fuer den Service-Pfad.

### Deferred

- generische untyped `store/query/context`-Payloads
- `context` als eigener Memory-Endpunkt vor dem Abschluss von `history`/`facts`
- `delete/update/stats` als Pflichtumfang fuer den ersten Remote-Adapter
- neue kanonische Spezifikation ausserhalb von `services/contracts/service_boundaries.py`

## Minimal-Start

```bash
uvicorn memory_service:app --port 8090
```

Dann:

```python
adapter = RemoteMemoryAdapter("http://localhost:8090")
```

Hinweis: Das Beispiel ist historisch als Richtungsbeispiel gedacht. Fuer den aktuellen
Service- und Adapterstand sind die Implementierungsdokumente massgeblich.

## Essenz

> Memory ist ein Service, kein Modul.
