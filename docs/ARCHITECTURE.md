# LIARA Architecture (As-Is Runtime)

Status: Code-verified runtime reference, updated on 2026-07-13.

This document describes the currently implemented and operated architecture.
It is intentionally operational and avoids target-state speculation.

## 1) Scope and Source of Truth

Primary architectural truth for runtime behavior:

- services/**
- scripts/**
- .vscode/tasks.json
- docs/INFERENCE_ROUTING_MATRIX.md

Non-authoritative for runtime truth (historical or generated):

- backups/**
- build/**
- liara.egg-info/**

## 2) Executive Summary

LIARA is a stateful, agentic orchestration runtime with:

- FastAPI entry and SSE streaming
- Orchestrator-based routing/planning/execution/validation
- Deterministic, policy-gated tool layer
- Memory service contracts with local/remote adapters
- Multi-provider inference gateway (llama_cpp, ollama, openvino)
- Centralized Windows-friendly service lifecycle guard
- Native WSL sessions for confined tests, compute and code simulation

Core dependency order in operation:

1. memory
2. embedding
3. api
4. bridge

## 3) Runtime Topology

```text
Client (Web/UI/Bridge)
  -> liara-api (services/api/app.py)
    -> orchestrator (services/orchestrator/*)
      -> tool coordinator (services/tools/coordinator.py)
      -> memory adapter (services/memory_adapter.py)
      -> inference gateway (services/inference/gateway.py)
      -> validator/judge (services/orchestrator/validator.py, services/judge/*)
    -> response envelope (/chat JSON or /chat/stream SSE)
```

## 4) Service Lifecycle and Operations

Primary controller:

- scripts/service_guard.py

Implemented behavior:

- lock-file and named mutex lifecycle control
- start/stop/status/recover actions
- stale lock detection via heartbeat timestamp
- startup timeout handling and process cleanup
- JSONL guard audit log at logs/services/service_guard.jsonl

Service definitions:

- api: services.api.app:app on 8010
- bridge: scripts.continue_openai_bridge:app on 8011
- memory: services.memory.app:app on 8020
- embedding: services.embedding.app:app on 8030

WSL preflight behavior (guard):

- active only when LIARA_SANDBOX_MODE=wsl
- probes distro reachability via wsl.exe
- attempts one self-heal cycle via wsl --shutdown on timeout
- fails fast with explicit preflight error when distro is unavailable

Task orchestration:

- .vscode/tasks.json uses service_guard start/stop/status/recover tasks
- liara-start-all is sequential and starts memory -> embedding -> api -> bridge
- liara-start-reload-core starts embedding/api/bridge reload tasks (memory is not in this reload bundle)

## 5) API Layer (services/api/app.py)

Major responsibilities:

- request/session handling
- attachment security scanning
- orchestration handoff
- history writes and session snapshots
- SSE progress/chunk/final streaming
- admin/system audit views

Code-declared routes:

- GET /health
- GET /health/backends
- POST /memory/relations/cleanup-expired
- POST /chat
- POST /chat/stream
- POST /compute/run
- GET /compute/models
- POST /compute/generate
- GET /history
- GET /session
- POST /session
- POST /files/upload
- GET /files/artifact
- GET /tools
- GET /tools/{tool_name}
- POST /tools/{tool_name}/invoke
- GET /admin/sys-audit/summary
- GET /admin/sys-audit/suspicious
- GET /admin/sys-audit/presets/{preset_name}
- GET /admin/llama-backends

## 6) Orchestrator Layer (services/orchestrator/*)

Primary modules:

- orchestrator.py
- router.py
- planner.py
- executor.py
- context_strategy.py
- librarian_router.py
- validator.py

Implemented responsibilities:

- route and tool selection
- context loading/compression
- prompt assembly and policy injection
- tool/inference execution orchestration
- validation-driven finalization

Notable behavior:

- memory shortcut intro/recall patterns are implemented in orchestrator.py
- policy content is assembled from config/system_promt.yaml

Decision explainability and adaptive control (implemented runtime):

- decision metadata is assembled in services/orchestrator/defs/decision_context.py and merged into validation output
- law interactions are exposed as deterministic conflict_resolution metadata (winner + overridden candidates with priority/weight)
- validation payload includes explainability and decision path compatibility fields for clients (decision_trace + decision_path alias)
- runtime threshold adaptation can apply guarded profile deltas per session and emit threshold_adaptation diagnostics
- degraded outcomes trigger outcome-guarded rollback to the baseline profile (rolled_back=true, reason=outcome_degraded)

Key implementation anchors:

- services/judge/engine.py (_merge_decisions ranking merge)
- services/orchestrator/orchestrator.py (_resolve_reasoning_threshold_profile, _maybe_apply_runtime_threshold_adaptation)
- services/orchestrator/defs/reasoning_metrics.py (validation math signals including triggered_laws/conflict_resolution)

## 7) Tool Layer (services/tools/*)

Primary runtime modules:

- services/tools/coordinator.py
- services/tools/registry.py
- services/tools/builtin/*

Currently registered tools in global registry:

- `sys` -> WslExecutorTool
- OrientationTool
- ComputeTool
- ComputeGenerateTool
- PlotChartTool
- WslSessionTool

Archived (not runtime-registered) tools:

- The following legacy tool implementations were moved from services/tools/builtin/* to services/tools/old/* to keep runtime scope explicit:
  - web_search.py
  - session_context.py
  - read_file.py
  - list_files.py
  - fetch.py
  - current_time.py
- These archived modules are retained for reference and optional reuse only; they are not imported by services/tools/registry.py.
- Archived modules were debranded to avoid direct LIARA coupling (generic env keys/user-agent naming).

Current public tool surface exposed via the API:

- `sys`
- `orientation`
- `plot_chart`

Coordinator behavior:

- centralized parameter validation
- timeout-wrapped tool execution
- parallel execution support
- simulation mode support via MockResultGenerator when ToolExecutionRequest.simulation_mode=True

Native WSL execution is a separate path from mock simulation:

```text
canonical C:\ai\LIARA
  -> filtered snapshot in /home/liara/workspace/sessions/<id>/source
  -> mutable session work tree
  -> policy-gated sys execution (including Julia)
  -> collected patch/candidate/hashes
  -> existing validator and governance gates
```

`WslSessionTool` owns only the workspace lifecycle. `WslExecutorTool` remains
the command-policy boundary. The session runtime has no automatic write-back
to the canonical Windows tree.

## 8) Inference Layer (services/inference/*)

Primary modules:

- services/inference/gateway.py
- services/inference/invocation.py
- services/inference/normalizer.py
- services/inference/queue.py
- services/inference/providers/llama_cpp.py
- services/inference/providers/ollama.py
- services/inference/providers/openvino.py

Gateway-supported provider modes:

- llama_cpp
- llama_cpp_auto
- ollama
- openvino
- ll_ol_fallback (llama_cpp -> ollama)
- hybrid (ollama/openvino race)

Default provider in gateway config path:

- DEFAULT_LLM_PROVIDER defaults to ll_ol_fallback when not overridden

Operational routing policy (documented baseline):

- agent-near primary inference on llama
- co_worker pinned to llama
- fallback chain to ollama gpu/cpu class paths
- openvino fp16 used for side-inference acceleration

Reference policy document:

- docs/INFERENCE_ROUTING_MATRIX.md

## 9) Memory Layer (services/memory* and services/memory_adapter.py)

Memory service API surface (services/memory/app.py):

- history: /history/append, /history/query
- facts: /facts/upsert, /facts/query
- retrieval: /retrieval/upsert, /retrieval/query
- embedding: /embedding/generate
- context: /context/search, /context/upsert
- relations: /relations/upsert, /relations/expand, /relations/cleanup-expired
- health: /health, /health/backends

Adapter modes:

- RemoteMemoryAdapter (HTTP-backed)
- InProcessMemoryAdapter (in-process)

Important current behavior:

- InProcessMemoryAdapter returns failed status on history backend errors
- BackedMemoryServiceStore applies in-memory fallback for history append/query when primary fails
- fallback responses are marked degraded/partial with metadata (fallback_backend=in-memory)
- relation lifecycle cleanup is explicit via /relations/cleanup-expired (memory service)
- liara-api exposes POST /memory/relations/cleanup-expired as trigger/proxy to the memory service route in service mode
- Memory lifecycle governance is phase-gated (scope-link, promotion, cleanup, pattern-learning) via env flags
- promotion uses separate thresholds for candidate vs validated context before Qdrant persistence
- promotion can require a positive judge decision and optional minimum judge confidence before persistence
- promotion relevance can blend retrieval relevance and reasoning relevance, and can include a cross-session pattern bonus
- context promotion writes stable Neo4j relation links only after successful retrieval upsert
- context upsert can aggregate cross-session content patterns and emit pattern metadata for governance decisions
- cleanup can be policy-disabled or judge-blocked and returns relation_cleanup_disabled_by_policy without deleting edges

## 10) Streaming and Run Flow

### /chat flow

```text
1. API accepts request.
2. User message written to history.
3. Orchestrator executes route/tool/inference path.
4. Validator/Judge evaluates output.
5. Explainability + conflict resolution + threshold adaptation metadata are attached to validation.
6. Assistant message written to history.
7. Session snapshot updated.
8. Final JSON response returned.
```

### /chat/stream flow

Common progress stages:

- accepted
- history_user_written
- orchestration_started
- orchestration_complete
- memory_effect_detected (only when context mode resolves to MEMORY)
- history_assistant_written
- session_snapshot_written

Then SSE chunk/artifact/final/done events are emitted.

## 11) Windows and WSL Sandbox Behavior

Source: services/shared/sandboxing.py

Current defaults:

- sandbox mode default is local (also on Windows)
- WSL mode is opt-in via LIARA_SANDBOX_MODE=wsl

WSL path resilience:

- UNC path resolution failures fall back to local LIARA_READ_ROOT/path.cwd resolution
- this avoids hard startup failures when \\wsl$ is temporarily unavailable

## 12) Reliability and Testing Practice

Runtime reliability rules in current operation:

- use guard-based lifecycle operations instead of ad-hoc uvicorn launches
- verify dependency order before API checks
- use small, targeted test batches under constrained performance conditions
- run live stream regression via scripts/live_stream_regression_check.py for isolated validation
- run live safety regression matrix via pytest integration test with explicit opt-in env flag

Key operational tasks:

- liara-services-status
- liara-services-stop-all
- liara-services-recover-all
- liara-live-stream-regression-check
- liara-safety-regression-live

Live CI-style safety regression (implemented):

- test module: tests/integration/test_safety_regression_live.py
- activation: RUN_LIVE_REGRESSION=1
- default endpoint: LIARA_API_BASE_URL=[http://127.0.0.1:8010](http://127.0.0.1:8010)
- scenario: 6-turn matrix in one shared session
  1. important_seed
  2. recurring_1
  3. neutral
  4. violation_soft
  5. violation_hard
  6. recurring_2
- enforced checks:
  - recurring turns must recall Neo4j
  - violation turns must refuse and must not leak actionable harm terms
  - suspicious audit endpoint must report at least one hit for the session
- execution path:
  - VS Code task liara-safety-regression-live
  - python -m pytest tests/integration/test_safety_regression_live.py -v --tb=short

## 13) Known Operational Risks and Nuances

- API startup can still fail when prerequisite infrastructure is unavailable (for example WSL distro hangs in explicit WSL mode).
- shared API instances under concurrent admin polling can skew live test timings; isolated regression script is preferred for deterministic checks.
- reload-core task sequence excludes memory reload by design; this is intentional but must be understood during hot-reload troubleshooting.
- post-violation recall contamination is mitigated by redacting safety-blocked user prompts in history; the live safety matrix verifies recurring recall remains stable after violation turns.

## 14) Related Documents

- docs/INFERENCE_ROUTING_MATRIX.md
- docs/ADMIN_TUI_INTEGRATION.md
- docs/AUDIT_RUN_CHECKLIST.md
- docs/AUDIT_SOURCE_OF_TRUTH.md
- docs/CHAT_TOOL_FLOW_DOCUMENTED.md
- docs/API_REFERENCE.md
