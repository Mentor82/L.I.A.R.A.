# Aktuelle API-Flaeche

Stand: 2026-08-11  
Quelle: Endpoint-Deklarationen in `services/api/app.py`, `services/memory/app.py`, `services/embedding/app.py`, `services/embedding_dev/app.py`, `services/heartbeat/app.py`, `services/self_observer/app.py`, `services/openai_bridge/bridge_service.py`, `services/inference/openvino_npu_app.py`.

## liara-api auf Port 8010

Der produktive Dienst bindet an `127.0.0.1:8010`. Browser greifen ueber den
Same-Origin-BFF des Next.js-Webfrontends zu:

```text
:3001/api/liara/{path} -> serverseitig 127.0.0.1:8010/{path}
```

Der streamende Route Handler erhaelt Queryparameter, Requestbody, Status,
relevante Header und Streamingantworten. Client-Abbruch wird an den internen
Request propagiert. Damit bleibt die API vom LAN abgeschottet, waehrend das
Webfrontend auch von einem zweiten Geraet aus funktioniert.

### Health

- `GET /health`
- `GET /health/backends`

### Memory/Admin

- `POST /memory/relations/cleanup-expired`
- `GET /admin/sys-audit/summary`
- `GET /admin/sys-audit/suspicious`
- `GET /admin/sys-audit/presets/{preset_name}`
- `GET /admin/llama-backends`

### Compute

- `POST /compute/run`
- `GET /compute/models`
- `POST /compute/generate`

### Chat

- `POST /chat`
- `POST /chat/stream`
- `GET /history`

### Session

- `GET /session`
- `POST /session`

### Dateien und Artefakte

- `POST /files/upload`
- `GET /files/artifact`

### Speech

- `GET /speech/health`
- `POST /speech/generate` - sessiongebundenes PCM16-WAV-Artefakt
- `POST /speech/stream` - binaerer `audio_stream/v1`-Proxy; Default
  `webm_opus`, alternativ `ogg_opus` oder `pcm_s16le`; mit
  `persist_artifact=true` paralleler transaktionaler WAV-Tee

### Operations (read-only)

- `GET /operations/workspace`
- `GET /operations/dreaming?decision=pending|approved|rejected|all&limit=1..200`
- `GET /operations/graph/subgraph?component=orchestrator|memory&limit=1..25`
- `GET /operations/heartbeat?window_seconds=10..900`
- `GET /operations/self-observer?history_limit=1..240`

`GET /operations/workspace` liefert Workspace-Status und die letzten
persistierten Artefakte fuer lokale Diagnoseoberflaechen. Optional kann mit
`artifact_type=validation|governance|memory|chat` gefiltert werden. Der
Endpunkt setzt `Cache-Control: no-store` und fuehrt keine Mutation aus. Der
Status nennt `store_mode`, kanonischen Workspace- und Artefaktroot sowie die
Zahl der Artefakte je Typ. Artefakte enthalten Pfad, Groesse, SHA-256 und die
gespeicherten Traceability-Felder.

`GET /operations/graph/subgraph` liefert ausschliesslich einen serverseitig
allowlist-basierten Neo4j-Ausschnitt fuer die Living Architecture Map. Freies
Cypher, beliebige Labels und ungefilterte Properties werden nicht akzeptiert.
Die Antwort enthaelt normalisierte `nodes`, `edges`, `truncated`, `query_ms`
und den Memory-Service-Status. `Cache-Control` ist `no-store`.

`GET /operations/heartbeat` ist die kanonische read-only Grenze fuer
Frontends. Die API fasst `HeartbeatSnapshot` und `StateCurve` der
eigenstaendigen Heartbeat-Instanz zusammen. Ein Ausfall der Instanz wird als
typisierte Antwort mit `status=failed` sichtbar; das Frontend greift nicht
direkt auf Port 8050 zu. Die Zielinstanz wird ueber
`LIARA_HEARTBEAT_BASE_URL` konfiguriert, der kurze Proxy-Timeout ueber
`LIARA_HEARTBEAT_PROXY_TIMEOUT_SECONDS`.

