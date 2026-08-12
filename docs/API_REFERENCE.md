# LIARA API Reference (Current State)

Stand: 2026-04-28
Basis:

- `services/api/app.py`
- `services/memory/app.py`
- `services/embedding/app.py`
- `services/embedding_dev/app.py`
- `services/contracts/service_boundaries.py`

## Service Landscape

### 1) liara-api (main entrypoint)

- Default URL: `http://127.0.0.1:8010`
- Start:

```bash
python -m uvicorn services.api.app:app --host 0.0.0.0 --port 8010
```

- Env vars:
  - `LIARA_API_BIND_HOST` (default `0.0.0.0`)
  - `LIARA_API_PORT` (default `8010`)

### 2) liara-memory (service boundary)

- App: `services/memory/app.py`
- Typed memory endpoints for history/facts/retrieval/context/relations/embedding/health

### 3) liara-embedding (dedicated embedding service)

- App: `services/embedding/app.py`
- Endpoint set is optimized for embedding generation and embedding health

### 4) embedding-dev (dev-only embedding service)

- App: `services/embedding_dev/app.py`
- Simplified health + embedding endpoint pair

## Core Models (liara-api)

### ChatRequest

```json
{
  "session_id": "string",
  "user_id": "string",
  "message": "string",
  "attachments": [
    {
      "name": "notes.txt",
      "media_type": "text/plain",
      "text_content": "optional inline file text",
      "content_url": "optional URL for binary/remote content",
      "size_bytes": 123,
      "source": "continue-openai-bridge",
      "metadata": {}
    }
  ],
  "tools_override": ["string"],
  "available_tools": [
    {
      "type": "function",
      "function": {
        "name": "external_tool",
        "description": "optional external tool definition",
        "parameters": {}
      }
    }
  ],
  "allow_external_tool_calls": false,
  "tool_results": [
    {
      "tool_name": "external_tool",
      "status": "success",
      "output": {}
    }
  ],
  "max_tokens": 2048,
  "preferred_provider": "llama_cpp",
  "preferred_model": "qwen2.5-3b-ollama-export.gguf",
  "sandbox_root": "string",
  "user_feedback_score": 0.84,
  "user_feedback_stars": 5
}
```

Required:

- `session_id`
- `user_id`
- `message`

Optional:

- `attachments`
- `tools_override`
- `available_tools`
- `allow_external_tool_calls` (default `false`)
- `tool_results`
- `max_tokens` (default `2048`)
- `sandbox_root`
- `user_feedback_score` (`0.0..1.0`, optional)
- `user_feedback_stars` (`1..6`, optional; used when `user_feedback_score` is not provided)

Sandbox semantics:

- On Windows, LIARA now defaults to `LIARA_SANDBOX_MODE=wsl` unless overridden.
- In WSL mode, `sandbox_root` is treated as a canonical WSL path rooted at `LIARA_WSL_SANDBOX_ROOT` (default `/home/liara/workspace`).
- Relative values like `frontend` are normalized under that canonical WSL root.
- API-side local filesystem access uses a derived Windows path, optionally overridden with `LIARA_WSL_SANDBOX_WINDOWS_ROOT`.

Attachment scan semantics:

- `LIARA_ATTACHMENT_SCAN_MODE=wsl-clamd` runs `clamdscan` inside WSL Debian against the canonical WSL path.
- `LIARA_ATTACHMENT_SCAN_COMMAND` can override the exact WSL scan command and receives `{path}` as the canonical WSL file path.
- If `LIARA_ATTACHMENT_SCAN_ALLOW_FALLBACK=true`, scanner launch failures fall back to the built-in EICAR detector.

### ChatResponse

