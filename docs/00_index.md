# LIARA – Ist-Stand und Übergabe

Stand: 2026-08-11  

> Kanonischer Einstieg fuer neue Menschen und KI-Assistenten:
> [`../LIARA_START_HERE.md`](../LIARA_START_HERE.md). Das dort verlinkte
> fuenfteilige Paket bietet eine gemeinsame kompakte Ausgangsbasis; dieses
> Dokument bleibt die ausfuehrliche Status- und Uebergabequelle.

Quelle: lokaler Workspace `C:\ai\LIARA`, Quellcode-, Konfigurations-,
Test- und Laufzeitsicht. Der Projektroot ist derzeit kein Git-Repository;
Commit-SHA, Branch und sauberer Git-Diff stehen deshalb nicht als
Übergabebeleg zur Verfügung.

## Zweck und Verbindlichkeit

Dieses Dokument ist der Einstieg für die Weiterentwicklung mit einem anderen
Coding-Assistenten. Es beschreibt den nachprüfbaren Ist-Zustand. Ältere
Roadmaps, Snapshots und Build-Berichte bleiben historische Quellen, dürfen
aber nicht ohne Abgleich mit diesem Dokument und dem Code als aktueller Stand
verwendet werden.

Statusbegriffe:

- **Implementiert**: aktiver Codepfad vorhanden und durch Tests oder einen
  aktuellen Lauf belegt.
- **Teilweise implementiert**: Contract oder Teilpfad vorhanden, aber nicht
  vollständig integriert, nicht aktiv betrieben oder nicht grün getestet.
- **Geplant**: dokumentierte Zielrichtung ohne vollständigen aktiven Pfad.
- **Verworfen/abgelöst**: nicht mehr als aktueller Architekturpfad verwenden.

Quellcode hat Vorrang vor Statusbehauptungen in älteren Dokumenten. Für die
Audit-Abgrenzung gilt zusätzlich `docs/AUDIT_SOURCE_OF_TRUTH.md`.

## Kurzfazit

LIARA ist eine lokal funktionsfähige, mehrschichtige KI-Orchestrierungsplattform
im Entwicklungszustand. API, Memory-Service, Store-Backends, lokale Inferenz,
nativer NPU-Embeddingdienst, policy-gated WSL-Ausführung, Validator und
Textual-Frontend laufen auf dem geprüften Rechner. Das System ist **nicht als
production-ready einzustufen**: Die vollständige Unit-Suite ist verifiziert grün (1382 passed, 0 failed),
öffentliche HTTP-Endpunkte besitzen keine durchgängige Authentisierung,
Governance-Enforcement ist standardmäßig nicht aktiviert, Konfigurationen
driften auseinander und einige Scheduler-/Helper-Ziele sind nur teilweise
integriert.

## Aktueller Laufzeit-Snapshot

Am 2026-07-14 wurden folgende Zustände read-only geprüft:

| Komponente | Zustand | Beleg |
| --- | --- | --- |
| `liara-api` | aktiv, Port 8010 | `GET /health` = 200 |
| `liara-memory` | aktiv, Port 8020 | `GET /health` und `/health/backends` = 200 |
| Postgres, Redis, Qdrant, Neo4j, Chroma | aktiv/healthy | `docker compose ps -a` |
| ai-validator Container | aktiv/healthy | Docker-Healthcheck; kein HTTP-Port veröffentlicht |
| nativer Embedding-Service | aktiv, Port 8030, NPU | Health meldet `openvino-cpp`, 1024 Dimensionen |
| LiNeP im Embedding-Service | aktiv | Worker 30, TCP 8767, Heartbeat 8768 |
| llama.cpp | aktiv, Port 8000 | `GET /health` = 200 |
| Ollama | aktiv, Port 11434 | `/api/tags` liefert lokale und Cloud-Modelle |
| OpenVINO NPU Helper | aktiv, Port 8040 | MiniCPM-o 2.6 INT4 als OpenVINO-VLMPipeline auf NPU; Health 200 |
| Self Observer | aktiv, Port 8060 | `GET /health`, `/v1/state` und API-Proxy `/operations/self-observer` |
| Debian WSL | aktiv | Nutzer `liara`, Home `/home/liara` |
| Python/Julia in WSL | aktiv | Python 3.13.5, Julia 1.12.6 |
| Textual Frontend | aktiv | Prozess `frontend/tex-ui/main.py` |

