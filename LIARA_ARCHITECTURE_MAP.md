# LIARA — Architecture Map

Stand: 2026-08-12

## Modularisierte Service-Architektur (Neu: 2026-08-12)

1. **`services/api/app.py` FastAPI Subrouter**:
   - [`services/api/routers/system.py`](file:///c:/ai/LIARA/services/api/routers/system.py) (`/health`, `/health/backends`)
   - [`services/api/routers/chat.py`](file:///c:/ai/LIARA/services/api/routers/chat.py) (`/chat`, `/chat/stream`, `/history`, `/session`)
   - [`services/api/routers/tools.py`](file:///c:/ai/LIARA/services/api/routers/tools.py) (`/tools`, `/tools/{name}/invoke`)
   - [`services/api/routers/governance.py`](file:///c:/ai/LIARA/services/api/routers/governance.py) (`/tools/sys/governance/*`)
   - [`services/api/routers/speech.py`](file:///c:/ai/LIARA/services/api/routers/speech.py) (`/speech/health`, `/speech/generate`, `/speech/stream`)
   - [`services/api/routers/compute.py`](file:///c:/ai/LIARA/services/api/routers/compute.py) (`/compute/models`, `/compute/run`, `/compute/generate`)
   - [`services/api/routers/operations.py`](file:///c:/ai/LIARA/services/api/routers/operations.py) (`/operations/heartbeat`, `/operations/self-observer`, `/operations/graph/subgraph`)
   - [`services/api/routers/artifacts.py`](file:///c:/ai/LIARA/services/api/routers/artifacts.py) (`/files/upload`, `/files/artifact`)

2. **`services/memory/stores/` Memory Subpackage & Facade**:
   - `services.memory.stores.base`: `MemoryServiceStore` ABC, Token/Fingerprint Estimators, Datetime Helpers.
   - `services.memory.stores.validation`: Docker Compose & Mock Validator Execution Backends.
   - `services.memory.stores.quality_signals`: Quality Signals, Evidence Calculation & Expiration Rules.
   - `services.memory.stores.in_memory`: `InMemoryMemoryServiceStore` backing implementation.
   - `services.memory.stores.backed`: `BackedMemoryServiceStore` production wrapping layer.
   - `services.memory.stores.factory`: `create_default_memory_service_store()`.
   - `services.memory.store`: Facade Re-export Module (100% abwärtskompatibel für `SessionStore`, `FactStore` etc.).

3. **`services/orchestrator/` Orchestrator Submodule**:
   - [`services/orchestrator/reasoning_control.py`](file:///c:/ai/LIARA/services/orchestrator/reasoning_control.py): Phase 1–4 Reasoning-Metriken (Belief, Utility, Stability, Decision), Julia/Python-Berechnungen & Schwellenwert-Adaptionen.
   - [`services/orchestrator/librarian_pipeline.py`](file:///c:/ai/LIARA/services/orchestrator/librarian_pipeline.py): Laden aller expliziten Kanäle (History, Facts mit `[fact_verified:ns]`, Reranked Vector Retrieval & Graph-Relational-Context).
   - [`services/orchestrator/tool_discovery.py`](file:///c:/ai/LIARA/services/orchestrator/tool_discovery.py): Tool-Selektion, Execution, External Tool Planning & Web-Discovery-Ranking.
   - [`services/orchestrator/generation_pipeline.py`](file:///c:/ai/LIARA/services/orchestrator/generation_pipeline.py): LLM-Inferenz, NPU-Offload, Prompt-Bau, Response-Validierung, Judge Traceability & Audit-Log-Integration (`log_judge_pre_action`).
   - [`services/orchestrator/orchestrator.py`](file:///c:/ai/LIARA/services/orchestrator/orchestrator.py): Facade Coordinator Class mit 100% monkeypatch-kompatibler Delegierung aller 89 privaten Methoden.

## Interaktive Karte

Die erweiterbare grafische Sicht liegt unter:

[`http://127.0.0.1:3001/architecture`](http://127.0.0.1:3001/architecture)

Ihr Datenmodell liegt in
`frontend/web-ui/src/app/architecture/architecture-data.ts`. Neue Komponenten
und Relationen werden dort ergänzt; Diagramm, Suche, Reifegrad und
Detailansicht entstehen daraus.

Die Komponenten `Orchestrator` und `Memory & Graph` besitzen zusaetzlich einen
read-only Neo4j-Drill-down. Ein Klick laedt einen auf einen Hop, 25 Beziehungen
und 50 Knoten begrenzten Runtime-Subgraphen. Das Frontend akzeptiert kein
Cypher; Komponenten, Labels, Relationstypen und ausgegebene Properties werden
serverseitig allowlist-basiert bestimmt.

```text
Architekturkomponente
-> GET /operations/graph/subgraph
-> Memory-Service /graph/architecture/subgraph
-> GraphStore.architecture_subgraph
-> gefilterte Nodes + Edges + truncated + query_ms
```

Damit bleiben kanonische Architektur und beobachteter Laufzeitgraph getrennte,
aber miteinander verknuepfte Ebenen.

## Systemkarte

```text
Zugaenge
   |
   v
LIARA API
   |
   v
InputSituationProfile ---> Router / Planner
   |                              |
   v                              v
Orchestrator -------------> Context / Evidence <---- Memory / Graph
   |                              |
   +------> Inference ------------+
   |             |
   +------> Tools / SYS            v
                 |          Validator / Judge / Reward
                 v                 |
          WSL / Compute            v
                 |          Antwort + Audit + Memory
                 v
      Mutation Verification
                 |
                 v
            ai-validator
```

## Normaler Chatfluss

Zwei zuvor in der grafischen Karte fehlende Infrastrukturpfade sind nun
explizit modelliert:

```text
Entfernter oder lokaler Browser
-> Next.js Web UI :3001
-> Same-Origin BFF /api/liara/*
-> streamender Route Handler 127.0.0.1:8010
-> LIARA API :8010 (weiterhin nur Loopback)

Continue / VS Code / OpenAI-Client
-> OpenAI Bridge :8011 (optionaler Formatadapter)
-> LIARA API :8010

Memory / semantisches Retrieval
-> nativer C++ Embedding Worker :8030
-> OpenVINO auf NPU, 1024 Dimensionen
-> LiNeP Worker 30, TCP 8767, Heartbeat 8768
```

Der BFF-Route-Handler reicht JSON, SSE sowie binaere Speech-Streams transparent
durch. Externe Browser benoetigen weder CORS-Freigabe noch direkten Zugriff
auf Port 8010. Port 3001 bleibt damit die einzige Web-UI-Grenze im LAN.

Der Python-Embedding-Service ist dabei nicht der primaere Worker, sondern nur
Wrapper beziehungsweise Fallback fuer den nativen C++/OpenVINO-Pfad.

```text
Nachricht
-> API-Safety und Contract-Normalisierung
-> InputSituationProfile
   -> Eingangskontext
   -> Fach-/Themenprofil
   -> Mood-Signal
   -> RetrievalIntent (Ziel, Entitaeten, Quelle, Unsicherheiten)
   -> Analyze / Think / Answer / Plan / Act
-> Router und bei Bedarf Planner
-> Kontext / Evidenz / Memory
-> optional policy-gated Tool
   -> konkretes ToolExecutionRequest-Payload
   -> Pre-Action-Judge bewertet exakt dieses Payload
   -> success: belastbare Evidenz
   -> failed/block/revise: sichtbare Failure-Envelope, evidence=false
-> Inference-Gateway
-> Validator / Judge / Reward
   -> Tool-Evidence-Integrity blockiert vorgetaeschte Ausfuehrung
-> History und optional Graph-v2
-> Antwort oder SSE-Abschluss
```

Routingabsicht ist keine Ausfuehrungsevidenz. `selected_tools` beschreibt den
geplanten Pfad; nur eine erfolgreiche `ToolExecutionResult` darf Fakten erden
oder Confidence erhoehen. Fehlgeschlagene Tools bleiben diagnostisch sichtbar,
werden aber mit `evidence=false` vom Grounding ausgeschlossen. Nach erfolglosen
Retries ersetzt eine deterministische Fehlermeldung jeden Entwurf, der dennoch
eine Toolausfuehrung oder ein externes Ergebnis behauptet. Die Entscheidung ist
in [`ADR-004`](docs/05_decisions/adr-004-tool-evidence-integrity.md) festgehalten.

Externe Recherche wird semantisch und ohne fachliche Quellen-Schlagwortliste
vorbereitet:

```text
RetrievalIntent-Inferenz
-> sichere URL? direkt zum frischen Policy-/Judge-Abruf
-> sonst genau eine begrenzte Suchseitenabfrage
   -> Kandidaten mit evidence_scope=discovery (keine Grounding-Evidenz)
   -> zweite Inferenz bewertet Ziel, Entitaeten und Unsicherheiten
   -> hoechstens ein Primaerabruf
   -> URL-Validierung + W/G/B + Judge + Governance + SYS-Audit erneut
-> nur success des Primaerabrufs erdet externe Fakten
```

Damit entscheidet Inferenz ueber Bedeutung und Kandidaten, nicht ueber
Berechtigungen. Die Entscheidung und der Live-Nachweis stehen in
[`ADR-005`](docs/05_decisions/adr-005-inference-first-web-retrieval.md).

MiniCPM-o 2.6 INT4 ist auf Port 8040 als echte OpenVINO-`VLMPipeline` auf der
NPU verfuegbar. Retrieval-Strukturprompts laufen dort ueber `/infer` ohne
doppelte Prompt-Einbettung. Der produktive Retrieval-Intent bleibt nach dem
aktuellen Wiederholungs-Gate dennoch beim Main-Provider, weil identische
MiniCPM-Laeufe Quellen und Identifikatoren noch nicht stabil bewahrten. Das
NPU-Modell bleibt fuer begrenzte Helper-Aufgaben aktiv und kann per
`RETRIEVAL_INTENT_PROVIDER` erneut opt-in getestet werden.

## Speech- und Streamingfluss

Speech folgt derselben Identity-/Planungsgrenze, besitzt aber einen eigenen
binaeren Ausgabevertrag. WAV ist kein Zwischenformat:

```text
VoiceIdentity
-> SpeechPlanner
-> semantische SpeechPlan-Segmente
-> MiniCPM-o TTS Engine :8040 (CPU-Referenz)
-> gemeinsamer PCM16-Produzent, mono, 24 kHz
   +-> /tts/generate -> WAV -> TtsServiceAdapter -> AudioArtifact
   +-> /tts/stream -> Backpressure/Abbruch -> /speech/stream :8010
       +-> WebM/Opus (Default) -> MediaSource -> Browserlautsprecher
           \-> ohne MediaSource: kompatibler /speech/generate-WAV-Fallback
       +-> Ogg/Opus oder PCM16 (ausgehandelte Alternative)
       +-> optionaler PCM-Tee -> atomar committed WAV -> AudioArtifact
```

Der PCM-Produzent erzeugt ein Segment erst, wenn der Verbraucher den naechsten
Chunk anfordert. Geplante Pausen werden als geordnete PCM-Frames ausgegeben.
Ein geschlossener Clientstream setzt das request-lokale Abbruchsignal; Port
8040 rechnet nicht still alle noch ausstehenden Segmente weiter.

Phase 7B transportiert `webm_opus` als Browserdefault sowie `ogg_opus` und
`pcm_s16le` als Alternativen mit `audio_stream/v1`. Kurze deterministische
Segmentfades reduzieren harte Kanten. Der optionale WAV-Tee puffert den
Clientstream nicht und verwirft unvollstaendige `.part`-Dateien bei Abbruch.
LiNeP darf Codec und Transport kuenftig aushandeln, besitzt aber nicht die
`VoiceIdentity`.

Fuer LIARA-bezogene Architekturfragen ist der Eingangskontext nun direkt mit
dem Librarian gekoppelt. Das Profil erteilt dabei keine Ausfuehrungsrechte,
sondern erweitert ausschliesslich die lesenden Evidenzpfade:

```text
InputSituationProfile(domain=ai_architecture|software, topic=liara)
-> Librarian: input_profile_internal_architecture
-> Qdrant + Session-Chroma + Neo4j lesend anfragen
-> kanonischen System-Contract als Baseline-Evidenz hinzufuegen
-> Evidence Engine selektiert die tatsaechlich relevanten Quellen
-> context_mode=SYSTEM, falls keine Session-Memory hoehere Prioritaet besitzt
```

Der System-Contract erklaert Identitaet, Rollen und Architekturgrenzen. Er darf
keine aktuellen Runtime-Zustaende wie Service-Health oder aktive Provider
behaupten; dafuer bleibt beobachtete Runtime-Evidenz erforderlich.

## Komplexer Workspace-Fluss

```text
komplexen Auftrag erkennen
-> typisierten Plan erzeugen
-> Schritt- und Ressourcenbudget anwenden
-> Schritt einzeln ueber ToolCoordinator -> SYS ausfuehren
-> reale Mutation in Debian-WSL
-> read-back / stat / hash / diff
-> Zustand beobachten
-> Folgeschritt freigeben oder abbrechen
-> projektbezogene Tests in der WSL-.venv
-> ai-validator
-> Kandidat, Patch, Findings und Audit persistieren
-> Governance entscheidet ueber spaetere Uebernahme
```

## Kontroll- und Entwicklungsfluss

```text
Observer / Runtime-Signal
-> Finding
-> Ursachenhypothese
-> isolierte Variante oder Simulation
-> Kosten, Utility, Risiko, Confidence und Information Gain
-> Validator / Judge
-> Governance Proposal
-> Freigabe oder Block
-> kontrollierte Ausfuehrung
-> erneute Beobachtung
```

## Verantwortungsgrenzen

| Rolle | Darf | Darf nicht allein |
| --- | --- | --- |
| API | Contracts transportieren, Safety anwenden | Modell- oder Toollogik besitzen |
| Orchestrator | Pfade und Ablauf koordinieren | eigene Produktivberechtigungen erteilen |
| Antwortsynthese / LLM | bereits ausgefuehrte Toolresultate zu einer belegten Antwort verdichten | Tools auswaehlen, Aufrufe simulieren oder interne Direktiven ausgeben |
| Planner | typisierte Schritte und Abhaengigkeiten erzeugen | ungeprueft ausfuehren |
| Worker | spezialisierte Aufgabe bearbeiten | sich selbst validieren und freigeben |
| Tool / SYS | W/G/B-gepruefte Operation im Debian-Workspace ausfuehren; validierte read-only HTTP(S)-Abrufe auditieren | Policies, Blacklist oder Root-Grenzen umgehen |
| Validator | Struktur und Zulaessigkeit pruefen | Findings als Governance-Entscheid behandeln |
| Judge | Qualitaet und Plausibilitaet bewerten | Produktivmutation freigeben |
| Memory | Zustand, Fakten und Beziehungen speichern | Modellbehauptungen automatisch verifizieren |
| Governance | erlaubte Tragweite entscheiden | fehlende technische Evidenz ersetzen |

## Nachvollziehbare Entwicklung

LIARA ist nicht nur durch aktuelle Komponenten definiert. Fuer jede relevante
Entwicklung soll auch folgende Kette nachvollziehbar bleiben:

```text
Finding -> Entscheidung -> Patch -> Test -> Validator -> Freigabe -> Zustand
```

Die Karte zeigt den gegenwaertigen Strukturstand. Build-Historie, Audit und
Decision Traces zeigen, wie dieser Zustand entstanden ist.

## Vertiefende Quellen

- [`docs/01_architektur/liara-overview.md`](docs/01_architektur/liara-overview.md)
- [`docs/02_services/liara-orchestrator.md`](docs/02_services/liara-orchestrator.md)
- [`docs/02_services/liara-tools.md`](docs/02_services/liara-tools.md)
- [`docs/05_decisions/adr-004-tool-evidence-integrity.md`](docs/05_decisions/adr-004-tool-evidence-integrity.md)
- [`docs/05_decisions/adr-005-inference-first-web-retrieval.md`](docs/05_decisions/adr-005-inference-first-web-retrieval.md)
- [`docs/WSL_SESSION_RUNTIME.md`](docs/WSL_SESSION_RUNTIME.md)
- [`docs/HYBRID_CONTROL_SYSTEM.md`](docs/HYBRID_CONTROL_SYSTEM.md)
- [`docs/DECISION_EXPLANATION_LAYER.md`](docs/DECISION_EXPLANATION_LAYER.md)