```json
{
  "run_id": "string",
  "response": "string",
  "tools_used": ["string"],
  "tool_outputs": {},
  "llm_provider": "string",
  "llm_model": "string",
  "ttft_ms": 12.3,
  "gen_ms": 45.6,
  "validation_passed": true,
  "pending_tool_calls": [
    {
      "id": "call_123",
      "type": "function",
      "function": {
        "name": "external_tool",
        "arguments": "{}"
      }
    }
  ],
  "artifacts": [
    {
      "kind": "image",
      "mime_type": "image/png",
      "title": "Revenue Trend",
      "url": "/files/artifact?session_id=session-a&path=.liara_artifacts%2Fsession-a%2Frevenue.png",
      "width": 960,
      "height": 540,
      "source_tool": "plot_chart",
      "metadata": {
        "stored_path": ".liara_artifacts/session-a/revenue.png",
        "session_id": "session-a"
      }
    }
  ],
  "metadata": {
    "state_final": "complete",
    "execution_trace": [],
    "validation": {
      "decision_explanation": {
        "primary_reason": "utility_negative",
        "secondary_reasons": ["cost_high"],
        "decision_confidence": 0.74,
        "decision_trace": ["check_policy", "check_risk", "check_utility", "check_feedback", "apply_soft_control"],
        "decision_path": ["check_policy", "check_risk", "check_utility", "check_feedback", "apply_soft_control"]
      },
      "explainability": {
        "triggered_laws": ["utility_negative", "feedback_floor_or_repair"],
        "decision_path": ["check_policy", "check_risk", "check_utility", "check_feedback", "apply_soft_control"],
        "decision_confidence": 0.74,
        "risk_score": 5.86,
        "resolution_basis": "utility"
      },
      "threshold_adaptation": {
        "applied": true,
        "session_id": "session-a",
        "sample_count": 5,
        "previous": {"soft_risk_max": 5.0, "hard_risk_max": 8.0},
        "recommended": {"soft_risk_max": 5.6, "hard_risk_max": 8.9},
        "applied_profile": {"soft_risk_max": 5.6, "hard_risk_max": 8.9, "version": "calib-20260429-090000"},
        "max_delta": 1.0,
        "strategy": "clamped_session_adaptation"
      },
      "math_signals": {
        "triggered_laws": ["utility_negative", "feedback_floor_or_repair"],
        "conflict_resolution": {
          "had_conflict": true,
          "winning_law": "utility_negative",
          "winning_priority": 70,
          "winning_weight": 0.7,
          "overridden_laws": ["feedback_floor_or_repair"],
          "strategy": "priority_then_weight"
        }
      }
    },
    "context_debug": {},
    "debug_run": {},
    "attachments": [],
    "attachment_scan_results": []
  }
}
```

Attachment metadata:

- `metadata.attachments`: normalized attachment summary stored with the user turn
- `metadata.attachment_scan_results`: per-attachment scan verdicts returned by the API before orchestration continues

Artifact metadata:

- `artifacts`: optional list of assistant artifacts (for example generated chart images)
- `artifacts[].url`: API fetch URL for rendering/downloading the artifact
- `artifacts[].metadata.stored_path`: sandbox-relative artifact file path

Validation metadata:

- `metadata.validation.decision_explanation.decision_path`: alias to `decision_trace`
- `metadata.validation.explainability`: compact top-level explainability block for clients
- `metadata.validation.explainability.triggered_laws`: resolved law candidates that influenced control resolution
- `metadata.validation.explainability.risk_score`: current actionable risk signal used in control selection
- `metadata.validation.threshold_adaptation`: runtime threshold adaptation decision/result for current session
- `metadata.validation.math_signals.conflict_resolution`: deterministic law conflict result (`winning_law`, `overridden_laws`, priorities/weights)

Threshold adaptation semantics:

- `applied=true`: recommended thresholds were accepted and clamped by guardrails.
- `rolled_back=true`: previous adaptive thresholds were reverted due to degraded outcomes.
- `reason`: explains non-application or rollback (for example `disabled`, `insufficient_samples`, `outcome_degraded`).

### SessionUpdateRequest

```json
{
  "session_id": "string",
  "user_id": "string",
  "sandbox_root": "string",
  "metadata": {}
}
```

In WSL sandbox mode, `SessionResponse.metadata` can also expose:

- `sandbox_root_mode`
- `sandbox_root_local`
- `sandbox_root_distro`

### SessionResponse

```json
{
  "session_id": "string",
  "user_id": "string",
  "message_count": 2,
  "last_run_id": "string",
  "updated_at": "2026-04-18T12:00:00+00:00",
  "metadata": {
    "sandbox_root": "string",
    "history_status": "success"
  }
}
```

### ToolInvokeRequest

```json
{
  "parameters": {},
  "timeout_seconds": 30,
  "simulation_mode": false
}
```

Validation:

- `timeout_seconds` range: `1..120`
- `simulation_mode` (optional): when `true`, the tool coordinator returns simulated/mock output

### ToolExecutionResult

```json
{
  "tool_name": "string",
  "status": "success",
  "output": {},
  "error": null,
  "execution_ms": 12.3
}
```

## liara-api Endpoints

### GET /health

Returns API liveness and configured backend availability flags.

Example:

```json
{
  "status": "ok",
  "service": "liara-api",
  "memory_mode": "in_process",
  "backends_configured": {
    "postgres": false,
    "redis": false,
    "qdrant": false,
    "chroma": false,
    "neo4j": false,
    "embedding": false
  }
}
```

Caching:

- `Cache-Control: public, max-age=5, stale-while-revalidate=10`
- `ETag: W/"..."`
- `Vary: Accept`
- Supports `If-None-Match` -> `304 Not Modified`

### GET /health/backends

Returns deep backend health via `BackedMemoryServiceStore.health_backends()`.

Response model:

- `MemoryHealthResponse`

Caching:

- `Cache-Control: no-store`

### POST /memory/relations/cleanup-expired

Explicit trigger for cleanup of expired ephemeral relation edges.

Behavior:

- In `MEMORY_MODE=service`, liara-api proxies to memory service route `POST /relations/cleanup-expired`.
- Otherwise liara-api executes cleanup via `BackedMemoryServiceStore.relation_cleanup_expired()`.
- If cleanup governance is blocked by policy (phase disabled or judge gate denied), no deletion is performed.

Request:

- Body: `RelationCleanupExpiredRequest`

```json
{
  "now_ts": 1714300000.0,
  "session_id": "session-a",
  "run_id": "run-123",
  "limit": 5000,
  "judge_decision": "allow",
  "judge_confidence": 0.91
}
```

Response:

- `200` with `RelationCleanupExpiredResponse`

```json
{
  "removed": 12,
  "status": {
    "status": "success",
    "backend": "memory-service",
    "degraded": false,
    "error": null,
    "metadata": {
      "scope_session_id": "session-a",
      "scope_run_id": "run-123",
      "now_ts": 1714300000.0
    }
  }
}
```

Policy-disabled cleanup example:

```json
{
  "removed": 0,
  "status": {
    "status": "partial",
    "backend": "memory-service",
    "degraded": true,
    "error": "relation_cleanup_disabled_by_policy",
    "metadata": {
      "governance": "memory_lifecycle",
      "governance_phase": "cleanup",
      "governance_reason": "cleanup_judge_gate_blocked"
    }
  }
}
```

Caching:

- `Cache-Control: no-store`

### POST /chat

Synchronous chat call.

Request:

- Body: `ChatRequest`

Response:

- `200` with `ChatResponse`

Caching:

- `Cache-Control: no-store`

Errors:

- `400`: invalid `sandbox_root`
- `422`: request validation error or attachment blocked by malware scan

### POST /files/upload

Multipart upload endpoint for larger or binary files before a later `/chat` call.

Request:

- `multipart/form-data`
- Fields: `session_id`, `user_id`, `file`
- Optional field: `sandbox_root`

Response:

- `200` with uploaded attachment descriptor and scan verdict

Example response:

```json
{
  "attachment": {
    "name": "report.txt",
    "media_type": "text/plain",
    "text_content": "optional inline preview",
    "size_bytes": 11,
    "source": "liara-upload",
    "metadata": {
      "stored_path": "C:/.../.liara_uploads/session-id/uuid_report.txt",
      "stored_path_local": "C:/.../.liara_uploads/session-id/uuid_report.txt",
      "session_id": "session-id",
      "user_id": "user-id",
      "sandbox_root": "/home/liara/workspace/frontend",
      "sandbox_root_local": "C:/.../frontend",
      "scan": {
        "status": "clean",
        "engine": "builtin-eicar",
        "reason": null,
        "sha256": "...",
        "size_bytes": 11
      },
      "has_text_preview": true
    }
  },
  "scan": {
    "status": "clean",
    "engine": "builtin-eicar",
    "reason": null,
    "sha256": "...",
    "size_bytes": 11
  }
}
```

In WSL sandbox mode, `stored_path` is the canonical WSL path and `stored_path_local`
is the derived Windows-accessible path used by the API process.

