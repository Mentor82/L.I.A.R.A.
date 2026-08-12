"""
IMPLEMENTATION SPEC: What each service MUST do.

This is the contract that the parallel teams will implement against.
"""

# ============================================================================
# POST-V1 ARCHITECTURE DELTAS (FROM README TARGET)
# ============================================================================

"""
Purpose:
- Align implementation work with the decoupled target architecture defined in README.md.
- Keep current v1 behavior stable while introducing service boundaries incrementally.

Global guardrails:
1) No breaking changes to existing API/orchestrator contracts during migration.
2) Every extraction step must provide a compatibility adapter for in-process execution.
3) Migration work is complete only when tests pass in both modes:
  - In-process mode (current v1)
  - Service-boundary mode (new decoupled path)

TEAM 1 DELTAS (Tools + Inference + Orchestration split):
- T1-D1: Define explicit interfaces for router/planner/executor responsibilities.
- T1-D2: Extract provider adapters for inference gateway (`providers/ollama`, `providers/openvino`) under one stable interface.
- T1-D3: Introduce stream normalization contract for token/event/final response envelopes.
- T1-D4: Add async invocation adapter for orchestrator -> inference (direct now, queue-ready next).
- T1-D5: Add migration contract tests proving no regression for current API responses.

TEAM 2 DELTAS (Memory + Integration boundary):
- T2-D1: Define memory boundary schemas for `history`, `facts`, `retrieval`, `embedding` operations.
- T2-D2: Separate storage implementation (Redis/Postgres/Qdrant/Neo4j) from boundary handlers.
- T2-D3: Specify health/degraded behavior for partial backend outages.
- T2-D4: Add integration tests validating identical behavior in in-process and service-boundary modes.
- T2-D5: Provide migration map from current MemoryLayer calls to liara-memory service endpoints.

Exit criteria for this delta phase:
- Team boards (TODO_TEAM1.md, TODO_TEAM2.md) tracked D1-D5 items during the migration wave and now remain as historical archive.
- Service contracts are versioned and reviewed by Team Lead.
- Full suite remains green in in-process mode throughout migration.
"""

# ============================================================================
# TEAM-1 FINAL REVIEW NOTE (2026-04-14)
# ============================================================================

"""
Owner: Team Lead
Status: ✅ DONE

Summary:
- ToolCoordinator and InferenceGateway are production-ready for v1 scope.
- Inference error handling is now explicit via InferenceResult.status + error
  (instead of embedding error text in content).
- Hybrid mode returns the first successful provider, tracks failed/cancelled
  providers in metadata, and preserves winner telemetry.
- Optional live verification exists in tests/integration/test_inference_live.py
  behind RUN_LIVE_INFERENCE_TESTS=1.

Evidence:
- Full suite command (README-aligned):
  c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/unit c:/ai/LIARA/tests/integration -q
  Result: 119 passed, 20 skipped (latest baseline)

- Live Ollama E2E command:
  RUN_LIVE_INFERENCE_TESTS=1 c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/integration/test_inference_live.py -v
  Result: 11 passed

Contract impact:
- No breaking orchestrator interface change.
- InferenceResult contract changed additively (status, error), consumed safely.
"""

# ============================================================================
# TEAM BOARDS
# ============================================================================

"""
Historical migration boards:

  Team 1 (Tools + Inference):  docs/TODO_TEAM1.md
  Team 2 (Memory + Integration): docs/TODO_TEAM2.md

Do not use these files as live sprint boards anymore.
Current status belongs in docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md and the domain-specific docs.
"""

# ============================================================================
# SPEC 1: TOOL_COORDINATOR
# ============================================================================

