# Service Boundary: Dreaming / Staging

Stand: 2026-08-08

Code:

- `services/contracts/memory_dreaming.py`
- `services/memory/app.py`
- `services/memory/store.py`
- `services/api/app.py`
- `frontend/web-ui/src/app/architecture/ArchitectureMap.tsx`

## Begriff

`Dreaming` ist der Bedien- und Frontend-Begriff fuer LIARAs manuelle
Konsolidierungsphase. Im Backend besteht diese Phase aus Staging,
Dreaming-Runs, Proposals und expliziten Entscheidungen.

Das Bild ist bewusst analog zur Schlafphase eines Menschen:

```text
laufender Betrieb
-> kurzlebige Inhalte / Beobachtungen
-> Staging
-> Dreaming-/Consolidation-Run
-> Proposal
-> Entscheidung
-> spaetere Uebernahme in stabilere Speicherbereiche
```

Dreaming ist damit kein Worker mit freier Ausfuehrung und keine autonome
Selbstveraenderung. Es ist eine kontrollierte Servicegrenze fuer
Zwischenablage, Verdichtung und Vorschlaege.

## Aktueller Ist-Stand

Der aktuelle lokale Stand ist `manual_only`:

- der Contract kennt `manual`, `ops` und `scheduled`;
- der Store meldet `scheduler_enabled=false`;
- periodische Dreaming-Ausfuehrung ist nicht aktiv;
- API und Web-UI zeigen den Zustand read-only an;
- Entscheidungen bleiben explizite Operationen, keine automatische Freigabe.

Live-Pruefung am 2026-08-08:

```text
GET http://127.0.0.1:8010/operations/dreaming?decision=pending&limit=20
-> status=success
-> mode=manual_only
-> scheduler_enabled=false
-> pending_staged_items=0
-> pending_proposals=0
-> last_run_state=idle
```

## Backend-Endpunkte

Die mutierenden Staging-/Dreaming-Endpunkte liegen auf `liara-memory`:

```text
POST /staging/stage
POST /staging/list
POST /staging/touch
POST /staging/discard
POST /staging/consolidate
POST /dreaming/run
GET  /dreaming/status
POST /dreaming/proposals
POST /dreaming/proposals/assurance
POST /dreaming/proposals/decision
POST /dreaming/cleanup
```

`/dreaming/run` kann optional eine Session-Summary als eigene Proposal-Quelle
erzeugen:

```text
include_session_summary=true
summary_max_messages=50
summary_max_chars=1400
```

Diese Summary ist deterministisch aus History-Messages gebaut. Sie ist kein
LLM-Fact und wird nicht automatisch promoviert.

Optional kann der Run bereits vorhandene, akzeptierte Graph-Kanten als
read-only Proposal-Evidenz uebernehmen:

```text
include_relation_evidence=true
relation_limit=25
```

Eine Kante wird nur angehaengt, wenn Quelle oder Ziel direkt in den
`source_ids` des Proposals enthalten ist. Zusaetzlich muss die Kante
`validated=true` oder `explicit_acceptance=true` tragen und darf nicht
abgelaufen sein. Dreaming erzeugt dabei keine neuen Kanten. Fehlende oder
degradierte Graph-Verfuegbarkeit erscheint in
`summary.relation_evidence`, beendet den Run aber nicht.

Optional erzeugt der Run nach dem Relationsabruf ein deterministisches
Complexity-/Coverage-Signal pro Proposal:

```text
include_quality_signals=true
```

Die Evidenzquelle `proposal_quality_signals` enthaelt Rohzaehler und einen
versionierten, begrenzten Komplexitaetsscore aus Inhaltslaenge, Zahl der
deklarierten Quellen, vorhandener Evidenz und akzeptierten Relationen.
Coverage wird getrennt als Quellenabdeckung und Relationsabdeckung
ausgewiesen; ohne deklarierte `source_ids` lautet der Status
`not_applicable`, nicht etwa `full`. Das Signal ist ausschliesslich
Validator-/Governance-Evidenz. Es setzt weder `decision` noch
`assurance_verdict` und fuehrt keine Promotion aus.

Ein Run kann das Assurance-Gate fuer eine spaetere Approval aktivieren:

```text
require_assurance_for_approval=true
```

Dann blockiert `/dreaming/proposals/decision` jede Approval, bis das Proposal
den Verdict `passed` traegt. Rejection bleibt jederzeit moeglich. Der
zugehoerige Validator-Job muss schon beim Submit fest gebunden sein:

```text
POST /validator/submit
proposal_id=<proposal-id>
context=dreaming_proposal_assurance
strict_mode=true

POST /dreaming/proposals/assurance
proposal_id=<proposal-id>
validator_job_id=<job-id>
assessment_reason=<reason>
```

