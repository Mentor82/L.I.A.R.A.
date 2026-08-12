# MEMORY SERVICE CONTRACTS

Contract draft for the future `liara-memory` service boundary.

This document defines the service-facing requests and responses for the four
memory capabilities that will be split out of the current in-process
`MemoryLayer`:

- `history`
- `facts`
- `retrieval`
- `embedding`

## Design Goals

- Orchestrator talks to memory through schemas, not concrete store classes.
- In-process mode and service mode must share the same request/response shapes.
- Partial degradation must be representable without changing the envelope shape.

## Shared Envelope

All `liara-memory` endpoints return a `MemoryServiceStatus` alongside domain
payload.

```python
class MemoryServiceStatus(BaseModel):
    status: Literal["success", "partial", "failed"]
    backend: Literal["redis", "postgres", "qdrant", "embedding", "memory-service"]
    degraded: bool = False
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}
```

Interpretation:
- `success`: request completed normally
- `partial`: request completed with degraded backend coverage
- `failed`: request failed and payload should be treated as unavailable

## History

Purpose:
- conversation history
- tool messages
- session-scoped timeline retrieval

Requests:
- `MemoryHistoryAppendRequest`
- `MemoryHistoryQueryRequest`

Response:
- `MemoryHistoryResponse`

Primary fields:
- `session_id`
- `run_id`
- `role`
- `content`
- `metadata`

## Facts

Purpose:
- durable key/value facts
- user preferences
- structured memory promoted from session history

Requests:
- `MemoryFactUpsertRequest`
- `MemoryFactQueryRequest`

Response:
- `MemoryFactResponse`

Primary fields:
- `namespace`
- `key`
- `value`
- `source`
- `confidence`
- `tags`

## Retrieval

Purpose:
- semantic search across embedded documents
- context recovery for RAG-like flows

Requests:
- `MemoryRetrievalUpsertRequest`
- `MemoryRetrievalQueryRequest`

Response:
- `MemoryRetrievalResponse`

Primary fields:
- `document_id`
- `content`
- `score`
- `source`
- `metadata`

## Embedding

Purpose:
- generate vectors for retrieval writes and semantic lookup requests

Request:
- `MemoryEmbeddingRequest`

Response:
- `MemoryEmbeddingResponse`

Primary fields:
- `input_text`
- `model`
- `normalize`
- `vector`
- `dimensions`

## Current Mapping

Current in-process implementation:
- `SessionStore` backs session-like state
- `FactStore` backs persistent key/value memory
- `RetrievalIndex` is stubbed
- `GraphStore` is stubbed

Migration intent:
- `history` and `facts` map first to Postgres/Redis-backed adapters
- `retrieval` and `embedding` become service contracts before full backend wiring
- Orchestrator should eventually depend on these schemas rather than directly on `MemoryLayer`

## Source of Truth

Canonical schema definitions live in:
- `services/contracts/service_boundaries.py`

This document is explanatory; code definitions remain authoritative.