Der API- und Memory-Service laufen aktuell als lokale Python-Prozesse, nicht
als `liara-api`/`liara-memory`-Compose-Container. Der Compose-Stack betreibt
die Store-Infrastruktur und den Validator.

## Statusmatrix

### Implementiert

| Bereich | Tatsächlicher Stand | Hauptbelege |
| --- | --- | --- |
| HTTP-API | Chat, SSE-Streaming, History, Sessions, Uploads, Artefakte, Tools, Compute, Audit und SYS-Governance-Endpunkte | `services/api/app.py` |
| Orchestrator | Routing, Planung, Kontext/Evidenz, Tools, Provider-Auswahl, Validation/Judge, Retry, Graph-Persistenz | `services/orchestrator/orchestrator.py`, `defs/` |
| Memory-Grenze | In-process- und Remote-Adapter; History, Facts, Retrieval, Context, Relations und Graph-v2 | `services/memory_adapter.py`, `services/memory/` |
| Manuelles Staging/Dreaming | Stage/List/Discard/Consolidate, manuelle Runs, Proposal-Liste und Entscheidungen; Frontend-Begriff `Dreaming`, Backend-Fluss Staging -> Proposal -> Decision | `services/memory/app.py`, `services/contracts/memory_dreaming.py`, `docs/02_services/liara-dreaming.md` |
| Inference-Gateway | llama.cpp, Ollama GPU/CPU, OpenVINO, NPU-Helper-Adapter, Fallback, Hybrid und Circuit Breaker | `services/inference/gateway.py` |
| Native Embeddings | C++/OpenVINO HTTP-Service, NPU-Betrieb, LiNeP TCP/UDP, Embedding und Consensus | `src/emeddingserver/`, `workers/embedding/exec/` |
| Ressourcen-Heartbeat | Eigenstaendige Instanz mit kanonischen Messwerten, Quellenadaptern, Snapshot, Zustandskurve und Ressourcenhuelle | `services/heartbeat/`, `services/contracts/heartbeat.py` |
| Self Observer | Eigenstaendige read-only Wahrnehmung plus getrenntes Assurance-Gate; normalisiert Evidenz, erkennt Ruhe ueber Hysterese und begrenzt optionale Validator-Einreichungen | `services/self_observer/`, `services/contracts/self_observer.py` |
| Tool Registry | `sys`, `orientation`, `compute.run`, `compute.generate`, `plot_chart`, `wsl_session` | `services/tools/registry.py` |
| Öffentliche Tool-API | `sys`, `orientation`, `plot_chart`, `wsl_session` | Live `GET /tools` am 2026-07-14 |
| SYS/WSL | strukturierte Argumentlisten, Command-Policy, Path-Confinement, Audit, verifizierte Mutationen | `wsl_executor.py`, `sys_command_policy.py`, `sys_audit.py` |
| Workspace-Agent | komplexe Aufträge erkennen, typisierten Plan ausführen, Mathematik-Gate, Schrittbeobachtung, Validator-Gate | `services/orchestrator/workspace_agent.py` |
| Dependency-Recovery | allowlistetes `venv-pip install/show` in der WSL-Workspace-`.venv`, danach Tests | Workspace-Agent und SYS-Policy |
| Native WSL-Sessions | Snapshot, read-only `source`, veränderbares `work`, collect, hashes, validate, destroy | `services/simulation/wsl_session_runtime.py` |
| Safe Simulation Mode | Toolausführung durch typisierte Mock-Ergebnisse ersetzen | `services/tools/coordinator.py`, `mock_result_generator.py` |
| Validator-Jobs | Submit/Status/Result, asynchroner Lifecycle, echter Docker-Compose-Worker | `services/memory/store.py`, `workers/ai-validator/` |
| Bedienung | CLI, Service-Textual-Chat, aktiver `frontend/tex-ui`, mehrere Admin-TUIs | `services/cli/`, `services/tui/`, `frontend/tex-ui/` |
| OpenAI-Kompatibilität | minimale Service-Bridge und umfangreichere Continue-Bridge | `services/openai_bridge/`, `scripts/continue_openai_bridge.py` |

