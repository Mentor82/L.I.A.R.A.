# EmbeddingServer Integration Boundary

Stand: 2026-05-01

## Grundsatz

`LiNeP` steht fuer:

```text
Liara Neural Protocol
```

Der native EmbeddingServer braucht zwei Schnittstellen:

```text
HTTP/API fuer LIARA-Orchestrator/Memory-Kompatibilitaet
LiNeP fuer native Scheduler-/Worker-Transporte
```

## Was ueber API laeuft

Alles, was Orchestrator-owned ist, bleibt ueber API-Contracts angebunden:

- Orchestrator
- Memory Adapter
- RAG-/Retrieval-Flow aus LIARA
- Kompatibilitaet mit dem bestehenden Python-Embedding-Service
- Health- und Fallback-Entscheidungen auf LIARA-Ebene

Das bedeutet:

```text
Orchestrator -> API/HTTP -> Embedding Contract
```

Kein Orchestrator-Pfad soll native LiNeP direkt nutzen.

## Was ueber LiNeP laeuft

LiNeP ist fuer native Scheduler-nahe Runtime:

- Scheduler -> Helper
- Scheduler -> Embedding
- Slot-Heartbeat
- Slot-Readiness
- Load-/Queue-/Thermal-/Degraded-Signale
- EMBED_REQUEST / EMBED_RESPONSE
- SIMILARITY_REQUEST / SIMILARITY_RESPONSE
- CONSENSUS_REQUEST / CONSENSUS_RESPONSE

Das bedeutet:

```text
Scheduler -> LiNeP/TCP -> Embedding Slot
Embedding Slot -> LiNeP/UDP Heartbeat -> Scheduler
```

## Warum beide noetig sind

HTTP/API bleibt noetig, weil LIARA bereits API-/Adapter-Contracts fuer Memory, Retrieval und Orchestrator besitzt.

LiNeP bleibt noetig, weil Scheduler/Helper/Embedding auf der nativen Runtime-Ebene Slot-Status, Last, Queue-Tiefe, Degraded-State und Consensus ohne Python-/HTTP-Hot-Path transportieren sollen.

## Zielbild

```text
Liara API / Orchestrator
  -> HTTP /embedding/generate
  -> bestehende API-Vertraege

Native Scheduler
  -> LiNeP EMBED_REQUEST
  -> LiNeP SIMILARITY_REQUEST
  -> LiNeP CONSENSUS_REQUEST

LiaraEmbeddingService
  -> bedient beide Oberflaechen
  -> HTTP fuer API-Kompatibilitaet
  -> LiNeP fuer Scheduler/Embedding
```

## Aktueller Implementierungsstand

Im lokalen Snapshot ist jetzt umgesetzt:

- HTTP `GET /health`
- HTTP `POST /embedding/generate`
- LiNeP UDP Heartbeat fuer den Embedding-Slot
- LiNeP TCP `EMBED_REQUEST -> EMBED_RESPONSE`

Noch nicht umgesetzt:

- LiNeP `SIMILARITY_REQUEST -> SIMILARITY_RESPONSE`
- LiNeP `CONSENSUS_REQUEST -> CONSENSUS_RESPONSE`
- Orchestrator-/Memory-Fallback-Umschaltung auf den C++-Dienst

## Nicht-Ziel

LiNeP ersetzt nicht die Orchestrator-API.

LiNeP ist kein direkter Shortcut vom Orchestrator in native Worker. Es ist die native Transportebene fuer Scheduler-seitige Slot- und Task-Verteilung.