"""
Module: services/tools/coordinator.py

Historical note:
  The examples below reflect an earlier tool surface snapshot.
  The current public/runtime path is centered on `sys`; legacy direct tools such as
  `web_search` and `current_time` should not be read as the active public CLI/API contract.

INPUT:
  List[ToolExecutionRequest] = [
    {tool_name: "sys", parameters: {command: "curl", args: ["https://example.com"]}, timeout: 30},
    {tool_name: "sys", parameters: {command: "date"}, timeout: 30},
  ]

OUTPUT:
  Dict[str_toolname, ToolExecutionResult] = {
    "sys": {status: "success", output: {...}, execution_ms: 125},
  }

BEHAVIOR:
- Execute tools ASYNCHRONOUSLY (asyncio.gather)
- Timeout after N seconds per tool
- Catch exceptions, return {status: "failed", error: "..."}
- Use tool_registry.get_tool(name) to load tool class
- Call tool.execute(**params)

REQUIREMENTS:
✅ Must handle parallel execution
✅ Must timeout gracefully
✅ Must validate parameters before execution
✅ Must preserve execution metadata (latency, etc)

DEPENDENCIES:
- services/tools/registry.py (get_tool_registry)
- All Tool subclasses in services/tools/builtin/
"""

# ============================================================================
# SPEC 2: INFERENCE_GATEWAY
# ============================================================================

"""
Module: services/inference/gateway.py

INPUT:
  InferenceRequest = {
    prompt: str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    provider: str = "hybrid",  # "ollama", "openvino", "hybrid"
    model: Optional[str] = None,
  }

OUTPUT:
  InferenceResult = {
    content: str,                     # empty string on failure
    provider: str,
    model: str,
    status: str,                      # "success" | "failed" | "timeout"
    error: Optional[str],             # populated when status != "success"
    ttft_ms: float,
    gen_ms: float,
    load_ms: float,
    winner_provider: Optional[str],   # For hybrid mode
    stop_reason: str,                 # "stop" | "length" | "error" | "timeout"
    metadata: Dict[str, Any],         # cancelled_providers, failed_providers
  }

BEHAVIOR:
- If provider="ollama": Call Ollama API (httpx)
- If provider="openvino": Call OpenVINO inference
- If provider="hybrid": Race both async, return fastest non-error result
- Errors: status="failed", error=<message>, content="" (never in content string)
- Hybrid all-fail: status="failed", error from last failure
- Hybrid timeout: status="timeout", error="timeout waiting for providers"

REQUIREMENTS:
✅ Must support 3 provider types
✅ Hybrid mode should timeout if one provider hangs
✅ Must measure TTFT (time to first token) and gen latency
✅ Must respect max_tokens parameter

DEPENDENCIES:
- ollama or httpx (for API calls)
- openvino.genai (for local inference)
- asyncio for racing
"""

# ============================================================================
# SPEC 3: MEMORY_LAYER (Session/Persistent Tiers)
# ============================================================================

"""
Module: services/memory/tier_store.py (FactStore, SessionStore implementations)

INPUT (for set):
  tier: MemoryTier.SESSION | PERSISTENT,
  key: str,
  value: Any,
  ttl_seconds: Optional[int],

OUTPUT (for get):
  Any (the stored value)

BEHAVIOR:
- SessionStore (Redis): Fast, ephemeral, TTL default 15 min
- FactStore (Postgres): Persistent, no TTL, immutable writes
- Both implement MemoryStore interface
- Exceptions → MemoryError

REQUIREMENTS:
✅ Must persist data across service restarts (FactStore only)
✅ Must auto-expire data (SessionStore only)
✅ Must support CRUD operations atomically
✅ Must handle connection pooling

DEPENDENCIES:
- redis (for SessionStore)
- psycopg2 (for FactStore)
- Abstract base in services/memory/tier_store.py
"""

# ============================================================================
# V1 SUCCESS CRITERIA
# ============================================================================

"""
Integration test (test_orchestrator_flow.py) should pass with:
1. MockToolCoordinator → Real ToolCoordinator (drop-in replacement)
2. MockInferenceGateway → Real InferenceGateway (drop-in replacement)  
3. MockMemoryLayer → Real MemoryLayer (drop-in replacement)

No code changes to Orchestrator needed.
"""

# ============================================================================
# TEAM ASSIGNMENT (for 2 Parallel Teams)
# ============================================================================