### Teilweise implementiert

| Bereich | Fehlender oder unsicherer Teil |
| --- | --- |
| Unit-Testbaseline | 22 Fehler; davon 16 durch nicht migrierte Testadapter für Graph-v2-Abstract-Methoden |
| Validator-Auswertung | Docker-Ausführung ist real; der Memory-Pfad bildet Nicht-Null-Exitcodes primär als generisches Finding ab und parst nicht alle Worker-Reports in strukturierte Einzelbefunde |
| SYS-Governance | Proposal-/Decision-/Audit-Pfad vorhanden; Enforcement ist bei fehlendem `LIARA_SYS_GOVERNANCE_ENFORCE` aus |
| Provider-Scheduler | `provider_selection.py` und NPU-Helper-Offload sind integriert; dies ist kein eigenständiger globaler Ressourcen-Scheduler |
| NPU Helper | HTTP-Service und Provider-Adapter aktiv; Retrieval-Strukturaufgaben nutzen direkten `/infer`-Transport, das produktive Retrieval-Gate bleibt wegen Wiederholungsdrift beim Main-Provider |
| LiNeP-Scheduler | Scheduler-Core, Score Engine, Slot Registry und Tests liegen als nativer Snapshot vor; der Embedding-Slot sendet Heartbeats, aber eine vollständige globale LIARA-Ressourcensteuerung ist nicht an API/Orchestrator angebunden |
| Heartbeat-Integration | Die Ressourcen-Heartbeat-Instanz ist implementiert; LiNeP-Transport, Helper-Mandate, Scheduler-Konsum und Multi-Node-Aggregation fehlen noch |
| Self-Observer-Kontrollkreis | Beobachtung, Persistenz, API, Architektur-Live-Evidenz und Assurance-Gate sind implementiert; Orchestrator-Konsum, Arbeitsfreigabe und Dreaming-Ausfuehrung bleiben bewusst unverbunden |
| Dreaming-Scheduler | Contracts kennen `scheduled`; Store meldet fest `scheduler_enabled=false`, `manual_only` |
| Queue-Worker | Redis-Stream-Code und Worker-Entrypoints existieren; sie liefen im geprüften Snapshot nicht als eigene Prozesse |
| Frontends | Textual-Frontend ist aktiv, WMTool-Liara ist implementiert; mehrere parallele/alte UI-Bäume sind nicht konsolidiert |
| OpenAI Bridges | Zwei Implementierungen mit unterschiedlichem Umfang; keine davon lief im Snapshot auf 8011 |
| Authentisierung | lokale Sicherheits-/Policy-Schichten existieren, aber keine durchgängige API-Authentisierung/TLS-Grenze für einen Netzwerkbetrieb |
| Temporäre OS-Nutzer | WSL-Sessions sind verzeichnis- und policy-isoliert, laufen aber noch unter dem festen Nutzer `liara` |
| Kontrollierte Übernahme | Kandidat/Patch/Validator sind vorhanden; automatischer Write-back in den Windows-Projektroot ist bewusst nicht implementiert |

### Geplant

- LiNeP-/Scheduler-Anbindung und Multi-Node-Aggregation des implementierten
  Ressourcen-Heartbeats fuer Helper, CoWorker und weitere Worker;