Die Bindung wird ueber den durchgaengigen `ValidatorJobSubject` mit
`proposal_id` und serverseitigem SHA-256-`proposal_digest` in Submit-, Status-
und Result-Responses geprueft. Der Digest umfasst den Proposal-Kern und seine
nicht vom Validator selbst erzeugte Evidenz, damit auch das optionale
Complexity-/Coverage-Signal unveraenderlich gebunden ist. Ein Report fuer ein
anderes oder nach dem Submit veraendertes Proposal sowie ein anderer Kontext
werden abgewiesen. Nur ein abgeschlossener strikter
Lauf ohne Warning-/Error-Findings und mit `exit_code=0` ergibt `passed`.
Nicht-strikte oder unvollstaendige Reports ergeben hoechstens `attention`,
Fehler ergeben `failed`. Der Report bleibt mit Findings- und Artefaktverweisen
als `validator_report`-Evidenz am Proposal erhalten.

`passed` belegt damit den gebundenen Validator-Lauf und seine Findings. Es ist
kein automatischer Wahrheitsbeweis fuer den vorgeschlagenen Memory-Inhalt;
diese fachliche Entscheidung bleibt bei Governance/Human Gate.

Die zentrale API stellt fuer lokale Bedienoberflaechen nur einen
read-only Snapshot bereit:

```text
GET /operations/dreaming?decision=pending|approved|rejected|all&limit=1..200
```

Dieser API-Endpunkt:

- startet kein Dreaming;
- staged keine neuen Items;
- entscheidet keine Proposals;
- vergibt keine Mutationsrechte;
- setzt `Cache-Control: no-store`;
- nutzt bei Remote-Memory den Memory-Service-Adapter;
- faellt bei Fehlern auf einen typisierten `status=failed` Snapshot zurueck.

## Datenmodell

Wichtige Contract-Typen:

- `MemoryStagingRecord`
- `MemoryStagingStageRequest`
- `MemoryStagingListRequest`
- `MemoryStagingDiscardRequest`
- `MemoryDreamingRunRequest`
- `MemoryDreamingRunResponse`
- `MemoryDreamingStatusResponse`
- `MemoryDreamingProposalRecord`
- `MemoryDreamingProposalListRequest`
- `MemoryDreamingProposalDecisionRequest`

Ein `MemoryDreamingProposalRecord` beschreibt eine vorgeschlagene Promotion:

```text
proposal_id
session_id
staging_id
target_namespace
target_key
proposed_value
proposed_status
promotion_reason
evidence
decision=pending|approved|rejected
created_at
metadata
```

Ein `MemoryStagingRecord` traegt zusaetzlich die aus `Erinnerungen`
uebernommene Konsolidierungsbasis:

```text
importance     # explizites Gewicht 0.0..1.0
access_count   # Recall-/Verstaerkungssignal
ttl_seconds    # optionale Lebensdauer des staged Items
source_ids     # konkrete Quellnachweise, z. B. Turns oder Summary-IDs
```

`/staging/touch` erhoeht `access_count` explizit. Listen- oder
Admin-Refreshes erhoehen den Wert nicht, damit UI-Beobachtung keine
Konsolidierungssignale erzeugt.

## Fluss

```text
Short-term/session context
-> /staging/stage
-> staged item
-> optional /staging/touch fuer echten Recall
-> /dreaming/run oder /staging/consolidate
-> optional Session-Summary-Proposal aus History
-> optional vorhandene Graph-Relationen als direkte source_id-Evidenz
-> proposal(s)
-> /dreaming/proposals
-> optional proposal-gebundener Validator-Report
-> /dreaming/proposals/assurance
-> /dreaming/proposals/decision
-> spaetere kontrollierte Promotion
```

Die Entscheidung ist vom Erzeugen des Vorschlags getrennt. Ein Proposal ist
daher keine automatische Speicherpromotion.

## Retention und Cleanup

`POST /dreaming/cleanup` ist standardmaessig eine reine Vorschau:

```text
dry_run=true
rejected_retention_seconds=2592000  # 30 Tage
staging_limit=500
proposal_limit=500
```

Die Runtime-Regeln sind absichtlich eng:

- staged Items sind nur Kandidaten, wenn `created_at + ttl_seconds` abgelaufen
  ist;
- staged Items mit Referenz aus einem `pending` oder `approved` Proposal sind
  geschuetzt;
- Proposals sind nur Kandidaten, wenn sie `rejected` sind und ein belastbares
  `metadata.decision_at` besitzen;
- `pending`, `approved` und Legacy-Proposals ohne `decision_at` werden nie
  entfernt;
- Limits begrenzen jeden Lauf auf maximal 500 staged Items und 500 Proposals;
- Apply erfordert explizit `dry_run=false` und erzeugt ein Audit-Ereignis;
- Validator-/Memory-Artefakte und das append-only Audit werden nicht geloescht.

Neue Proposal-Entscheidungen schreiben `decision_at` serverseitig nach der
Decision. Dadurch kann Request-Metadata den Retention-Zeitpunkt nicht
vorverlegen.

## Frontend-Sicht

Die Living Architecture Map nutzt:

```text
GET /operations/dreaming
```

Sie zeigt:

