# Service: liara-api

Stand: 2026-07-14  
Code: `services/api/app.py`

## Aufgabe

`liara-api` ist der zentrale HTTP-Einstiegspunkt. Der Service nimmt Chat-, Session-, Datei-, Tool- und Admin-Anfragen entgegen und verbindet sie mit Orchestrator, Memory-Adapter, Inference-Gateway und Tool-Coordinator.

## Verantwortlichkeiten

- Health und Backend-Health
- Chat und Chat-Streaming
- Session-Snapshots und Session-Metadaten
- Dateiuploads mit Attachment-Pruefung
- Artifact-Auslieferung
- Tool-Liste, Tool-Metadaten und manuelle Tool-Ausfuehrung
- Admin-Sys-Audit-Auswertung
- Compute-Endpunkte fuer `compute.run` und `compute.generate`
- einfache Safety-Refusal-Checks fuer klar schaedliche Anfragen

## Wichtige Abhaengigkeiten

- `services.orchestrator.Orchestrator`
- `services.inference.gateway.InferenceGateway`
- `services.memory_adapter.InProcessMemoryAdapter`, `RemoteMemoryAdapter`
- `services.memory.store.*`
- `services.tools.coordinator.ToolCoordinator`
- `services.tools.registry.get_tool_registry`
- `services.shared.attachment_security`
- `services.shared.sandboxing`
- `services.shared.output_sanitizer`

## Ports und Start

Standard:

```powershell
python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8010
```

Compose-Profil:

```powershell
docker compose --profile app up liara-api
```

Container-Port: `8010`

## Aktive Endpunktgruppen

Siehe `docs/03_apis/current-api-surface.md` fuer die vollstaendige aktuelle Flaeche.

Wichtigste Endpunkte:

- `GET /health`
- `GET /health/backends`
- `POST /chat`
- `POST /chat/stream`
- `GET /history`
- `GET /session`
- `POST /session`
- `POST /files/upload`
- `GET /files/artifact`
- `GET /tools`
- `GET /tools/{tool_name}`
- `POST /tools/{tool_name}/invoke`
- `POST /compute/run`
- `POST /compute/generate`
- `POST /tools/sys/governance/proposals`
- `GET /tools/sys/governance/proposals`
- `GET /tools/sys/governance/events`
- `POST /tools/sys/governance/decisions`
- `POST /tools/sys/governance/actions`

Der SYS-Governance-Pfad bindet eine genehmigte Aktion an `command` und alle
ausfuehrungsrelevanten Parameter. Die kanonische Aktion erhaelt einen
serverseitigen SHA-256-`invocation_digest`. Runtime-Tracefelder wie
`request_id`, `run_id`, `session_id`, `source` und `context` bleiben davon
getrennt. Standardmaessig autorisiert eine Approval genau einen gestarteten
Versuch; auch ein fehlgeschlagener Run verbraucht den Slot, weil
Nebenwirkungen nicht ausgeschlossen werden koennen. Parallele, abweichende
oder bereits verbrauchte Invocations werden abgewiesen. Policy-blockierte
Proposals koennen nicht approved werden.

Der Action-Endpoint trennt die Entscheidung von der Ausfuehrung allgemeiner
Proposals. `apply` konsumiert den genehmigten Single-use-Invoke. Bei einem
Overwrite einer vorhandenen Datei im verwalteten WSL-Workspace wird zuvor ein
hashgebundener Snapshot unter `logs/services/sys_governance_rollback/`
gesichert. `rollback` fuehrt den Snapshot als eigene gebundene
Kompensations-Proposal genau einmal aus und akzeptiert Erfolg erst nach
Read-after-write-Hashpruefung. Nicht reversierbare Aktionsfamilien erhalten
keine scheinbare Rollback-Garantie, sondern `rollback.supported=false`.

Proposal, Decision, Invocation-Attempt und Result werden im separaten
Governance-Eventstream korreliert. Der read-only Event-Endpunkt kann ueber
`proposal_id` gefiltert werden. Die normale SYS-Ausfuehrung bleibt parallel im
SYS Audit sichtbar.

Die zentrale Enforcement-Grenze liegt im `ToolCoordinator`, damit direkte
Orchestrator- und Workspace-Agent-Aufrufe dieselbe Policy wie HTTP-Aufrufe
erhalten. `LIARA_SYS_GOVERNANCE_MODE` kennt:

- `off`: nur Audit;
- `risk_based`: read-only Inspection und durch die W/G/B-Command-Policy
  validierter, rein lesender HTTP(S)-Abruf erlaubt; Mutation, unprofilierter
  Netzwerkzugriff, Installation und freie Codeausfuehrung nur mit gebundener
  Approval;
- `all`: jeder reale SYS-Aufruf braucht eine gebundene Approval.

Der alte Schalter `LIARA_SYS_GOVERNANCE_ENFORCE=1` bleibt kompatibel und wird
als `all` interpretiert, sofern kein expliziter Modus gesetzt ist.

Fuer `curl` bleibt die bestehende Command-Policy die Autoritaet: Whitelist-
Argumente laufen direkt, Greylist-Argumente erst nach ihrer kontextuellen
Validierung. Blacklist-Treffer wie Upload, POST, Credentials, Proxy,
Dateiausgabe oder `file://` werden endgueltig abgewiesen und koennen nicht per
Governance-Approval umgangen werden.

Wenn der Workspace-Agent an dieser Grenze ein strukturiertes
`governance_required` erhaelt, erzeugt er atomar ein Pending-Proposal im
gemeinsamen Governance-Store und liefert `awaiting_decision`. Das Proposal
enthaelt Traceability, Step-Metadaten und die exakt gebundene Aktion. Die
Proposal-Liste und der Decision-Endpunkt synchronisieren den Store bei jedem
Zugriff, sodass dafuer kein API-Neustart erforderlich ist. Die automatische
Fortsetzung des restlichen Plans nach der Entscheidung gehoert noch nicht zum
HTTP-Contract.

## Betriebsgrenzen

- Nicht-oeffentliche Tools werden aus der normalen Tool-Flaeche gefiltert, unter anderem `compute.run`, `compute.generate`, `read_file`, `list_files`, `web_search`.
- Uploads laufen ueber Attachment-Scan und Sandbox-Pfade.
- Memory kann in-process oder remote laufen, gesteuert ueber `MEMORY_MODE`, `MEMORY_SERVICE_BASE_URL` und Adapterlogik.
- `wsl_session` ist neben `sys`, `orientation` und `plot_chart` ueber die
  aktuelle Live-Toolliste sichtbar.
- Die HTTP-Flaeche besitzt noch keine durchgaengige Authentisierung/TLS-Grenze
  und ist daher als lokaler Dienst zu betreiben.

## Aktueller Befund

Der Service ist umfangreich und lokal funktionsfaehig, aber nicht als
production-ready belegt. API, Sicherheitspruefung, Dateiannahme, Chatflow,
Tool-Infrastruktur und Admin-Funktionen liegen in einer Datei. Bei kuenftigen
Aenderungen ist eine Aufteilung in Router-Module sinnvoll, ohne die externe API
zu veraendern.