- temporäre WSL-Ausführungsnutzer, sobald Julia/Toolchains nicht mehr nur im
  privaten Home von `liara` liegen;
- Promotion validierter Kandidaten in den Windows-Projektroot auf Basis des
  implementierten begrenzten SYS-Apply-/Rollback-Vertrags;
- vollständige strukturierte Übernahme der ai-validator-Reports;
- Konsolidierung der Frontends und der zwei OpenAI-Bridge-Pfade;
- weitere Zerlegung der sehr großen Integrationsmodule ohne Contract-Bruch;
- produktionsfähige Authentisierung, Secret-Verwaltung und Deployment-Härtung.

### Verworfen oder abgelöst

- `/mnt/c/ai/LIARA` als regulärer WSL-Arbeitsroot: Windows-Automount ist für
  die LIARA-Ausführungsgrenze deaktiviert; Tests und Simulationen liegen nativ
  unter `/home/liara/workspace`.
- Erfolg nur aufgrund einer Modellbehauptung: Writes gelten erst nach
  Zustandsprüfung/Hash-Evidenz als erfolgreich.
- freie Shellketten als agentischer Hauptpfad: direkte, typisierte Argumente
  und SYS-Policy sind der aktuelle Pfad.
- automatische Selbstveränderung des kanonischen Projektroots: WSL sammelt
  Kandidaten; Freigabe und Übernahme bleiben getrennt.
- alte Direkttools unter `services/tools/old/` als öffentliche Toolfläche:
  sie bleiben nur für Altcode/Tests, sind nicht registriert.
- Safe Simulation Mode als Ersatz für echte Ausführung: er ist ausdrücklich
  ein Mock-/Dry-Run-Pfad.
- OpenAI/Continue-Bridge als Agentenlogik: sie ist nur ein Formatadapter vor
  der LIARA-API.
- `planner_clean.py` und `planner_fixed.py` als aktive Planner: der aktive
  Importpfad verwendet `services/orchestrator/planner.py`.

## Tatsächliche Datenflüsse

### Normaler Chat

```text
Client
-> POST /chat oder /chat/stream
-> API-Safety und Request-Normalisierung
-> Orchestrator
-> InputSituationProfiler (Analyze/Think/Answer/Plan/Act + Kontext/Mood/Budget)
-> Router/Planner + Context/Evidence
-> optional ToolCoordinator -> Tool
-> InferenceGateway -> Provider
-> ResponseValidator/Judge/Reward
-> History + optional Graph-v2-Persistenz
-> ChatResponse oder SSE final/done
```

### Komplexer Workspace-Auftrag

```text
Chat-Request
-> is_complex_workspace_request
-> zentral ausgewählter Planner-Provider
-> typisierter WorkspacePlan
-> lokale Julia workspace_budget.jl (Python-Fallback)
-> Schritt einzeln via ToolCoordinator -> sys -> Debian WSL
-> Read-after-write/Hash-Verifikation
-> optional allowlistete Workspace-.venv-Synchronisierung
-> python -m pytest in WSL
-> RemoteMemoryAdapter -> /validator/submit|status|result
-> Docker ai-validator
-> persistiertes workspace_agent_run-Artefakt
```

Der nächste Schritt wird nur nach erfolgreicher Beobachtung freigegeben.
Run-Kommandos und Append-Writes werden nicht automatisch wiederholt.

### Memory und Retrieval

```text
API/Orchestrator
-> MemoryServiceAdapter
-> in-process Store ODER HTTP zu liara-memory:8020
-> Redis/Postgres/Qdrant/Chroma/Neo4j
-> externer Embedding-Service:8030
```

### Native Embedding-/LiNeP-Grenze

```text
Memory HTTP -> Embedding HTTP :8030 -> OpenVINO/NPU

nativer Scheduler/Worker-Pfad:
LiNeP UDP heartbeat :8768
LiNeP TCP EMBED/CONSENSUS :8767
```

