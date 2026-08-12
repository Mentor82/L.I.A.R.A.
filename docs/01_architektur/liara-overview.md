# LIARA Architekturuebersicht

Stand: 2026-08-09

## Reifegrad

Der lokale Kern ist implementiert und aktuell betriebsfähig, aber LIARA ist
noch ein Entwicklungssystem und nicht production-ready. Die datierte
Statusmatrix, Testbaseline und priorisierte Übergabe stehen in
`docs/00_index.md`. Insbesondere sind die vollständige Unit-Suite, Auth-Grenze,
Konfigurationskonsistenz und globale Scheduler-/Heartbeat-Integration offen.

## Kurzbild

LIARA ist eine entkoppelte lokale KI-Plattform. Der kanonische Runtime-Code liegt unter `services/`. Die aktive Python-Paketkonfiguration in `pyproject.toml` installiert `services*` als Paket.

Der aktuelle Kernpfad ist:

```text
Client / Frontend / CLI
-> liara-api
-> Orchestrator
-> Tools / Memory / Inference / Judge
-> Antwort, Artefakte, Memory Writes
```

Im Containerbetrieb kommt fuer den App-Pfad hinzu:

```text
liara-api:8010
-> liara-memory:8020
-> Postgres / Redis / Qdrant / Neo4j / Chroma

liara-api:8010
-> primaer lokaler llama.cpp Server
-> Fallback/Alternative: Ollama, OpenVINO, NPU Helper

liara-memory:8020
-> externer Embedding-Service per IP/Host, lokal typischerweise 127.0.0.1:8030
```

## Warum diese Trennung existiert

Die Architektur trennt bewusst:

- API-Zugriff und Session-/Datei-Handling
- Orchestrierung, Routing, Planung, Kontextaufbau und Validierung
- Memory-Grenze mit austauschbaren Backends
- Inferenz-Grenze mit Provider-Fallbacks
- deterministische Tools
- Sicherheits-, Judge- und Reward-Pruefungen
- native und terminalbasierte Bedienoberflaechen

Das reduziert Kopplung: API-Endpunkte muessen keine Datenbankdetails kennen, der Orchestrator muss keine HTTP-Details der Clients kennen, und Memory-Zugriffe koennen lokal oder remote ueber denselben Adapterpfad laufen.

## DDNA, Faehigkeit und technische Expression

Die DDNA ist nicht mit der aktuellen Service- oder Frontendstruktur
gleichzusetzen. Das Genome Cockpit zeigt die identitaetsorientierte
Gen-Projektion; die Architecture Map zeigt deren technische Umsetzung mit
Komponenten, Reifegrad, Codepfaden und Evidenz.

Insbesondere gilt:

```text
Faehigkeit != Service != Runtime-Instanz
```

Ein Gen kann seine technische Expression wechseln, ohne aus LIARAs DDNA zu
verschwinden. Mehrere Gene duerfen durch denselben Service ausgedrueckt werden.
Das Genome Cockpit waechst durch `vision`, `hearing` und `speech` von 12 auf 15
Gene; die sechs Primary Genes bleiben als uebergeordnete Familien bestehen.

Kanonische Definition und Umsetzungsplan:
`docs/01_architektur/liara-ddna.md`.

## Aktive Service-Domaenen

| Domaene | Pfad | Rolle |
| --- | --- | --- |
| API | `services/api/app.py` | FastAPI-Einstieg, Chat, Streaming, Sessions, Dateien, Tools, Admin-Endpoints |
| Orchestrator | `services/orchestrator/` | Routing, Planung, Kontext, Toolausfuehrung, LLM-Aufruf, Validation, Retry, Graph-Persistenz |
| Memory | `services/memory/`, `services/memory_adapter.py` | History, Facts, Retrieval, Context, Relations, Graph-v2, Health |
| Embedding | `src/emeddingserver/`, `services/embedding/`, `services/embedding_dev/` | Externer nativer C++ OpenVINO-Embedding-Service; Python nur Fallback/Wrapper |
| Inference | `services/inference/` | Provider Gateway; primaerer Pfad ist llama.cpp, mit Ollama/OpenVINO/NPU Helper als Fallbacks oder Spezialpfade |
| Tools | `services/tools/` | Registry, Coordinator, eingebaute Tools |
| Judge | `services/judge/` | Pre-/Post-Action-Pruefungen, Reward-Anbindung |
| Reward Model | `services/reward_model/` | Scoring, Dataset-Generator, Routing-Signale |
| Simulation | `services/simulation/` | Safe Simulation Mode (Mock) sowie native WSL-Session-Runtime |
| Heartbeat | `services/heartbeat/`, `services/contracts/heartbeat.py` | Eigenstaendige Wahrnehmungsinstanz fuer normalisierte Ressourcenmessungen, Zustandskurven und Scheduler-Evidenz |
| CLI/TUI | `services/cli/`, `services/tui/`, `frontend/tex-ui/` | Textual Chat, aktiver Workspace-Explorer, Admin Console, Shell, Monitor-Tools |

## Native WSL-Ausfuehrungsgrenze