- Dreaming-Modus;
- Scheduler-Status;
- Anzahl staged Items;
- Anzahl pending Proposals;
- begrenzte Proposal-Liste;
- aggregierte Assurance-Verdicts und blockierte Approvals;
- Anzahl verfuegbarer Quality-Signale und Complexity-Level;
- pro Proposal Validator-Job, Findings-Anzahl, hoechste Severity,
  Artefaktpfade, Audit-Referenz, Complexity sowie Quellen- und
  Relations-Coverage.

Die Textual Admin Console zeigt dieselbe read-only Projektion in einer
eigenen Dreaming-Tabelle. Mit `v` werden fuer die markierte Zeile der
vollstaendige Assurance-/Artefaktkontext und die Quality-Rohwerte samt
unbedeckten `source_ids` im Eventbereich eingeblendet. Die Console startet
keine Runs, bindet keine Reports und trifft keine Decisions.

Der Frontend-Begriff bleibt `Dreaming`, weil er die gedachte
Systemphase beschreibt. Die technische Umsetzung darunter bleibt Staging,
Consolidation, Proposal und Decision.

## Abgrenzung

Dreaming ist aktuell nicht:

- ein autonomer Scheduler;
- ein freier Worker;
- ein Selbstreparaturrecht;
- ein direkter Write-back in den Projektroot;
- eine Umgehung von Validator, Governance oder Human Gate.

Dreaming darf spaeter Ausfuehrungsrechte erhalten, aber nur ueber eine
separate Policy-/Governance-Grenze.

## Abgleich mit Erinnerungen

Referenzprojekt: `C:\ai\Erinnerungen`

Relevante Backend-Dateien:

- `src/erinnerungen/staging_store.py`
- `src/erinnerungen/service.py`
- `src/erinnerungen/mcp_server.py`
- `src/erinnerungen/graph_store.py`
- `src/erinnerungen/qdrant_store.py`

`Erinnerungen` enthaelt eine aeltere, aber fachlich nuetzliche
Konsolidierungslogik:

```text
Redis short-term buffer
-> staged memory mit ttl_seconds, importance, access_count
-> manuelle oder automatische Consolidation
-> Qdrant-Vektorstore + Neo4j-Graph
```

Die dortige biologische Analogie ist staerker ausgepraegt:

- Redis ist der kurzlebige Arbeits-/Hippocampus-Buffer;
- Qdrant und Neo4j bilden den stabileren Langzeitspeicher;
- `access_count` verstaerkt die Konsolidierungswahrscheinlichkeit;
- `importance >= 0.5` oder mindestens ein Recall machen staged Items
  eligible fuer Auto-Consolidation;
- Chatverlaeufe koennen als einzelne Memories importiert, als Summary
  verdichtet und per `SUMMARIZES`, `NEXT` und `REPLY_TO` verknuepft werden;
- `write_suggestion` erzeugt Ideen-/Todo-Memories mit `SUGGESTS`-Kanten;
- `analyze_memory_complexity` und `find_coverage_gaps` liefern einfache
  Qualitaets- und Coverage-Signale fuer den Graph.

LIARA ist dagegen governance-strenger modelliert:

```text
Staging
-> Dreaming-/Consolidation-Run
-> Proposal
-> Decision
-> spaetere kontrollierte Promotion
```

Der wichtigste Unterschied ist damit:

- `Erinnerungen` kann staged Inhalte direkt in Qdrant/Neo4j uebernehmen;
- LIARA erzeugt zunaechst Proposals und trennt die Entscheidung explizit ab.

Fuer LIARA sollten deshalb nicht die direkten Schreibrechte aus
`Erinnerungen` uebernommen werden. Sinnvoll sind vor allem die Heuristiken
und Evidenzsignale:

- `importance` als explizites Gewicht im Staging-Record;
- `access_count` oder Recall-Signal als Konsolidierungs-Evidenz;
- `ttl_seconds`/Ablaufzeit fuer kurzlebige staged Items;
- Session-Summary als eigene Proposal-Quelle;
- Graph-Kanten wie `SUMMARIZES`, `NEXT`, `REPLY_TO`, `SUGGESTS` als
  belastbare Relationsevidenz;
- Complexity-/Coverage-Analysen als Validator- oder Dreaming-Evidenz.

Nicht direkt uebernehmen:

- `auto_consolidate_staged()` als direkter Write in den Langzeitspeicher;
- freie periodische Konsolidierung ohne Policy;
- Summary-on-Summary-Schleifen ohne harte Quellenbindung;
- unbewertete Suggestion-Memories als verifizierte Fakten.

Zielbild fuer LIARA:

```text
Staged item
+ importance/access_count/ttl/source_ids
+ Summary-/Graph-/Coverage-Evidenz
-> Dreaming proposal
-> Human/Policy decision
-> kontrollierte Promotion in Memory/Graph
```

## Offene Punkte

- optionale Quality-Signal-Darstellung im Web-UI-Detailpanel;
- optionaler Scheduler nur nach Policy-Freigabe;
- Orchestrator-/Self-Observer-Konsum bleibt geplant und bewusst unverbunden.