Der Orchestrator spricht den Embeddingdienst über den Memory-/HTTP-Contract,
nicht direkt über LiNeP.

### Validator

```text
Client/Workspace-Agent
-> liara-memory /validator/submit
-> queued -> running -> completed|failed
-> docker compose run ai-validator <scope>
-> /validator/status und /validator/result
```

## Schnittstellen

Die aktuelle vollständige Route-Liste steht in
`docs/03_apis/current-api-surface.md`. Wichtig:

- API: `8010`
- Memory + Staging + Dreaming + Validator: `8020`
- nativer Embedding-Service: `8030`
- OpenVINO Inferenz und MiniCPM-o Speech: `8040`, aktiv; Text/Helper auf NPU,
  TTS derzeit als CPU-Referenz
- OpenAI/Continue Bridge: typischerweise `8011`, optionaler Formatadapter
- llama.cpp: `8000`
- Ollama: `11434`
- LiNeP: TCP `8767`, UDP-Heartbeat `8768`

Die API ist für lokalen Betrieb ausgelegt. Ohne vorgeschaltete
Authentisierung/TLS darf sie nicht unverändert auf ein untrusted Netzwerk
exponiert werden.

## Wichtige Dateien

| Datei/Pfad | Bedeutung |
| --- | --- |
| `services/api/app.py` | zentraler FastAPI-Einstieg, öffentliche HTTP-Fläche |
| `services/orchestrator/orchestrator.py` | Hauptpipeline und größter Integrationspunkt |
| `services/orchestrator/workspace_agent.py` | typisierter Coding-/Workspace-Regelkreis |
| `services/orchestrator/defs/provider_selection.py` | aktuelle Provider-/Helper-Auswahl, kein globaler Scheduler |
| `services/contracts/` | Service-, Orchestrator-, Memory- und Validator-Contracts |
| `services/memory/app.py` | Memory-HTTP-Endpunkte |
| `services/memory/store.py` | Store-Koordination, Staging/Dreaming, Validator-Jobs |
| `docs/02_services/liara-dreaming.md` | Dreaming-/Staging-Begriffe, aktueller `manual_only`-Stand, API-/Memory-Fluss und offene Gates |
| `services/memory_adapter.py` | In-process-/Remote-Servicegrenze |
| `services/inference/gateway.py` | Provider, Fallbacks und Circuit Breaker |
| `services/tools/registry.py` | registrierte Tools |
| `services/tools/builtin/wsl_executor.py` | reale SYS-Ausführung in WSL |
| `services/tools/builtin/sys_command_policy.py` | Command-/Argument-Policy |
| `services/tools/builtin/sys_audit.py` | Trace-/Risiko-/Mutationsaudit |
| `services/simulation/wsl_session_runtime.py` | native Test-/Simulationssessions |
| `services/simulation/models/*.jl` | allowlistete Julia-Modelle |
| `src/emeddingserver/` | nativer C++ Embedding-/LiNeP-Quellcode (Schreibweise historisch) |
| `workers/embedding/exec/` | aktuell gestartetes natives Embedding-Runtimepaket |
| `workers/ai-validator/` | unabhängiger Docker-Validator |
| `frontend/tex-ui/` | aktuell verwendetes Textual-Frontend mit Workspace-Explorer |
| `frontend/WMTool-Liara/` | native GTK/C-UI, parallel vorhanden |
| `scripts/continue_openai_bridge.py` | umfangreicher Continue/OpenAI-Adapter |
| `docker-compose.yml` | Store-Infrastruktur, Root-Validator und Legacy-Containerprofil fuer API/Memory |
| `.env`, `.env.example`, `services/config/settings.py` | effektive lokale, Beispiel- und Code-Defaults; derzeit nicht vollständig konsistent |
| `pyproject.toml`, `requirements-*.txt` | zwei derzeit driftende Dependency-Quellen |

## Start- und Diagnosebefehle

Alle Befehle aus `C:\ai\LIARA`. Für reproduzierbare Python-Aufrufe den
expliziten Interpreter verwenden.

