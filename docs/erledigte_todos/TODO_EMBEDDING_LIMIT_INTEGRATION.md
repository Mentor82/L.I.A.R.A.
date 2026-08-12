# TODO: Embedding-Limit Integration (Gegenpruefung + Umsetzungsplan)

## Gegenpruefung (Stand Code/Config)

### Bestaetigt
- Embedding ist als eigener Service/Worker getrennt vom LLM-Prozess.
- Eigene Embedding-Ports und Queue-Streams sind vorhanden.
- NPU-Pfad ist konfiguriert (`EMBEDDING_DEVICE=NPU`) mit Fallback.

### Teilweise / Offen
- In `docker-compose.yml` ist fuer `liara-embedding` aktuell `EMBEDDING_DEVICE: CPU` gesetzt.
- Damit unterscheiden sich lokale `.env` (NPU) und Compose-Setup (CPU).

### Widerlegt / Nicht belegt
- "1024 Token Input-Limit aktiv" ist im aktuellen Runtime-Code so nicht belegt.
- Der produktive Embedding-Engine-Pfad nutzt aktuell `max_length=512` als Default.
- Worker-Schicht schneidet zusaetzlich auf `EMBEDDING_WORKER_MAX_INPUT_CHARS=8000` (Zeichen, nicht Tokens).
- Aussagen zu festen Positionsembeddings / kein RoPE-Scaling / kein Sliding-Window sind im Repo nicht direkt nachgewiesen.

## Ziel
- Embedding-Inputgrenzen explizit und konsistent machen.
- Query-Embeddings robust kurz halten.
- Retrieval-Pipeline fuer lange Dokumente stabilisieren (Chunking + ggf. 2-Level-Re trieval).

## Priorisierte TODOs

## P0 - Sofort (Konfig-Konsistenz + Transparenz)
- [x] Device-Konfig harmonisieren: `.env` fuer Embedding (`NPU->CPU fallback`) klar festlegen.
- [x] `EMBEDDING_MAX_LENGTH` als Env-Variable einfuehren und in `services/embedding/engine.py` verwenden (statt hardcoded Default).
- [x] In `/health` des Embedding-Service `effective_max_length`, `runtime_backend`, `execution_devices` explizit ausgeben.
- [x] Dokumentation aktualisieren: Unterschied `max_input_chars` (Worker) vs `max_length` (Tokenizer/Tokens) klarstellen.

## P1 - Query-Embedding im Chatflow
- [x] Router-Query-Rewrite fuer Retrieval-Queries einfuehren (kurze, dichte Suchanfrage statt kompletter Verlauf).
- [x] Guardrail: Scout/Router darf nur komprimierte Query an Embedding senden (kein kompletter Chatverlauf).
- [x] Metriken loggen: Original-Query-Laenge, Rewrite-Laenge, Token-Laenge, truncation-Flag.

## P1 - Chunking-Strategie fuer Dokumente
- [x] Standard-Chunking definieren (z. B. 512-800 Tokens, Overlap 64-128) als zentrale Konfiguration.
- [x] Chunking Utility implementieren (token-basiert, nicht nur char-basiert).
- [x] Retrieval-Ingestion auf einheitliches Chunking umstellen.
- [x] Regressionstest: Kein Chunk ueber `effective_max_length`.

## P2 - Librarian Retrieval Pipeline
- [x] Bestehende Librarian-Routen um klare Retrieval-Phasen erweitern. (explizite `retrieval_phases` in Orchestrator `context_debug` + `LIBRARIAN_PHASES` Log)
- [x] Optionales 2-Level Retrieval pruefen: (implementiert hinter Feature-Flag `RETRIEVAL_TWO_LEVEL_ENABLED`)
- [x] Level 1: Dokument-Summary/Fingerprint-Embeddings (grobes Vorfiltering).
- [x] Level 2: Chunk-Embeddings innerhalb relevanter Dokumente (Feinsuche).
- [x] Evaluationsset bauen (Recall@k, MRR, Antwortqualitaet). (Eval-Utility in `services/memory/retrieval_eval.py` + Unit-Tests)

## P2 - Validierung und Betrieb
- [x] Live-Testmatrix: NPU-OpenVINO, CPU-Fallback, Compose-CPU. (Script `scripts/embedding_runtime_matrix.py`)
- [x] Lasttest fuer Embedding-Queue (Latenz, Fehlerrate, Fallback-Rate). (Script `scripts/embedding_queue_load_test.py`)
- [x] Alerts: hohe Truncation-Rate, hohe Fallback-Rate, Runtime-Wechsel OpenVINO->Transformers. (Health `runtime_stats` + `alerts`)

## Technische Referenzen (Dateien)
- `services/embedding/engine.py`
- `services/embedding/app.py`
- `workers/embedding-worker/worker.py`
- `services/orchestrator/librarian_router.py`
- `.env`
- `docker-compose.yml`

## Definition of Done
- [x] Effektive Token-Grenze ist per Env steuerbar und im Health sichtbar.
- [x] Query-Embeddings im Chatflow bleiben unter der Grenzlaenge.
- [x] Dokument-Retrieval nutzt einheitliches token-basiertes Chunking.
- [x] Optionales 2-Level Retrieval ist implementiert oder bewusst verworfen und dokumentiert.
- [x] Tests und Monitoring decken Truncation/Fallback sauber ab. (Embedding-Health Monitoring + Eval/Chunking/Fallback-Tests)