`GET /operations/dreaming` liefert einen read-only Snapshot des manuellen
Staging-/Dreaming-Subsystems fuer lokale Diagnoseoberflaechen. Die Antwort
enthaelt Scheduler-Modus, letzten Run, Anzahl staged Items, pending Proposals
und eine begrenzte Proposal-Liste samt normalisierter Assurance- und
Quality-Signal-Projektion. Der Endpunkt startet kein Dreaming, trifft
keine Proposal-Entscheidungen und vergibt keine Mutationsrechte. Der
Frontend-/Architekturbegriff ist `Dreaming`; der technische Backend-Fluss ist
Staging -> Dreaming-/Consolidation-Run -> Proposal -> Decision. Details:
`docs/02_services/liara-dreaming.md`.

`GET /operations/self-observer` liefert den aktuellen `SystemStateEnvelope`
und eine begrenzte Historie der eigenstaendigen Instanz auf Port 8060. Der
Proxy ist read-only und no-store. Er verleiht weder der API noch dem Observer
Scheduler-, Dreaming- oder Mutationsrechte.

### Tools

- `GET /tools`
- `GET /tools/{tool_name}`
- `POST /tools/{tool_name}/invoke`
- `POST /tools/sys/governance/proposals`
- `GET /tools/sys/governance/proposals`
- `GET /tools/sys/governance/events`
- `POST /tools/sys/governance/decisions`
- `POST /tools/sys/governance/actions`

Der geschuetzte Ablauf lautet:

```text
ToolProposal -> Policy Check -> Decision -> Invoke -> SYS Audit
                                      \-> Governance Event Result
```

Allgemeine genehmigte Proposals werden ueber
`POST /tools/sys/governance/actions` mit `action=apply` explizit angewendet.
Der Endpoint verwendet weiterhin den gebundenen Single-use-Invoke und bietet
keinen zweiten Ausfuehrungsweg. Fuer einen `tee`-Overwrite auf eine bereits
vorhandene Datei unter dem verwalteten WSL-Workspace wird vor Apply ein
groessenbegrenzter, SHA-256-gebundener Snapshot persistiert. Ein spaeteres
`action=rollback` erzeugt daraus eine eigene genehmigte, einmalige
Kompensations-Proposal und verifiziert den wiederhergestellten Hash.

Rollback ist in Version 1 bewusst nicht verfuegbar fuer neue Dateien, Append,
Verzeichnisse, Paketinstallationen, Netzwerk oder freie Codeausfuehrung. Diese
Aktionen koennen nach Approval angewendet werden, werden aber im
Transaction-Contract explizit als `supported=false` markiert.

Eine Approval bindet `command` und ausfuehrungsrelevante Parameter ueber
einen serverseitigen SHA-256-`invocation_digest`. Genehmigte, beim Invoke
ausgelassene Parameter werden aus dem Proposal uebernommen; zusaetzliche oder
abweichende Parameter werden abgewiesen. Der Default `max_invocations=1`
autorisiert genau einen gestarteten Versuch. Jeder Attempt verbraucht einen
Slot, weil auch ein fehlgeschlagener Tool-Run bereits Nebenwirkungen gehabt
haben kann. Ein Retry benoetigt einen neuen Proposal oder ein vorab explizit
hoeher gesetztes Limit. Policy-blockierte Proposals sind nicht approvable.

`GET /tools/sys/governance/proposals` liefert Decision- und
Invocation-Aggregate sowie pro Proposal eine `audit_reference`.
`GET /tools/sys/governance/events?proposal_id=...` projiziert den read-only
Eventstream aus Created, Decided, Attempted und Completed/Failed.