Errors:

- `400`: invalid `sandbox_root`
- `413`: upload exceeds `LIARA_UPLOAD_MAX_BYTES`
- `422`: file blocked by malware scan

### GET /files/artifact

Read a session-scoped artifact file (for example plot images produced by tools).

Query params:

- `session_id` (required)
- `path` (required): sandbox-relative artifact path, e.g. `.liara_artifacts/session-a/chart.png`
- `sandbox_root` (optional)

Response:

- `200` binary file payload
- `Cache-Control: private, no-store`

Errors:

- `400`: invalid `sandbox_root`
- `403`: artifact path outside allowed boundary or session scope
- `404`: artifact file not found

### POST /chat/stream

SSE streaming chat call.

Request:

- Body: `ChatRequest`

Response:

- `200`
- `Content-Type: text/event-stream`
- `Cache-Control: no-store`

Event types emitted:

1. `progress`

```text
event: progress
data: {"stage":"accepted","message":"Chat request accepted","run_id":"...","session_id":"...","user_id":"...","ts":"...","metadata":{...}}
```

1. `heartbeat`

```text
event: heartbeat
data: {"ts":"...","stage":"orchestration_started","elapsed_ms":12345}
```

1. `chunk`

```text
event: chunk
data: {"run_id":"...","index":0,"text":"..."}
```

1. `artifact`

```text
event: artifact
data: {"run_id":"...","index":0,"artifact":{...}}
```

1. `final`

```text
event: final
data: { ... ChatResponse JSON ... }
```

1. `done`

```text
event: done
data: {}
```

Notes:

- Heartbeat interval is controlled by `LIARA_STREAM_HEARTBEAT_SECONDS` (minimum `0.1`).
- At least one `chunk` event is always emitted, even if response text is empty.
- If artifacts are available, one `artifact` event is emitted per artifact before `final`.
- `progress` stages include: `accepted`, `history_user_written`, `orchestration_started`, `orchestration_complete`, `history_assistant_written`, `session_snapshot_written`, and optionally `memory_effect_detected`.

Errors:

- `400`, `422` same semantics as `/chat`

### GET /history

Returns history from the configured memory adapter.

Query params:

- `session_id` (required)
- `run_id` (optional)
- `limit` (optional, default `50`, range `1..500`)
- `include_tool_messages` (optional, default `true`)

Response model:

- `MemoryHistoryResponse`

Caching:

- `Cache-Control: private, no-store`

Errors:

- `422`: invalid query params

### GET /session

Returns session snapshot and message count.

Query params:

- `session_id` (required)
- `user_id` (required)

Response model:

- `SessionResponse`

Caching:

- `Cache-Control: private, no-store`

Errors:

- `422`: invalid query params

### POST /session

Upserts session metadata and optional sandbox root.

Request body:

- `SessionUpdateRequest`

Response model:

- `SessionResponse`

Caching:

- `Cache-Control: no-store`

Errors:

- `400`: invalid `sandbox_root`
- `422`: validation error

### GET /tools

Lists registered tools and metadata.

Runtime scope note:

- This endpoint only returns tools currently registered in `services/tools/registry.py`.
- Legacy tools moved to `services/tools/old/*` are intentionally excluded from `/tools` and `/tools/{tool_name}/invoke` unless they are re-registered.

Response:

```json
{
  "status": "success",
  "count": 3,
  "tools": [
    {
      "name": "sys",
      "description": "Canonical public gateway for system, filesystem, fetch, and compute actions",
      "required_parameters": ["command"],
      "optional_parameters": ["args", "source", "request_id", "session_id", "run_id", "context"]
    }
  ]
}
```

Current public tools are `sys`, `orientation`, and `plot_chart`.

Caching:

- `Cache-Control: public, max-age=300, stale-while-revalidate=600`
- `ETag`, `Vary: Accept`
- Supports `If-None-Match` -> `304 Not Modified`

### GET /tools/{tool_name}

Returns metadata for one tool.

Caching:

- `Cache-Control: public, max-age=300, stale-while-revalidate=600`
- `ETag`, `Vary: Accept`
- Supports `If-None-Match` -> `304 Not Modified`

Errors:

- `404`: unknown tool

### POST /tools/{tool_name}/invoke