"""
TEAM 1: Tool Execution + Inference
  ────────────────────────────────

  Files:
    - services/tools/coordinator.py → execute_tools_parallel()
    - services/inference/gateway.py → infer() + hybrid mode
  
  Tasks:
    1. ToolCoordinator.execute_tools_parallel()
       - Async execution of N tools in parallel
       - Timeout handling
       - Error handling
       
    2. InferenceGateway.infer()
       - _infer_ollama() via httpx
       - _infer_openvino() via openvino.genai
       - _infer_hybrid() - race both async, return faster
  
  Tests needed:
    - tests/unit/test_tool_coordinator.py
    - tests/unit/test_inference_gateway.py
  
  Dependencies:
    - ollama on localhost:11434
    - openvino.genai + models
  
  Blockers: None (code can work offline with mocks first)
  
  Order:
    a) ToolCoordinator (simpler) - 2-3 hours
    b) InferenceGateway (needs LLM) - 4-5 hours
  
  Estimated Total: 6-8 hours


TEAM 2: Memory + Integration
  ───────────────────────────

  Files:
    - services/memory/tier_store.py → SessionStore + FactStore implementations
    - Integration test verification
  
  Tasks:
    1. SessionStore (Redis)
       - Implement get/set/delete/exists
       - TTL support
       - JSON serialization
       
    2. FactStore (Postgres)
       - Create schema (tables for runs, messages, tool_executions)
       - Implement CRUD operations
       - Connection pooling
       
    3. Integration
       - Swap Mocks → Real Services in test_orchestrator_flow.py
       - Fix any import/contract mismatches
       - Run full integration test
  
  Tests needed:
    - tests/unit/test_memory_stores.py
  
  Dependencies:
    - Redis running on localhost:6379
    - Postgres running on localhost:5432
  
  Blockers:
    - Needs to wait for Team1 to publish first versions
    - Can start with schema design immediately
  
  Order:
    a) Schema + FactStore (0 dependencies) - 2-3 hours
    b) SessionStore (depends on working Connector) - 1-2 hours
    c) Integration testing (depends on Team1) - 2 hours
  
  Estimated Total: 5-7 hours


WORKFLOW:
  ────────
  
  Day 1:
    - Team1 starts: ToolCoordinator (simpler, no blocker)
    - Team2 starts: Database schema + FactStore skeleton
  
  Day 2:
    - Team1: InferenceGateway (can mock tools from Team1)
    - Team2: SessionStore + Unit tests
  
  Day 3:
    - Team1: Unit tests passing
    - Team2: Schema migrations + Memory tests
  
  Day 4:
    - Both: Integration test with real services
    - Team Lead: Deploy verification
"""

# ============================================================================
# TEAM-2 HANDOFF CHECKPOINTS (ACTIONABLE)
# ============================================================================

