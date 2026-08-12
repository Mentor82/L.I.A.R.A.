# Service: liara-memory

Stand: 2026-07-14  
Code: `services/memory/`, `services/memory_adapter.py`

## Aufgabe

`liara-memory` kapselt Speicherzugriffe hinter einer Service- und Adaptergrenze. Clients und Orchestrator sollen nicht direkt gegen Datenbanken arbeiten.

## Backends

| Backend | Zweck | Compose-Port |
| --- | --- | --- |
| Postgres | Facts / relationale Persistenz | `5433 -> 5432` |
| Redis | Session-/Working-State, Queue-nahe Daten | `6380 -> 6379` |
| Qdrant | semantische Retrieval-Vektoren | `6335 -> 6333`, `6336 -> 6334` |
| Neo4j | Graph-v2, Beziehungen, Kontextgraph | `7688 -> 7687`, `7475 -> 7474` |
| Chroma | lokaler Context-/Vector-Tier | `8001 -> 8000` |

## API

Der Service stellt FastAPI-Endpunkte fuer History, Facts, Retrieval,
Embeddings, Context, Relations, Graph-v2, Staging, Dreaming und Validator-Jobs
bereit. Details: `docs/03_apis/current-api-surface.md`.

Dreaming ist aktuell manuell/ops-getriggert. Obwohl der Contract den Trigger
`scheduled` kennt, meldet der Store `scheduler_enabled=false` und
`mode=manual_only`.

Die ausfuehrliche Abgrenzung steht in `docs/02_services/liara-dreaming.md`:
Frontend und Architekturkarte verwenden den Begriff `Dreaming`; der
Backend-Fluss bleibt technisch Staging -> Dreaming-/Consolidation-Run ->
Proposal -> explizite Decision. Dieser Pfad ist keine autonome
Selbstveraenderung und kein Scheduler-Recht.

### Validator-Jobs und WSL-Workspaces

Validator-Jobs werden ueber den Memory-Service persistiert und asynchron
ausgefuehrt. Job-Lifecycle, Workspace-Vorbereitung und Ergebniscontract sind
von der Ausfuehrungsumgebung getrennt:

```text
Validator-Job
-> begrenzte Workspace-Vorbereitung
-> ValidatorExecutionBackend
-> einheitliches Ergebnis + Audit
```

`services/memory/validator_execution.py` definiert das registrierbare
`ValidatorExecutionBackend`-Protokoll. Aktuell implementiert sind `mock` und
`docker_compose`. VM-, Remote-Worker- oder alternative Container-Adapter
koennen denselben Request-/Result-Contract implementieren, ohne Memory-API,
Orchestrator oder TUI zu veraendern. `LIARA_VALIDATOR_BACKEND` waehlt im
Worker-Modus das registrierte Backend; ein unbekannter Name wird fail-closed
abgewiesen.

Nur der aktuelle Compose-Adapter loest den Docker-Client in dieser Reihenfolge
auf:

1. `LIARA_VALIDATOR_DOCKER_CLI`
2. `docker` aus dem Prozess-`PATH`
3. bekannte Docker-Desktop-Pfade unter Windows

Fuer freigegebene native WSL-Workspaces erzeugt der Memory-Service vor der
Backend-Ausfuehrung eine eingegrenzte lokale Arbeitskopie unter
`artifacts/validator_jobs/<job-id>/workspace_snapshot`. Das Backend erhaelt
diesen vorbereiteten Pfad; der kanonische Workspace bleibt in WSL. Damit ist
die Schutzgrenze nicht von Docker, einer VM oder einer bestimmten
Container-Runtime abhaengig.

Die Staging-Grenzen sind konfigurierbar:

- `LIARA_VALIDATOR_ALLOWED_WSL_DISTROS` (Standard `Debian`)
- `LIARA_VALIDATOR_WORKSPACE_MAX_FILES` (Standard `2000`, Hard-Cap `10000`)
- `LIARA_VALIDATOR_WORKSPACE_MAX_BYTES` (Standard `104857600`, Hard-Cap 1 GiB)

Die bisherigen `...WSL_DISTROS`- und `...SNAPSHOT_MAX_*`-Namen bleiben als
Kompatibilitaetsaliases erhalten.

Ausgeschlossen werden unter anderem `.venv`, `.git`, `.pytest_cache`,
`__pycache__` und `.liara_artifacts`; Symlinks werden nicht uebernommen. Das
Validator-Ergebnis dokumentiert Originalpfad, Staging-Pfad, Distribution,
Datei-/Bytezahl, Ausschluesse und den tatsaechlich verwendeten Docker-Client.

## Adapter-Modell

Aktueller Grundsatz:

```text
Kein direkter Memory-Zugriff aus API/Orchestrator ausserhalb Adaptergrenze.
```

Pfade:

- `InProcessMemoryAdapter` fuer lokalen/in-process Betrieb
- `RemoteMemoryAdapter` fuer HTTP gegen `liara-memory`
- `ensure_memory_service_adapter(...)` als Vereinheitlichung

## Policy im Memory-Service

`services/memory/store.py` enthaelt Policy-Pruefungen fuer Context-Upserts:

- leere Inhalte werden blockiert
- sensitive Muster wie API Keys, Tokens, Authorization Bearer, Passwoerter und Secrets werden blockiert
- explizit als `working_context` markierte Inhalte brauchen Scope und Validierung oder explizite Akzeptanz

## Graph-v2

Graph-v2 ist im Memory-Service als Endpunktfamilie `/graph/*` vorhanden:

- Agent
- Task
- Context
- Fact
- Fact-Link
- Embedding
- Semantic-Link
- Tool
- Context-Graph
- Architecture-Subgraph fuer die read-only Living Architecture Map

Der Architecture-Subgraph ist kein allgemeiner Graphbrowser. Sein Contract
akzeptiert nur `orchestrator|memory` und maximal 25 Beziehungen. Neo4j-Labels,
Relationstypen und ausgegebene Properties sind fest allowlist-basiert; rohe
Fact-Texte und `metadata_json` verlassen diesen Diagnosepfad nicht.

Der vorhandene Snapshot `docs/LIARA_SNAPSHOT_2026-04-28.md` dokumentiert eine erfolgreiche 100/100-Validierung des API -> Orchestrator -> Graph-v2 -> Neo4j-Pfads.

## Start

```powershell
python -m uvicorn services.memory.app:app --host 127.0.0.1 --port 8020
```

oder mit Compose-Profil:

```powershell
docker compose --profile app up liara-memory
```

## Aktueller Befund

Die Servicegrenze ist real implementiert. Das System kann in-process und remote
betrieben werden. Im Laufzeit-Snapshot waren alle sechs gemeldeten Backends
einschliesslich Embedding healthy. Der reale Validator-Job
`7c895ee9-66e8-4b51-ab9c-0ba182a5f12b` lief am 2026-07-14 ueber eine
kontrollierte WSL-Arbeitskopie erfolgreich durch (`exit_code=0`, 0 Findings,
8 Dateien / 6939 Bytes). Offen sind unter anderem veraltete Testadapter nach
der Graph-v2-Contract-Erweiterung sowie Aufbewahrungs-/Cleanup-Regeln fuer
Validator-Snapshots.