`summary.enforcement_mode` zeigt den zentral wirksamen Modus `off`,
`risk_based` oder `all`. Die Pruefung liegt im `ToolCoordinator` und gilt daher
auch fuer interne Orchestrator-/Workspace-Agent-Aufrufe. Unter `risk_based`
bleiben `health`, Zeitabfragen und read-only Datei-/Inventaroperationen
kompatibel. Dasselbe gilt fuer einen von der W/G/B-Policy vollstaendig
validierten read-only `curl`-Abruf ueber HTTP(S). Blacklist-Treffer bleiben
endgueltige Policy-Denials. Unprofilierter Netzwerkzugriff,
Python-/Julia-Fallback, Installation und Workspace-Mutation liefern ohne
gebundene Approval ein strukturiertes `governance_required`-Signal statt
ausgefuehrt zu werden.

Der Workspace-Agent transformiert dieses Signal in ein idempotentes
Pending-Proposal und beendet den aktuellen Run mit `awaiting_decision`. Die
API liest den gemeinsamen Store vor List-, Decision- und gebundenen
Invoke-Operationen neu ein. Damit ist die Uebergabe sofort sichtbar, ohne dass
der API-Prozess neu gestartet werden muss. Ein automatisches Resume des
restlichen Workspace-Plans ist noch nicht Bestandteil dieser Oberflaeche.

Aktuelle public Tool-Oberflaeche:

- `sys`
- `orientation`
- `plot_chart`
- `wsl_session`

Historische Direkttools wie `read_file`, `list_files`, `web_search`, `fetch`, `current_time` und `session_context` gehoeren nicht mehr zur regulaeren public Tool-Flaeche.

## liara-memory auf Port 8020

### History

- `POST /history/append`
- `POST /history/query`

### Facts

- `POST /facts/upsert`
- `POST /facts/query`

### Staging und Dreaming

- `POST /staging/stage`
- `POST /staging/list`
- `POST /staging/touch`
- `POST /staging/discard`
- `POST /staging/consolidate`
- `POST /dreaming/run`
- `GET /dreaming/status`
- `POST /dreaming/proposals`
- `POST /dreaming/proposals/assurance`
- `POST /dreaming/proposals/decision`
- `POST /dreaming/cleanup`

Der Scheduler-Modus ist im aktuellen Store `manual_only`; die Routes sind
implementiert, aber kein periodischer Dreaming-Scheduler ist aktiv. Mutierende
Operationen liegen hier auf `liara-memory`; die zentrale API bietet mit
`GET /operations/dreaming` nur einen read-only Bedien- und Diagnose-Snapshot.

`POST /dreaming/run` akzeptiert optional `include_session_summary=true`,
`summary_max_messages` und `summary_max_chars`. Dadurch entsteht ein
zusaetzliches pending Proposal aus der Session-History; es wird nichts
automatisch in Long-term Memory promoviert.

Mit `include_relation_evidence=true` und `relation_limit=1..50` liest der
Run zusaetzlich bereits akzeptierte, nicht abgelaufene Relationen. Eine Kante
wird nur als `graph_relation`-Evidenz angehaengt, wenn ihr `source` oder
`target` direkt in den Proposal-`source_ids` vorkommt. Der Pfad ist read-only:
Er fuehrt kein Relation-Upsert und keine automatische Promotion aus. Der
Abfragestatus steht in `summary.relation_evidence`.

Mit `include_quality_signals=true` haengt der Run nach dem Relationsabruf eine
deterministische `proposal_quality_signals`-Evidenz an jedes Proposal. Sie
enthaelt strukturelle Complexity-Rohwerte sowie getrennte Quellen- und
Relations-Coverage. Das Signal ist beobachtend, versioniert und hat keine
direkte Freigabe- oder Promotionswirkung. Der Run fasst die Erzeugung unter
`summary.quality_signals` zusammen.

`require_assurance_for_approval=true` markiert die im Run erzeugten Proposals
als assurance-pflichtig. Vor einer Approval muss ein Validator-Job ueber
`POST /dreaming/proposals/assurance` gebunden werden. Sein
`ValidatorJobSubject` muss dieselbe `proposal_id` sowie
`context=dreaming_proposal_assurance` tragen. Nur ein strikter,
abgeschlossener Lauf mit `exit_code=0` und ohne Warning-/Error-Findings ergibt
`passed`. Der serverseitige Proposal-Digest bindet dabei neben dem Proposal-
Kern auch alle nicht vom Validator selbst erzeugten Evidenzen.