Runs one tool manually.

Request:

- Body: `ToolInvokeRequest`

Response:

- `200` with `ToolExecutionResult`

Behavior note:

- The canonical direct execution path is `POST /tools/sys/invoke`.
- Legacy direct tools such as `read_file`, `list_files`, `web_search`, `fetch`, `current_time`, and `session_context` are not part of the regular public tool contract.

Caching:

- `Cache-Control: no-store`

Errors:

- `404`: unknown tool
- `422`: validation error

### GET /admin/sys-audit/summary

Returns aggregated metrics from `logs/services/sys_audit.jsonl`.

Query params:

- `limit` (optional, default `500`, range `1..5000`)
- `blocked_only` (optional, default `false`)
- `source` (optional, default `all`)
- `risk_level` (optional, default `all`)
- `command_family` (optional, default `all`)
- `log_path` (optional; override audit log file path)

Response fields:

- `summary.total`, `summary.allowed`, `summary.blocked`
- `summary.failed_allowed`, `summary.network_calls`, `summary.write_ops`, `summary.high_risk`
- `summary.avg_duration_ms`, `summary.top_sources`, `summary.top_contexts`
- `summary.inspected_entries`, `summary.filtered_entries`

Caching:

- `Cache-Control: no-store`

### GET /admin/sys-audit/suspicious

Returns suspicious audit events (blocked/high-risk/error-like/large output/slow).

Query params:

- `limit` (optional, default `500`, range `1..5000`)
- `max_items` (optional, default `30`, range `1..200`)
- `blocked_only` (optional, default `false`)
- `source` (optional, default `all`)
- `risk_level` (optional, default `all`)
- `command_family` (optional, default `all`)
- `log_path` (optional; override audit log file path)

Caching:

- `Cache-Control: no-store`

### GET /admin/sys-audit/presets/{preset_name}

Returns predefined sys-audit views for operations usage.

Path param:

- `preset_name` (one of: `top-risk`, `blocked-only`, `orchestrator-network-risk`)

Query params:

- `log_path` (optional; override audit log path)
- `limit` (optional; override preset limit, range `1..5000`)
- `max_items` (optional; override suspicious items cap, range `1..200`)

Response includes:

- resolved preset config
- filtered summary
- suspicious items for that preset

Unknown preset:

- `404` with `detail.available_presets`

Caching:

- `Cache-Control: no-store`

### GET /admin/llama-backends

Lists available llama.cpp build variants and shows which one is active.

Example response:

```json
{
  "build_base_dir": "C:/ai/LIARA/llama-builds-final",
  "configured_variant": "auto",
  "active_variant": "avx2",
  "active_binary": "C:/ai/LIARA/llama-builds-final/avx2/llama-server.exe",
  "available_builds": [
    {"variant": "avx2", "path": "...", "present": true},
    {"variant": "vulkan", "path": null, "present": false}
  ]
}
```

Caching:

- `Cache-Control: no-store`

### POST /compute/run

Runs a Julia simulation model.

Request body (raw JSON):

```json
{
  "model": "turbine_power",
  "inputs": {"wind_speed_ms": 12.5}
}
```

- `model` (required): must be in `JULIA_ALLOWLIST`
- `inputs` (required): model-specific parameters as JSON object

Response: model-specific output dict.

Errors:

- `422`: missing/invalid fields, model not in allowlist, simulation error

Caching:

- `Cache-Control: no-store`

### GET /compute/models

Lists available (allowlisted) Julia computation models.

Example response:

```json
{"models": ["turbine_power", "chat_math", "reasoning_metrics"]}
```

Caching:

- `Cache-Control: no-store`

### POST /compute/generate

Generates a new Julia computation model from natural language via LLM.

Request body (raw JSON):

```json
{
  "model_name": "solar_yield",
  "description": "Compute daily solar energy yield from panel area and irradiance",
  "inputs": {"area_m2": "Float64", "irradiance_wm2": "Float64"},
  "outputs": {"yield_kwh": "Float64"},
  "llm_provider": "hybrid"
}
```

- `model_name`, `description`, `inputs`, `outputs` are required.
- `llm_provider` is optional.

Errors:

- `422`: missing required field or generation failed

Caching:

- `Cache-Control: no-store`

## liara-memory Endpoints