```powershell
# Infrastruktur und Root-Validator; API/Memory laufen lokal ueber service_guard
docker compose up -d
docker compose ps -a

# Lokale Kernprozesse
.\.venv\Scripts\python.exe scripts\service_guard.py start --service memory --repo-root C:\ai\LIARA
.\.venv\Scripts\python.exe scripts\service_guard.py start --service api --repo-root C:\ai\LIARA

# Nativer Embedding-Service
.\.venv\Scripts\python.exe scripts\service_guard.py start --service embedding --repo-root C:\ai\LIARA

# Aktuell verwendetes Frontend
.\.venv\Scripts\python.exe .\frontend\tex-ui\main.py --base-url http://127.0.0.1:8010 --mode stream

# CLI/TUI
.\.venv\Scripts\python.exe -m services.cli.main repl
.\.venv\Scripts\python.exe -m services.tui.sys_audit_tui --scope sys --textual

# Continue Bridge, falls benötigt
.\.venv\Scripts\python.exe -m uvicorn scripts.continue_openai_bridge:app --host 127.0.0.1 --port 8011
```

Health:

```powershell
curl.exe http://127.0.0.1:8010/health
curl.exe http://127.0.0.1:8010/health/backends
curl.exe http://127.0.0.1:8020/health/backends
curl.exe http://127.0.0.1:8030/health
curl.exe http://127.0.0.1:8000/health
wsl -d Debian -- /home/liara/.juliaup/bin/julia --version
```

WSL-Sessions:

```powershell
.\.venv\Scripts\python.exe scripts\wsl_session_cli.py plan
.\.venv\Scripts\python.exe scripts\wsl_session_cli.py create --label handover-test
.\.venv\Scripts\python.exe scripts\wsl_session_cli.py exec <session-id> -- julia --version
.\.venv\Scripts\python.exe scripts\wsl_session_cli.py collect <session-id>
.\.venv\Scripts\python.exe scripts\wsl_session_cli.py destroy <session-id>
```

## Testbaseline vom 2026-07-14

```text
python -m pytest --collect-only -q tests
-> 1265 Tests gesammelt in 5,64 s

python -m pytest -q tests/unit
-> 1124 bestanden, 22 fehlgeschlagen, 5 übersprungen
-> Laufzeit 367,84 s

fokussierte Workspace/SYS/Audit-Regression
-> 163 bestanden in 40,24 s
```

Die 22 Unit-Fehler sind nicht 22 unabhängige Produktdefekte:

- 16 Fehler: Testadapter implementieren die neuen abstrakten Graph-v2-Methoden
  des Memory-Adapters noch nicht.
- 1 Fehler: API-Fake besitzt das neue `llm_generation`-Attribut nicht.
- 1 Fehler: Validator-Test-Mock akzeptiert den neuen Parameter `session_id`
  noch nicht.
- 3 Fehler: JuliaBridge-Testmocks bilden die aktuelle gestufte
  WSL-Staging-/Antwortsequenz nicht korrekt ab.
- 1 Fehler: UTC-Test erwartet das Formatargument an Position 0, die reale
  Auswahl setzt zuerst `-u`; Erwartung und Contract müssen entschieden werden.

`pytest` ohne expliziten Testpfad ist derzeit **nicht zulässig**: Pytest
rekursiert in `artifacts/wsl_sessions/**/candidate/`, importiert dort
`scripts/test_policy_smoke.py` und endet mit internem `SystemExit`. Es fehlen
`testpaths`/`norecursedirs` in der Pytest-Konfiguration.