"""
Status baseline:
- Team 1 delivery is available and validated.
- Current verification: full unit+integration suite is green (`119 passed, 20 skipped`).

Use the checkpoints below in order. Do not skip gates.

CHECKPOINT C0 — Lock baseline  ✅ DONE (2026-04-14)
Owner: Team Lead
Goal: Ensure Team 2 starts from known-good contracts.
Steps:
  1) Pull latest branch with Team 1 changes.
  2) Run:
  c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/unit/test_tool_coordinator.py c:/ai/LIARA/tests/unit/test_inference_gateway.py c:/ai/LIARA/tests/integration/test_orchestrator_flow.py -q
Gate:
  - Must be green before Team 2 begins integration work.
Evidence:
  - Baseline commands executed before Team-2 cutover.
  - Tool/Inference/Orchestrator contract paths remained stable during migration.

CHECKPOINT C1 — FactStore readiness  ✅ DONE (2026-04-14)
Owner: Team 2
Goal: Persistent store available behind existing interface.
Steps:
  1) Implement/verify FactStore initialize(), get(), set(), delete(), exists().
  2) Confirm schema creation for runs, messages, tool_executions.
  3) Add unit tests for upsert + exists + delete + close behavior.
Gate:
  - New memory unit tests pass.
  - No changes required in Orchestrator contract models.
Evidence:
  - FactStore implementation completed in `services/memory/tier_store.py`.
  - Coverage in `tests/unit/test_memory_stores.py` validates CRUD + schema/init behavior.

CHECKPOINT C2 — SessionStore readiness  ✅ DONE (2026-04-14)
Owner: Team 2
Goal: Redis-backed session tier operational.
Steps:
  1) Implement SessionStore get(), set(), delete(), exists().
  2) Support TTL in set() and verify expiration logic with tests.
  3) Normalize serialization format (JSON-safe values only).
Gate:
  - SessionStore unit tests pass.
  - API unchanged for MemoryLayer consumers.
Evidence:
  - SessionStore implementation completed in `services/memory/tier_store.py` with TTL semantics.
  - Unit tests validate serialization and expiration behavior.

CHECKPOINT C3 — Real MemoryLayer wiring  ✅ DONE (2026-04-14)
Owner: Team 2
Goal: Replace mock memory with real MemoryLayer instance.
Steps:
  1) Add a test fixture that builds MemoryLayer with real FactStore + SessionStore.
  2) Keep RetrievalIndex and GraphStore as stubs for v1.
  3) Inject the real MemoryLayer into orchestrator integration setup.
Gate:
  - Integration tests still pass with real memory on session + persistent tiers.
Evidence:
  - Real MemoryLayer fixture wired in `tests/integration/test_orchestrator_flow.py`.
  - Integration suite remained green with session+persistent real stores.

CHECKPOINT C4 — Drop-in replacement validation  ✅ DONE (2026-04-14)
Owner: Team 2 + Team Lead
Goal: Prove contract compatibility.
Steps:
  1) In integration tests, swap only:
     MockMemoryLayer -> MemoryLayer(...)
  2) Keep Team-1 services unchanged.
  3) Re-run orchestrator integration suite.
Gate:
  - No breaking Orchestrator response contract changes.
  - Response schema remains unchanged.
Evidence:
  - real_memory_layer fixture added to tests/integration/test_orchestrator_flow.py.
  - MemoryLayer built with real FactStore (FakePool) + real SessionStore (InMemoryRedisClient)
    wired to SESSION and PERSISTENT tiers.
  - Orchestrator response schema remained stable through memory-boundary migration.
  - Verification: `c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/integration -q` → 6 passed.

CHECKPOINT C5 — Full v1 smoke run  ✅ DONE (2026-04-14)
Owner: Team Lead
Goal: Verify all moving parts together.
Steps:
  1) Run full set:
    c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/unit c:/ai/LIARA/tests/integration -q
  2) Execute one manual orchestrator run with a known query and verify:
     - state transitions recorded
     - tools_executed populated
     - validation_result present
Gate:
  - Full suite green.
  - No breaking contract changes.
Evidence (2026-04-14):
  Command:
    c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/unit c:/ai/LIARA/tests/integration -q
  Result:
    119 passed, 20 skipped in 0.86s
  Manual orchestrator check (test_full_pipeline_run):
    - state COMPLETE reached
    - tools_executed populated for time-query
    - validation_result confidence ≥ 0.5
    - final_response non-empty

CHECKPOINT C6 — Release handoff  ✅ DONE (2026-04-14)
Owner: Team Lead
Goal: Prepare Team 3/v2 work without regressions.
Deliverables:
  - Short changelog of MemoryLayer behavior.
  - Test evidence (command + pass count).
  - Open risks list (e.g., redis downtime, postgres pool sizing).

Changelog (v1 MemoryLayer):
  - SESSION tier: Redis-backed SessionStore; TTL default 900 s; JSON-only values.
  - PERSISTENT tier: Postgres FactStore; UPSERT keyed by (store_id, key); schema auto-created on initialize().
  - RETRIEVAL + GRAPH tiers: RetrievalIndex + GraphStore stubs; raise NotImplementedError — reserved for v2.
  - MemoryLayer.get/set/delete/exists route by MemoryTier enum; raises MemoryError on unknown tier.
  - Both stores accept injectable clients (redis client / pool_factory) for test isolation — no live services needed in CI.

Test evidence:
  Command: c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/unit c:/ai/LIARA/tests/integration -q
  Result:  119 passed, 20 skipped in 0.86s  (2026-04-14 latest)

Open risks for v2:
  - Redis downtime: SESSION data lost; fallback to in-process dict not yet implemented.
  - Postgres pool exhaustion: psycopg2 SimpleConnectionPool; no async support; block under high concurrency.
  - RetrievalIndex (Qdrant) not wired: semantic memory lookups silently unavailable.
  - GraphStore (Neo4j) not wired: relationship-based recall unavailable.
  - Legacy direct tools are no longer part of the regular public tool surface; migration debt can still appear in older specs/tests/docs.

Rollback rule:
- If C4 fails, revert only the integration wiring (real MemoryLayer -> mock) and keep Team 2 code on branch for fix-forward.
"""