Service app: `services/memory/app.py`

All endpoints use typed contracts from `services/contracts/service_boundaries.py`.

- `POST /history/append` -> `MemoryHistoryResponse`
- `POST /history/query` -> `MemoryHistoryResponse`
- `POST /facts/upsert` -> `MemoryFactResponse`
- `POST /facts/query` -> `MemoryFactResponse`
- `POST /retrieval/upsert` -> `MemoryRetrievalResponse`
- `POST /retrieval/query` -> `MemoryRetrievalResponse`
- `POST /embedding/generate` -> `MemoryEmbeddingResponse`
- `POST /context/search` -> `ContextSearchResponse`
- `POST /context/upsert` -> `ContextSearchResponse`
- `POST /relations/upsert` -> `RelationExpandResponse` (body: `RelationUpsertRequest`)
- `POST /relations/expand` -> `RelationExpandResponse` (body: `RelationExpandRequest`)
- `POST /relations/cleanup-expired` -> `RelationCleanupExpiredResponse` (body: `RelationCleanupExpiredRequest`)

### RelationCleanupExpiredRequest

Fields:

- `now_ts` (`float`, optional): Unix timestamp used as expiry cutoff. If omitted, service current time is used.
- `session_id` (`string`, optional): Restrict cleanup to one session.
- `run_id` (`string`, optional): Restrict cleanup to one run.
- `limit` (`int`, optional, default `5000`): Maximum number of candidate edges to delete in one call.
- `judge_decision` (`string`, optional): Optional cleanup gate signal (`allow`/`pass`/`ok` expected when judge-gate is enabled).
- `judge_confidence` (`float`, optional): Optional confidence score used when minimum judge confidence is configured.

Cleanup criteria:

- edge metadata has `ephemeral=true`
- `valid_until_ts` exists
- `valid_until_ts <= now_ts`

### RelationCleanupExpiredResponse

Fields:

- `removed` (`int`): Number of deleted relation edges.
- `status` (`MemoryServiceStatus`): operation status and backend metadata.

## Memory Governance Configuration

The lifecycle governance layer controls scope-linking, promotion, and cleanup phases.

Phase flags:

- `MEMORY_GOVERNANCE_ENABLED` (default `true`)
- `MEMORY_GOVERNANCE_SCOPE_LINK_ENABLED` (default `true`)
- `MEMORY_GOVERNANCE_PROMOTION_ENABLED` (default `true`)
- `MEMORY_GOVERNANCE_CLEANUP_ENABLED` (default `true`)
- `MEMORY_GOVERNANCE_PATTERN_LEARNING_ENABLED` (default `true`)

Judge gates:

- `MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION` (default `false`)
- `MEMORY_GOVERNANCE_CLEANUP_REQUIRE_JUDGE` (default `false`)
- `MEMORY_GOVERNANCE_JUDGE_MIN_CONFIDENCE` (default `0.55`)

Promotion thresholds:

- `MEMORY_PROMOTION_THRESHOLD_CANDIDATE` (default `0.82`)
- `MEMORY_PROMOTION_THRESHOLD_VALIDATED` (default `0.92`)

Relevance tuning:

- `MEMORY_REASONING_RELEVANCE_WEIGHT` (default `0.35`)
- `MEMORY_PATTERN_RELEVANCE_BONUS` (default `0.03`)

Related TTL control:

- `RELATION_EPHEMERAL_TTL_SECONDS` (default `3600`)

Operational notes:

- Scope-link phase writes ephemeral Neo4j `PART_OF` edges with `valid_until_ts`.
- Promotion phase can persist context to Qdrant and write a stable Neo4j `REFERENCES` edge.
- Promotion phase can blend `relevance_score` with `reasoning_relevance` and can include a cross-session pattern bonus.
- Promotion decisions can include judge signals in response metadata (`governance_judge_decision`, `governance_judge_confidence`).
- Pattern-learning phase can emit metadata such as `pattern_id`, `pattern_cross_session_count`, and `pattern_abstraction`.
- Cleanup phase removes expired ephemeral edges, unless policy-disabled or judge-blocked.

### RelationUpsertRequest — relation field

`relation` is now a validated enum (`RelationType`). Accepted values:

| Value | Usage |
| --- | --- |
| `USES_TOOL` | query → tool (orchestrator-generated) |
| `INFORMS_RESPONSE` | tool → response (orchestrator-generated) |
| `DIRECT_RESPONSE` | query → response, no tool (orchestrator-generated) |
| `REFERENCES` | entity references another entity |
| `DESCRIBES` | entity describes another (baseline semantic link) |
| `DEPENDS_ON` | entity depends on another |
| `SUPPORTS` | entity supports another |
| `CONTRADICTS` | entity contradicts another |
| `RESOLVES` | entity resolves another |
| `PRODUCES` | entity produces another |
| `SUMMARIZES` | entity summarizes another |
| `PART_OF` | entity is part of another |
| `FOLLOWS` | entity follows another |

Unknown strings are rejected by Pydantic with `422`.

Recommended rollout sequence for semantic relation extraction:
`DESCRIBES` -> `DEPENDS_ON` -> `SUPPORTS` -> `CONTRADICTS` -> `RESOLVES`.

### Graph node keys

Node identifiers (`source`, `target`) written by the orchestrator are normalized:

- Format: `<prefix>:<slug>:<sha1[:8]>`
- Lowercase, whitespace-collapsed, special chars stripped from slug (max 48 chars)
- Example: `query:was_ist_berlin:a3f1b2c4`
- Identical text with different casing/whitespace always maps to the same key

### LLM-based relation extraction

Controlled by env vars (both off by default):

- `RELATION_EXTRACTION_ENABLED=0` – set to `1` to enable an extra inference call per turn that extracts `(subject, relation, object)` triples from query+response content
- `RELATION_EXTRACTION_MAX_TRIPLES=5` – maximum triples extracted per turn

Extracted relations use prefix `entity:` for both source and target, `confidence: 0.55`, `validated: false`.

- `GET /health` -> `MemoryHealthResponse`
- `GET /health/backends` -> `MemoryHealthResponse`

## liara-embedding Endpoints

Service app: `services/embedding/app.py`

- `POST /embedding/generate` -> `MemoryEmbeddingResponse`
- `GET /health` -> `MemoryHealthResponse`
- `GET /health/dev` -> alias for `/health`

### liara-embedding /health — extended metadata

The embedding `/health` response includes additional monitoring fields inside `status.metadata`:

```json
{
  "status": {
    "metadata": {
      "runtime_stats": {
        "request_count": 120,
        "failed_count": 2,
        "failure_rate": 0.017,
        "fallback_rate": 0.05,
        "truncation_rate": 0.03,
        "cache_hit_rate": 0.42,
        "avg_latency_ms": 38.2,
        "max_latency_ms": 210.0,
        "runtime_backend_switch_count": 1
      },
      "alerts": {
        "active": ["high_fallback_rate"],
        "thresholds": {
          "truncation_rate_max": 0.05,
          "fallback_rate_max": 0.10,
          "failure_rate_max": 0.05
        }
      },
      "live_test_matrix": {
        "npu_openvino": {"configured": false},
        "cpu_fallback": {"configured": true},
        "compose_cpu": {"configured": false}
      }
    }
  }
}
```

Alert names: `high_truncation_rate`, `high_fallback_rate`, `high_failure_rate`, `runtime_backend_switched`.

Thresholds are controlled by env vars:

- `EMBEDDING_ALERT_TRUNCATION_RATE_MAX` (default `0.05`)
- `EMBEDDING_ALERT_FALLBACK_RATE_MAX` (default `0.10`)
- `EMBEDDING_ALERT_FAILURE_RATE_MAX` (default `0.05`)

## embedding-dev Endpoints

Service app: `services/embedding_dev/app.py`

- `GET /health` -> lightweight dev health JSON
- `POST /embedding/generate` -> `EmbeddingGenerateResponse`

## HTTP Caching Matrix (liara-api)

Cacheable:

- `GET /health` (short TTL)
- `GET /tools`
- `GET /tools/{tool_name}`

Non-cacheable:

- `POST /chat`
- `POST /chat/stream`
- `GET /health/backends`
- `GET /history`
- `GET /session`
- `POST /session`
- `POST /tools/{tool_name}/invoke`
- `GET /admin/llama-backends`
- `GET /compute/models`
- `POST /compute/run`
- `POST /compute/generate`
