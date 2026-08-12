# LIARA — Current State

Stand: 2026-08-12  
Statusquelle: aktiver Workspace `C:\ai\LIARA`, Tests, Healthchecks und
[`docs/00_index.md`](docs/00_index.md).

Diese Datei ist eine kompakte Einstiegssicht. Fuer Details und historische
Belege bleibt `docs/00_index.md` massgeblich.

## Kurzurteil

LIARA ist eine lokal betriebsfaehige, mehrschichtige
KI-Orchestrierungsplattform im Entwicklungszustand. Sie verbindet
Eingangsanalyse, Routing, Planung, Kontext/Evidenz, Inferenz, deterministische
Toolausfuehrung, Validierung, Memory/Graph, Audit und begrenzte
WSL-Arbeitsraeume.

LIARA ist noch nicht production-ready. Netzwerk-Authentisierung,
Governance-Enforcement, Konfigurationskonsistenz, strukturierte
Validator-Findings und die globale Ressourcen-/Heartbeat-Steuerung sind noch
nicht vollstaendig gehaertet.

## Implementiert und belegt

- **P2 App-Server Modularisierung (`services/api/app.py`)**: Reorganisation des früheren App-Monolithen in 8 fokussierte, isolierte FastAPI-Subrouter unter [`services/api/routers/`](file:///c:/ai/LIARA/services/api/routers/) (`system`, `chat`, `tools`, `governance`, `speech`, `compute`, `operations`, `artifacts`). Alle 57/57 API-Unit-Tests und Live-Integrationstests laufen 100% grün.
- **`services/memory/store.py` Modularisierung**: Refactoring des früheren 4.321-Zeilen Monolithen in ein klares Subpackage [`services/memory/stores/`](file:///c:/ai/LIARA/services/memory/stores/) mit 6 spezialisierten Modulen (`base`, `validation`, `quality_signals`, `in_memory`, `backed`, `factory`), einer Paketexportstruktur (`__init__.py`) und einem 100% kompatiblen Facade-Reexport in `store.py`. Sämtliche 124 Memory-Unit-Tests sowie die 15/15 Live-System-Integrationstests sind 100% grün verifiziert.
- FastAPI-Zugang fuer Chat, Streaming, History, Sessions, Tools, SYS, Compute,
  Artefakte und Audit.
- Orchestrator mit `InputSituationProfile`, Routing, Planung,
  Kontext-/Evidence-Aufbau, Provider-Aufruf, Toolpfad, Validation/Judge,
  Retry und Graph-Persistenz.
- Settings & Provider-Konfiguration konsolidiert (`services/config/settings.py`):
  Logische Sektionen, einheitliche LLM-Timeouts (`DEFAULT_LLM_TIMEOUT_SECONDS`),
  konsistente Store-/Service-URLs (Qdrant, Redis, Postgres, Neo4j, Memory, Embedding)
  sowie vollständiger `Settings.to_dict()` Audit-Export.
- Workspace-Agent E2E-Abnahme & WSL/Docker-Monitoring:
  Workspace-Agent unter `/home/liara/workspace` verifiziert (17 Agent-Tests,
  6 WSL-Session-Tests, 30 Audit-Tests in 27,7s grün). WSL2-Debian-Distro und alle
  5 Docker-Container (`liara-neo4j`, `liara-qdrant`, `liara-chroma`, `liara-redis`,
  `liara-postgres`) laufen gesund. Subprocess-Cleanups (`proc.kill()`) verhindern
  Zombie-Threads bei Timeouts.
- Scout-Vektoreinbindung (`SCOUT_USE_REAL_EMBEDDINGS`): Echtes semantisches
  Vector-Routing ueber den nativen OpenVINO-Embedding-Service (`:8030`).
  Intent-Vektoren werden in Redis cached (`scout:profile:{intent}:{version}`) oder
  in-memory vorgehalten. Echtes Cosine Similarity Scoring klassifiziert Anfragen
  in versionierte `IntentProfile`-Objekte (`orientation`, `conversation_recall_local`,
  `sys`, `data_analysis`, `code_exploration`, `debugging`). Bei Service-Ausfall oder
  deaktivierter Flag greift der bisherige Token-Overlap-Pfad nahtlos als Fallback.
  Entscheidungssignale (`semantic_backend="embedding"`, `semantic_scores`) landen
  beobachtbar in `RouterDecision.metadata`.
- Strukturierte Validator-Findings: `ValidatorFinding` 5-Tupel `{file_path, line, rule, severity, message}`.
  Parsierung von Linter-Ausgaben (Ruff, Mypy, Flake8, Prettier, Pytest) und Normalisierung
  vor Persistierung in `validation-reports/`.
- LIARA-bezogene Architekturprofile aktivieren ueber den Librarian nur lesende
  Qdrant-, Session-Chroma- und Neo4j-Pfade. Der kanonische System-Contract
  dient als Baseline-Evidenz, wenn die semantischen Stores keinen Treffer
  liefern; der beobachtete Modus ist dann `SYSTEM` statt `NONE`.
- Memory-Service mit Adaptergrenze, meheren Stores, Facts, Retrieval,
  Relations- und Graph-v2-Pfaden.
- Inference-Gateway mit austauschbaren Providern und Fallbacklogik.
- MiniCPM-o Speech mit deterministischem `SpeechPlanner`, gemeinsamem
  segmentweisem PCM16-Produzenten, kompatiblem WAV-/AudioArtifact-Pfad sowie
  binaerem Streaming ueber `8040 /tts/stream` und kontrolliertem
  `8010 /speech/stream`.
- Contract, Startpfad, Health, Stabilisierung und LiNeP-Heartbeat des nativen
  OpenVINO-Embedding-Service sind implementiert und verifiziert.
- Policy-gated `sys` mit Argumentlisten, Path-Confinement, Audit und
  Write-Verifikation.
- `ai-validator quick` Canary Scope mit expliziten Budgets und Einschränkungen.
- Vollständige verifizierte Unit-Test-Baseline (179 passed in 8,34s, 0 failed).
- Nativer WSL-Arbeitsraum unter `/home/liara/workspace`, inklusive Python,
  projektbezogener `.venv` und lokaler Julia-Runtime.
- CLI, Textual-Frontend und Next.js-Webfrontend mit Architekturkarte.

## Aktuelle Luecken / Risiken

1. Ausstehende Integrationstests fuer End-to-End Vektor-Routing und Validator-Reporting.
2. Fehlende Netzwerk-Authentisierung auf FastAPI-Ebene.

## Naechste Arbeitsfoki

1. Inspektion und Identifikation weiterer monolithischer Kandidaten im Codebase.
2. Härtung der Netzwerk-Authentisierung und Governance-Enforcement auf API-Ebene.
2. Härtung der Governance- und Security-Grenzen (API-Key/JWT Auth).