`GET /operations/dreaming` ergaenzt jedes Proposal fuer read-only UIs um
`assurance.required`, `verdict`, `blocked`, `validator_job_id`,
`findings_count`, `highest_severity`, strukturierte Artefaktpfade und eine
Audit-Referenz. Auf Snapshot-Ebene stehen aggregierte Verdict-, Required- und
Blocked-Counts. `quality_signals` normalisiert pro Proposal Complexity,
Quellen-/Relations-Coverage und unbedeckte Quellen; auf Snapshot-Ebene stehen
Anzahl vorhandener Signale und Complexity-Level. Diese Projektion vergibt
keine Entscheidungsrechte.

`POST /dreaming/cleanup` arbeitet standardmaessig mit `dry_run=true`. Staged
Items werden nur nach Ablauf ihrer expliziten TTL und ohne Schutz durch ein
pending/approved Proposal entfernt. Bei Proposals sind ausschliesslich
`rejected` Eintraege mit serverseitigem `decision_at` nach Ablauf von
`rejected_retention_seconds` zulaessig. Pending, Approved und Legacy-Eintraege
ohne Entscheidungszeit bleiben erhalten. Audit und Artefakte liegen ausserhalb
dieses Cleanup-Scopes.

### Validator Jobs

- `POST /validator/submit`
- `POST /validator/status`
- `POST /validator/result`

`ValidatorSubmitRequest` kann eine `proposal_id` tragen. Submit-, Status- und
Result-Responses liefern den dazugehoerigen `ValidatorJobSubject` mit
Proposal-ID, serverseitigem Proposal-Digest, Kontext, Scope, Strict-Mode und
Checks zurueck. Dadurch kann ein Report nicht nachtraeglich fuer ein anderes
oder veraendertes Proposal verwendet werden.

### Retrieval

- `POST /retrieval/upsert`
- `POST /retrieval/query`

### Embedding Proxy

- `POST /embedding/generate`

### Context

- `POST /context/search`
- `POST /context/upsert`

### Relations

- `POST /relations/upsert`
- `POST /relations/expand`
- `POST /relations/cleanup-expired`

### Graph-v2

- `POST /graph/agent/upsert`
- `POST /graph/task/upsert`
- `POST /graph/context/upsert`
- `POST /graph/fact/upsert`
- `POST /graph/fact/link`
- `POST /graph/embedding/upsert`
- `POST /graph/embedding/semantic-link`
- `POST /graph/tool/upsert`
- `POST /graph/context/graph`
- `POST /graph/architecture/subgraph`

### Health

- `GET /health`
- `GET /health/backends`

## Externer Embedding-Service auf Port 8030

- `POST /embedding/generate`
- `GET /health`
- `GET /health/dev`

## embedding-dev

- `GET /health`
- `POST /embedding/generate`

## OpenAI Bridge

`services/openai_bridge/bridge_service.py` stellt eine OpenAI-kompatible Minimalflaeche bereit:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `GET /health`

Die umfangreichere und aktuell fuer Continue vorgesehene Implementierung liegt
in `scripts/continue_openai_bridge.py` und ergaenzt insbesondere:

- `POST /v1/responses`
- Streaming
- Attachment-/Tool-Call-Mapping
- Continue-Meta-Request-Shortcuts

Beide Pfade sind noch nicht konsolidiert. Im Laufzeit-Snapshot war keine
Bridge auf dem typischen Port 8011 aktiv.

## OpenVINO Inferenz und MiniCPM-o Speech auf Port 8040

`services/inference/openvino_npu_app.py`:

- `GET /health`
- `POST /infer`
- `POST /infer/helper`
- `POST /vision/analyze` - kanonischer `VisionRequest`; bis zu vier inline-normalisierte JPEG/PNG/WebP/BMP-Bilder, keine Remote-URLs
- `GET /tts/health`
- `POST /tts/generate` - vollstaendige PCM16-WAV
- `POST /tts/stream` - geordnete PCM16-Frames mit Backpressure und Abbruch

