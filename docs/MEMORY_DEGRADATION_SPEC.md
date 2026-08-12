# MEMORY DEGRADATION SPEC

Defines health and degraded-mode behavior for the future `liara-memory`
boundary across Redis, Postgres, Qdrant, and Neo4j.

## Goal

Keep the orchestrator contract stable when one or more memory backends are
slow, unavailable, or intentionally disabled.

The service must communicate degradation through `MemoryServiceStatus`
instead of changing payload shapes or raising transport-specific errors into
normal orchestration code.

## Health Model

Each backend is tracked independently:

- `redis`
- `postgres`
- `qdrant`
- `neo4j`
- `embedding`

Each backend reports one of:

- `healthy`
- `degraded`
- `unavailable`

Recommended response metadata shape:

```json
{
  "backend_health": {
    "redis": "healthy",
    "postgres": "healthy",
    "qdrant": "unavailable",
    "neo4j": "unavailable"
  }
}
```

## Status Mapping

Every memory endpoint returns `MemoryServiceStatus`.

Rules:

- `status="success"` when the request completes on all required backends
- `status="partial"` when the request completes but one or more non-critical backends are degraded or skipped
- `status="failed"` when the request cannot satisfy its primary contract

`degraded=true` must be set when `status="partial"`.

`error` should contain a short stable machine-readable message for the primary
failure cause, not a stack trace.

## Backend Criticality

### History

Primary backend:
- `postgres`

Non-critical backends:
- none in v1

Behavior:
- Postgres unavailable -> `failed`
- Redis unavailable -> no impact for history endpoint unless explicit cache layer is later added

### Facts

Primary backend:
- `postgres`

Non-critical backends:
- none in v1

Behavior:
- Postgres unavailable -> `failed`

### Retrieval

Primary backends:
- `qdrant`
- `embedding` when query embeddings must be generated on demand

Behavior:
- Qdrant unavailable -> `failed`
- Embedding unavailable and no embedding supplied -> `failed`
- Embedding unavailable but caller supplied embedding vector -> still `success` if Qdrant query succeeds

### Embedding

Primary backend:
- `embedding`

Behavior:
- Embedding worker unavailable -> `failed`

### Pattern / Graph

Primary backend:
- `neo4j`

Behavior:
- Neo4j unavailable -> `failed` for graph endpoints
- No impact on history/facts unless graph enrichment is made part of those paths

## Orchestrator Expectations

The orchestrator should treat memory responses as follows:

- `success`: use payload normally
- `partial`: use payload and log degradation metadata
- `failed`: fall back to a reduced path if the operation is optional; otherwise fail the stage with explicit reason

Examples:

- Missing retrieval context should not block a simple tool-only answer path if retrieval was optional
- Failed history append should not silently pass if audit/history persistence is mandatory for the run mode

## In-Process Compatibility

The in-process adapter should mirror service behavior:

- translate backend exceptions into `MemoryServiceStatus`
- preserve request/response schema
- avoid leaking Redis/Postgres client exceptions into orchestrator logic

This is required so in-process mode and service mode stay contract-compatible.

## v1 / v2 Scope

v1:
- `history` and `facts` are active
- retrieval/embedding/graph may be stubbed but should still follow status semantics when surfaced

v2:
- all memory capabilities expose health-aware service responses
- endpoint-level health checks should be added for orchestrator readiness decisions

## Recommended Follow-up

- Add a formal `MemoryHealthResponse` contract if orchestrator readiness will depend on backend state
- Add tests for degraded `partial` responses once service-mode adapters exist
