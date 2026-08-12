# MEMORY MIGRATION PLAN

Migration plan from the current in-process `MemoryLayer` to future
`liara-memory` service endpoints.

## Goal

Move orchestrator-facing memory access from direct in-process store objects to
a stable service boundary without breaking the current v1 runtime.

The migration must preserve:
- request/response schema compatibility
- in-process testability
- graceful fallback during rollout

## Current State

Today:
- `Orchestrator` accepts a memory dependency and wraps it via `ensure_memory_service_adapter(...)`
- `InProcessMemoryAdapter` exposes service-facing history/facts operations over `MemoryLayer`
- `history` and `facts` have concrete contract models
- `retrieval` and `embedding` have contract models but no full backend path yet

## Target State

Target architecture:
- orchestrator depends on a service-facing memory adapter only
- in-process adapter remains available for tests and local fallback
- service-mode adapter calls `liara-memory` endpoints over a transport boundary

## Endpoint Mapping

Proposed `liara-memory` endpoints:

- `POST /history/append`
  - request: `MemoryHistoryAppendRequest`
  - response: `MemoryHistoryResponse`

- `POST /history/query`
  - request: `MemoryHistoryQueryRequest`
  - response: `MemoryHistoryResponse`

- `POST /facts/upsert`
  - request: `MemoryFactUpsertRequest`
  - response: `MemoryFactResponse`

- `POST /facts/query`
  - request: `MemoryFactQueryRequest`
  - response: `MemoryFactResponse`

- `POST /retrieval/upsert`
  - request: `MemoryRetrievalUpsertRequest`
  - response: `MemoryRetrievalResponse`

- `POST /retrieval/query`
  - request: `MemoryRetrievalQueryRequest`
  - response: `MemoryRetrievalResponse`

- `POST /embedding/generate`
  - request: `MemoryEmbeddingRequest`
  - response: `MemoryEmbeddingResponse`

Optional later:
- `GET /health`
- `GET /health/backends`

## Migration Phases

### Phase A - Boundary First

Status:
- done in this repo

Steps:
- define request/response schemas
- introduce adapter boundary
- verify in-process and service-mode contract parity in tests

Exit:
- orchestrator no longer depends conceptually on concrete store classes

### Phase B - Service Adapter Introduction

Steps:
- add `RemoteMemoryAdapter` implementing `MemoryServiceAdapter`
- choose transport: HTTP first, queue-ready later
- configure base URL and timeouts through settings
- map endpoint failures into `MemoryServiceStatus`

Exit:
- orchestrator can run against in-process adapter or remote adapter with the same call sites

### Phase C - Selective Endpoint Rollout

Recommended order:
1. `history`
2. `facts`
3. `retrieval`
4. `embedding`

Reason:
- history/facts already have concrete backing behavior
- retrieval/embedding still depend on unfinished backend wiring

Exit:
- history/facts can be served remotely in non-production and staging paths

### Phase D - Dual-Mode Validation

Steps:
- run the same adapter contract tests against in-process and remote adapters
- run orchestrator integration tests in:
  - in-process mode
  - remote memory service mode
- compare payload shapes and degraded/failure handling

Exit:
- no schema drift between modes

### Phase E - Cutover

Steps:
- make remote adapter the default in target deployments
- keep in-process adapter behind feature flag for fallback
- observe health, latency, and degradation patterns

Exit:
- `liara-memory` is the default production path

## Required Config

Recommended new settings once service mode begins:

- `MEMORY_MODE=in_process|service`
- `MEMORY_SERVICE_BASE_URL=http://127.0.0.1:8020`
- `MEMORY_SERVICE_TIMEOUT_SECONDS=10`
- `MEMORY_SERVICE_FAIL_OPEN=true|false`

Behavior:
- `MEMORY_MODE=in_process` -> use `InProcessMemoryAdapter`
- `MEMORY_MODE=service` -> use `RemoteMemoryAdapter`

## Fallback Strategy

Recommended rollout behavior:

- history/facts writes:
  - fail closed if audit/history persistence is mandatory
  - otherwise fail open only when explicitly configured

- retrieval reads:
  - fail open if memory augmentation is optional for the request class

- embedding:
  - fail closed for retrieval-write flows that require vectors

Operational fallback:
- remote adapter failure may temporarily switch to in-process adapter only if
  the deployment intentionally carries both capabilities
- fallback choice must be explicit, not silent

## Test Plan

Minimum required test coverage before cutover:

- unit tests for `RemoteMemoryAdapter` request/response mapping
- contract parity tests: in-process vs remote adapter
- degraded-mode tests for backend failures
- orchestrator integration tests in both memory modes
- optional live tests against actual Redis/Postgres/Qdrant/Neo4j services

## Open Decisions

- transport for orchestrator <-> memory:
  - HTTP now
  - queue/event integration later

- whether retrieval and embedding live in the same service process or split
  into separate workers behind the same boundary

- whether graph/pattern memory is part of `liara-memory` v1.5 or deferred to v2

## Acceptance Criteria

`M5` is complete when:
- migration phases are documented
- endpoint mapping is defined
- fallback behavior is explicit
- Team Lead can schedule service-mode implementation work without reopening
  contract design questions