Der interne Stream verwendet `audio/x-pcm`, Codec `pcm_s16le`, mono bei
24 kHz. Die kontrollierte 8010-Grenze kodiert ihn standardmaessig als
WebM/Opus bei 48 kHz; Ogg/Opus und rohes PCM16 sind explizit waehlbar.
`X-Liara-TTS-Codec`, Sample-Rate, Kanaele und Contract beschreiben den
ausgehandelten Transport. Bei `persist_artifact=true` kuendigen
`X-Liara-TTS-Artifact-URL` und `X-Liara-TTS-Artifact-Commit: on-complete`
das WAV-Artefakt an. Browser und andere oeffentliche Clients greifen nicht
direkt auf 8040 zu.

Der Vision-Pfad wird ausschliesslich intern vom Orchestrator aufgerufen. Eine
erfolgreiche Antwort enthaelt fuer jedes tatsaechlich dekodierte Bild
`VisionImageEvidence` mit SHA-256 und Dimensionen. Die Bildnutzlast selbst wird
nicht in Chat-History oder Tool-Evidence persistiert. Details und offene Gates:
`docs/05_decisions/adr-006-canonical-vision-evidence-path.md`.

## Eigenstaendige Heartbeat-Instanz auf Port 8050

`services/heartbeat/app.py`:

- `GET /health`
- `GET /v1/heartbeat`
- `GET /v1/curve?window_seconds=10..900`
- `GET /v1/status.txt`
- `POST /v1/observations`

Die lesenden Endpunkte liefern `Cache-Control: no-store`. Externe Messwerte
werden standardmaessig abgewiesen und koennen nur nach expliziter Aktivierung
mit Bearer-Token als kanonischer `ObservationBatch` eingeliefert werden. Die
Instanz plant und startet selbst keine Arbeit.

## Eigenstaendige Self-Observer-Instanz auf Port 8060

`services/self_observer/app.py`:

- `GET /health`
- `GET /v1/state`
- `GET /v1/history?limit=1..240`
- `GET /v1/inspection`
- `POST /v1/inspection/canary`
- `GET /v1/status.txt`

Die Instanz liest Hardware-, Software- und Assurance-Evidenz zyklisch,
normalisiert sie als `StateEvidence` und persistiert den daraus abgeleiteten
`SystemStateEnvelope`. Sie besitzt ausschliesslich lesende Quelladapter. Das
read-only Inspection-Endpoint zeigt zusaetzlich die Entscheidung des getrennten
Assurance-Gates. Dessen Defaultmodus `observe` fuehrt keine Einreichung aus.
Nach einer expliziten Einreichung enthaelt die Entscheidung Jobstatus,
Abschlusszeit, bis zu 100 strukturierte Findings und bis zu 100 Artefaktpfade.
Der Canary-POST ist standardmaessig deaktiviert und benoetigt einen ephemeren
Bearer-Token. Er ist ausschliesslich fuer einen korrelierten One-shot-Test des
realen Assurance-Pfads vorgesehen.

## Contract-Quelle

Die meisten Request-/Response-Modelle liegen in:

- `services/contracts/service_boundaries.py`
- `services/contracts/orchestration_split.py`
- `services/contracts/heartbeat.py`

Wichtige Contract-Gruppen:

- `ChatRequest`, `ChatResponse`, `ChatAttachment`, `ChatArtifact`
- `OrchestratorRequest`, `OrchestratorResponse`
- `Memory*Request`, `Memory*Response`
- `Context*Request`, `Context*Response`
- `Relation*Request`, `Relation*Response`
- `Graph*Request`, `Graph*Response`
- `ToolExecutionRequest`, `ToolExecutionResult`
- `InferenceRequest`, `InferenceResult`
- `ValidationContext`, `ValidationResult`
- `ResourceObservation`, `ObservationBatch`, `HeartbeatSnapshot`, `StateCurve`