Der kanonische LIARA-Code bleibt lokal unter `C:\ai\LIARA`. Reale Tests,
Compute-Aufgaben und veraendernde Simulationen koennen in einer temporaeren,
nativen WSL-Session ausgefuehrt werden:

```text
C:\ai\LIARA (kanonisch, kein WSL-Write-back)
-> gefilterter Snapshot
-> /home/liara/workspace/sessions/<session-id>/source (read-only)
-> /home/liara/workspace/sessions/<session-id>/work (veraenderbar)
-> bestehender policy-gated /sys-Executor, Python oder Julia
-> Patch + Kandidat + Hashes
-> ai-validator / Governance
-> kontrollierte Uebernahme ausserhalb der Session
```

Die Runtime liegt in `services/simulation/wsl_session_runtime.py`. Der
Lifecycle wird als Tool `wsl_session` registriert; Befehle laufen weiterhin
ueber `WslExecutorTool`. Die Session selbst besitzt daher weder einen direkten
Schreibpfad zum kanonischen Projekt noch einen zweiten, unkontrollierten
Command-Executor.

Diese reale Session-Ausfuehrung ist vom Mock-basierten Safe Simulation Mode zu
trennen. Details: `docs/WSL_SESSION_RUNTIME.md`.

Komplexe Implementierungsauftraege koennen zusaetzlich den begrenzten
Workspace-Agenten verwenden. Dieser erzeugt einen typisierten Plan, fuehrt
jeden Schritt einzeln ueber den bestehenden `sys`-Coordinator aus und gibt den
Folgeschritt erst nach erfolgreicher Beobachtung beziehungsweise verifizierter
Mutation frei. Der letzte Gate ist der vorhandene `ai-validator`; ein
fehlgeschlagener oder nicht erreichbarer Validator wird nicht als Erfolg
umgedeutet.

## Persistenzmodell

Der dokumentierte Memory-Pfad ist adapterbasiert:

```text
Orchestrator/API
-> ensure_memory_service_adapter(...)
-> InProcessMemoryAdapter oder RemoteMemoryAdapter
-> MemoryServiceStore
-> SessionStore / FactStore / RetrievalIndex / ContextStore / GraphStore
-> Redis / Postgres / Qdrant / Chroma / Neo4j
```

Der vorhandene Snapshot `docs/LIARA_SNAPSHOT_2026-04-28.md` beschreibt Graph-v2 als validierten Pfad:

```text
API (8010)
-> Orchestrator
-> persist_run_to_graph_v2()
-> RemoteMemoryAdapter
-> Memory Service (8020)
-> GraphStore
-> Neo4j
```

## Lokaler Runtime-Stack

`docker-compose.yml` definiert:

- immer verfuegbare Infrastruktur: Postgres, Redis, Qdrant, Neo4j, Chroma
- Profil `app`: `liara-memory`, `liara-api`

Embedding laeuft bewusst nicht in Docker Compose. Alle Compose-Services sprechen Embedding ueber `EMBEDDING_SERVICE_BASE_URL` als externen IP-/Host-Endpunkt an. Lokal bedeutet das aus Containern heraus typischerweise `http://host.docker.internal:8030`; ausserhalb von Docker typischerweise `http://127.0.0.1:8030`.

Der lokale Laufzeit-Snapshot vom 2026-07-14 hatte sowohl llama.cpp als auch
Ollama aktiv. Die effektive lokale `.env` setzte `DEFAULT_LLM_PROVIDER=ollama`,
der Code-Default ist `ll_ol_fallback` und das Compose-App-Profil setzt
`hybrid`. Diese Drift ist offen; deshalb darf keine einzelne Variante ohne
Angabe des gestarteten Profils als allgemeiner Primaerpfad bezeichnet werden.

Die vorhandene `provider_selection` ist eine Anfrage-/Helper-Auswahl im
Orchestrator. Der native LiNeP-Scheduler-Core und der aktive Embedding-
Heartbeat bilden eine andere, teilweise integrierte Runtime-Ebene. Ein
globaler Ressourcen-Scheduler fuer alle Worker ist noch nicht implementiert.

Die neue `liara-heartbeat`-Instanz ist von beiden Ebenen getrennt. Sie liest
Ressourcenwerte ueber austauschbare Adapter, normalisiert sie in einen
herstellerneutralen Contract und erzeugt aus dem Zeitfenster eine
Zustandskurve. Sie trifft keine Scheduling- oder Ausfuehrungsentscheidung.

```text
Native Reader / JSON / gemappter CSV-Exporter (z. B. HWiNFO)
-> ResourceObservation
-> HeartbeatSnapshot + StateCurve
-> spaeter LiNeP / Scheduler / Helper-Mandat / Operations-UI
```

## Nicht-kanonische oder besondere Bereiche

- `src/llama.cpp` ist ein grosser vendored/native Build-Bereich. Er ist nicht der LIARA-Python-Servicecode, aber fuer den primaeren lokalen Inference-Pfad relevant.
- `backups/`, `logs/`, `build/`, `artifacts/`, `llama-builds-final/` enthalten Laufzeit-, Build- oder Sicherungsdaten.
- `frontend/gtk-ui-backup-*` und `frontend/server-manager-backup-*` sind Backup-Staende.
