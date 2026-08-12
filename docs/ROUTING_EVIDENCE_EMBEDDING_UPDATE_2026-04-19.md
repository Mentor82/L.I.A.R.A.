# Routing / Evidence / Embedding Update (2026-04-19)

## Kurzfassung

Diese Aenderungsrunde erweitert LIARA um drei eng gekoppelte Bausteine:

- semantisches Tool-Routing als optionalen Vorpfad vor der Keyword-Heuristik
- eine Semantic-Filtering-Stage innerhalb der Evidence Engine
- einen `input_hash -> embedding` Cache im Embedding-Service

Zusammen verschiebt das den Stack in Richtung eines embedding-first Workflows, ohne die vorhandenen deterministischen Fallbacks oder die bestehende Orchestrator-Logik zu entfernen.

## Umgesetzte Punkte

### 1. Embedding Cache

- Datei: `services/embedding/app.py`
- Neuer In-Memory-Cache mit TTL + LRU
- Cache-Key basiert auf `input_text`, `normalize`, `model_id`, `device`, `backend`
- Antwort-Metadaten enthalten jetzt:
  - `input_hash`
  - `cache_hit`
  - `embedding_latency_ms`
- Health-Ausgabe zeigt Cache-Status (`enabled`, `items`, `max_items`, `ttl_seconds`)

### 2. Zentrale Evidence-Thresholds

- Datei: `services/config/settings.py`
- Neue Konfigurationswerte:
  - `EVIDENCE_CONFIDENCE_STRONG_THRESHOLD`
  - `EVIDENCE_CONFIDENCE_MEDIUM_THRESHOLD`
- Evidence-Klassifikation in `services/orchestrator/evidence_engine.py` nutzt diese Schwellen statt fest verdrahteter Werte

### 3. Semantic Filtering in der Evidence Engine

- Datei: `services/orchestrator/evidence_engine.py`
- Neue Stage: Collect -> Semantic Filter -> Validate
- Neue Konfiguration:
  - `EVIDENCE_SEMANTIC_FILTER_ENABLED`
  - `EVIDENCE_SEMANTIC_MIN_RELEVANCE`
- Neue Trace-Felder im Evidence-Result:
  - `semantic_filtered_evidence`
  - `semantic_discarded_evidence`

### 4. Live Semantic Routing

- Datei: `services/orchestrator/router.py`
- Optionaler semantischer Routing-Pfad mit Threshold-Baendern:
  - `SEMANTIC_ROUTING_STRONG_THRESHOLD=0.85`
  - `SEMANTIC_ROUTING_MEDIUM_THRESHOLD=0.70`
- Unterstuetzte semantische Hauptintents aktuell:
  - `orientation`
  - `conversation_recall_local`
  - `sys`
- Router-Telemetrie wird im Live-Betrieb sichtbar geloggt:
  - `[ROUTER][SEMANTIC] ...`

### 5. API-Trace fuer semantisches Routing

- Datei: `services/api/app.py`
- `debug_run` enthaelt jetzt explizit:
  - `route_debug`
  - `semantic_route`
- Das gilt fuer beide Pfade:
  - `/chat`
  - `/chat/stream` (im `final`-Event)

## Live validierte Faelle

### Orientation

Natuerliche Query:

`Can you help me understand your capabilities?`

Live validiert:

- `/chat`
- sichtbarer Server-Log

Erwartetes Routing:

- `semantic_orientation_query`

### Sys

Natuerliche Query:

`Please check the latest stable Python release`

Live validiert:

- `/chat`
- `/chat/stream`
- sichtbarer Server-Log

Erwartetes Routing:

- `semantic_sys_web`

Hinweis:

Das Routing ist korrekt, aber die finale Antwort kann weiterhin durch die Evidence-Policy blockiert werden, wenn fuer aktuelle Web-/Latest-Fragen keine belastbare Evidenz vorhanden ist.

## Konfiguration (.env)

Aktive neue oder relevante Schalter:

- `EVIDENCE_CONFIDENCE_STRONG_THRESHOLD=0.85`
- `EVIDENCE_CONFIDENCE_MEDIUM_THRESHOLD=0.70`
- `EVIDENCE_SEMANTIC_FILTER_ENABLED=true`
- `EVIDENCE_SEMANTIC_MIN_RELEVANCE=0.18`
- `SEMANTIC_ROUTING_ENABLED=true`
- `SEMANTIC_ROUTING_STRONG_THRESHOLD=0.85`
- `SEMANTIC_ROUTING_MEDIUM_THRESHOLD=0.70`

## Verifikation

- Regression-Task:
  - `liara-test-memory-and-team1`
  - Ergebnis waehrend dieser Runde mehrfach gruen (`91 passed`)
- Direkte Live-Smokes:
  - `/chat`
  - `/chat/stream`
- Sichtbare Log-Nachweise:
  - `route=orientation ...`
  - `route=sys intent=web command=curl ...`

## Offener Rest

- semantische Intent-Profile koennen weiter verbreitert werden
- pytest-Standalone-Laeufe fuer `tests/unit/test_orchestration_split.py` sind in dieser Shell-Umgebung uneinheitlich wegen Async-Plugin-Ladung; die stabile Validierung lief ueber den bestehenden Regression-Task und direkte Runtime-Smokes
- Evidence-Blocking fuer aktuelle Webfragen ist fachlich konsistent, kann aber spaeter durch staerkere Web-Evidenzketten verbessert werden