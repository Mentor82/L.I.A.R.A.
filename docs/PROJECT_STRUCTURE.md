# LIARA Project Structure

This document defines the repository structure in two views:

1. Current structure (what is executable now)
2. Target structure (service and worker split)

Note:
- `README.md` restored from `README.old.md`.
- Target architecture reference: `README.old.md` (kept as historical snapshot).

## Current Structure (Ist)

```text
LIARA/
|- services/
|  |- config/           # Runtime settings and environment mapping
|  |- contracts/        # Request/response and boundary contracts
|  |- orchestrator/     # Orchestrator, router, planner, executor, validator
|  |- memory/           # Memory layer internals
|  |- inference/        # Inference gateway, invocation, queue, providers
|  |  |- gateway.py
|  |  |- invocation.py
|  |  |- queue.py
|  |  |- normalizer.py
|  |  |- providers/
|  |- memory_adapter.py
|  |- tools/            # Builtin tools, registry, coordinator
|  |- shared/           # Shared types, utils, exceptions
|  |- api/              # API surface
|
|- tests/
|  |- unit/
|  |- integration/
|
|- docs/
|  |- IMPLEMENTATION_SPEC.md
|  |- SERVICE_CONTRACTS.md
|  |- MEMORY_ARCHITECTURE.md
|  |- PROJECT_STRUCTURE.md
|
|- workers/
|  |- llm-worker/
|  |- embedding-worker/  # Redis Streams queue worker (async path)
|  |- vision-worker/
|- shared/
|- frontend/
|- infra/
|- logs/                # Service and worker log separation
```

## Target Structure (Soll)

```text
LIARA/
|- services/
|  |- api/
|  |- orchestrator/
|  |- inference/
|  |- tools/
|  |- validator/
|  |- memory/
|
|- workers/
|  |- llm-worker/
|  |- embedding-worker/
|  |- vision-worker/
|
|- shared/
|  |- schemas/
|  |- contracts/
|  |- utils/
|  |- config/
|
|- frontend/
|  |- qt-ui/
|  |- web-ui/
|
|- infra/
|  |- docker/
|  |- compose/
```

## Migration Rule

- Canonical runtime code lives in `services/*`.
- No direct database access outside Memory service boundaries.
- New work should preserve contract compatibility during moves.