Empfohlene Befehle bis zur Reparatur:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit
.\.venv\Scripts\python.exe -m pytest -q tests/integration -m "not live and not live_regression"
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_workspace_agent.py tests/unit/test_wsl_executor.py tests/unit/test_sys_command_policy.py tests/unit/test_sys_audit.py
```

Live-Tests nur mit bewusst gestarteten Services und expliziten Markern
ausführen. `ruff` ist in der aktuellen Windows-`.venv` nicht installiert;
`black` und `mypy` sind die dokumentierten Dev-Werkzeuge.

## Bekannte Einschränkungen und Risiken

1. **Unit-Baseline wieder grün.** Der vollständige Lauf nach Contract-
   Migration endet mit 1189 erfolgreichen und 4 bewusst übersprungenen Julia-
   Paritätstests (`RUN_JULIA_PARITY_TESTS=1` aktiviert sie).
2. **Pytest-Scope eingegrenzt.** `testpaths = ["tests"]` und `norecursedirs`
   verhindern Imports aus Artefakten, Backups, Builds, Scripts und WSL-
   Kandidaten. Root-Smoke-Skripte bleiben nur explizit startbar.
3. **Konfigurationsdrift.** `.env` nutzt aktuell `DEFAULT_LLM_PROVIDER=ollama`,
   `.env.example` `ll_ol_fallback`, Compose `hybrid`.
4. **Dependency-Drift.** Unter anderem FastAPI, ChromaDB, Pillow und Black
   unterscheiden sich zwischen `pyproject.toml` und Requirements-Profilen.
5. **Keine durchgängige Netzwerk-Auth.** Betrieb nur auf vertrauenswürdigem
   lokalen Host/Netz.
6. **Governance-Enforcement opt-in.** Proposal/Audit ersetzt ohne ENV-Flag
   keine harte Freigabepflicht.
7. **NPU-Helper-Qualitaetsgate offen.** MiniCPM-o 2.6 INT4 ist auf Port 8040
   aktiv; identische Retrieval-Intent-Laeufe bewahren Quellen und
   Identifikatoren noch nicht wiederholbar. Der Main-Provider bleibt dafuer
   produktiv, Helper-Fehler fallen explizit zurueck.
8. **Validator-Findings noch grob.** Workerreports müssen strukturiert in das
   Result-Contract übernommen werden.
9. **Große Integrationsmodule.** `orchestrator.py`, `memory/store.py` und
   `api/app.py` sind mit rund 4020, 3228 und 1954 Zeilen weiterhin
   Änderungs-Hotspots.
10. **Kein Git-Metadatenanker.** Übergaben können Änderungen nicht sauber an
    Commit/Branch binden.
11. **Lokale Default-Passwörter.** Compose-Credentials sind nur für lokale
    Entwicklung geeignet.
12. **Laufende Prozesse laden Code nicht automatisch neu.** Nach Änderungen
    API/Memory/Worker kontrolliert neu starten und Health erneut prüfen.

## Priorisierte nächste Schritte

### P0 – reproduzierbare Baseline herstellen

1. [x] Pytest auf kanonische Testpfade begrenzen.
   - Akzeptanz: `python -m pytest --collect-only -q` sammelt ausschließlich
     kanonische Tests und importiert nichts aus `artifacts/`, `build/`,
     `backups/`, `dist/` oder WSL-Kandidaten.
2. [x] Test-Doubles auf aktuelle Contracts migrieren.
   - Akzeptanz: alle Fake-/ServiceMode-MemoryAdapter implementieren die neun
     Graph-v2-Methoden oder verwenden einen gemeinsamen vollständigen Fake.
   - Akzeptanz: API-Fake besitzt `llm_generation`; Validator-Fake akzeptiert
     `session_id`.
3. [x] JuliaBridge- und UTC-Tests gegen den beabsichtigten aktuellen Contract
   entscheiden und korrigieren.
   - Akzeptanz erfüllt: `python -m pytest -q tests/unit -rs` endet mit
     Exitcode 0 (`1189 passed, 4 skipped`).

### P0 – Workspace-Agent nach Neustart end-to-end abnehmen

1. API und Memory mit aktuellem Source neu starten.
2. Einen kleinen, deterministischen Worker mit erlaubten Dependencies erzeugen.
3. Nach jedem Write Hash-Evidenz, danach `.venv`-Install/Show, Tests und echten
   ai-validator prüfen.
   - Akzeptanz: alle geplanten Dateien existieren im realen WSL-Workspace.
   - Akzeptanz: keine Mutation wird nur aufgrund von Modelltext bestätigt.
   - Akzeptanz: Tests und Validator stehen auf `completed`, Exitcode 0.
   - Akzeptanz: unbekannte Dependency und Pfadausbruch werden jeweils geblockt.

### P1 – Konfiguration vereinheitlichen

1. Einen kanonischen lokalen Providerdefault festlegen und in Settings,
   `.env.example`, Compose und Doku angleichen.
2. Eine Dependency-Quelle bestimmen oder automatischen Konsistenztest bauen.
   - Akzeptanz: keine widersprüchlichen Constraints für FastAPI, ChromaDB,
     Pillow und Black.
   - Akzeptanz: Container- und Editable-Install verwenden nachweislich
     kompatible Versionen.

### P1 – Validator und Governance härten

1. ai-validator-Reports in strukturierte Findings mit Datei, Zeile, Regel und
   Severity überführen.
2. Lokalen Audit-only- und freigabepflichtigen Modus explizit dokumentieren.
   - Akzeptanz: kontrollierter Negativtest erzeugt mindestens ein konkretes
     Finding, nicht nur `exit_code != 0`.
   - Akzeptanz: bei Enforcement kann ein SYS-Aufruf ohne genehmigte Proposal-ID
     nicht ausgeführt werden.

### P1 – Scheduler/Helper-Betrieb eindeutig machen

1. MiniCPM-INT4-Helper auf Port 8040 mit einem wiederholbaren Intent-/Entity-
   Eval freigeben, bevor `RETRIEVAL_INTENT_PROVIDER` produktiv umgestellt wird.
2. Provider-Auswahl von der geplanten globalen LiNeP-Ressourcensteuerung
   begrifflich und technisch trennen.
   - Akzeptanz: Health-/Routing-Telemetrie zeigt ohne stille Timeouts, ob
     Helper-Offload verfügbar ist.
   - Akzeptanz: ein Helper-Ausfall fällt deterministisch auf den Main-Provider
     zurück.

### P2 – Konsolidierung

- OpenAI-Bridge-Implementierungen zusammenführen;
- aktive, experimentelle und Backup-Frontends klar kennzeichnen;
- unverdrahtete Planner-Varianten und alte Toolmodule archivieren oder löschen;
- große Integrationsmodule entlang vorhandener Contracts aufteilen;
- nach Einrichtung eines Git-Repositories diese Übergabe mit Commit-SHA und
  reproduzierbarem Baseline-Tag versehen.

## Dokumentationsnavigation

| Thema | Datei |
| --- | --- |
| Architektur und Status | `docs/01_architektur/liara-overview.md` |
| DDNA, Genome Cockpit und technische Expression | `docs/01_architektur/liara-ddna.md` |
| API-Service | `docs/02_services/liara-api.md` |
| Orchestrator | `docs/02_services/liara-orchestrator.md` |
| Memory | `docs/02_services/liara-memory.md` |
| Inference | `docs/02_services/liara-inference.md` |
| Embedding/LiNeP | `docs/02_services/liara-embedding.md` |
| Ressourcen-Heartbeat | `docs/02_services/liara-heartbeat.md` |
| Tools/WSL | `docs/02_services/liara-tools.md`, `docs/WSL_SESSION_RUNTIME.md` |
| Frontends | `docs/02_services/liara-frontends.md` |
| API-Routen | `docs/03_apis/current-api-surface.md` |
| Tests | `docs/07_tests/test-overview.md` |
| Security | `docs/08_security/security-boundaries.md` |
| Ports/ENV/Befehle | `docs/09_reference/runtime-reference.md` |
| Historie Juli | `docs/06_build-history/2026-07.md` |
