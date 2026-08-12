# Optimization Backlog (2026-04-24)

## Scope
This document captures the current high-impact optimization opportunities after validating sys-only UTC/Fibonacci and direct WSL Julia execution.

## Priority 1: Strict Output Compliance for Numeric-Only Requests ✅ DONE (via system content)
Problem:
- Queries like "Gib nur die Zahl zurueck" can still produce wrapper text and references.

Evidence:
- logs/manual_tests/manual_julia_retest_20260424-143756.json (response includes text wrapper)
- logs/manual_tests/manual_julia_retest_20260424-143756.json (tool output already contains clean numeric value)

Resolution (2026-04-26):
- Addressed via system content / system prompt constraints.
- No additional post-formatting code path required.

## Priority 2: Make Julia Path Resolution Configurable ✅ DONE (2026-04-26)
Problem:
- Direct WSL julia execution depended on a user-specific juliaup path.

Resolution:
- Added env-based override via `LIARA_WSL_JULIA_PATH`.
- Added deterministic fallback strategy in `services/tools/builtin/wsl_executor.py`:
  1) env override
  2) WSL discovery via `command -v julia`
  3) default `/home/liara/.juliaup/bin/julia`
- Added telemetry metadata (`julia_resolution`) for strategy and resolved path.
- Covered by unit tests in `tests/unit/test_wsl_executor.py`.

Optimization target:
- Introduce env-based override (for example LIARA_WSL_JULIA_PATH).
- Add discovery fallback strategy:
  1) explicit env path
  2) command -v julia in WSL
  3) default juliaup path
- Emit clear telemetry when fallback path is used.

## Priority 3: Regression Tests for Time/UTC Intent Routing ✅ DONE (2026-04-26)
Problem:
- Time requests previously fell back to web lookup for some phrasing.

Resolution:
- Extended UTC/ISO intent detection in `services/orchestrator/sys_selector.py`.
- Added regression tests for phrasing variants:
  - `aktuelle UTC-Zeit`
  - `ISO-8601 UTC`
  - `current utc time`
- Assertions now cover `date` command with UTC args and normalized `time_lookup` output including `utc_iso`.
- Covered in `tests/unit/test_sys_selector.py` and `tests/unit/test_orchestration_split.py`.

Optimization target:
- Add automated tests for phrasing variants:
  - "aktuelle UTC-Zeit"
  - "ISO-8601 UTC"
  - "current utc time"
- Assert selected sys command is date with UTC args.
- Assert tool output kind is time_lookup and includes utc_iso.

## Priority 4: Clarify and Separate Execution Paths in Docs and Tests ✅ DONE (2026-04-26)
Problem:
- "sys + julia" behavior can be misunderstood as direct shell execution only.

Resolution:
- Documented both explicit Julia execution paths in `docs/ARCHITECTURE.md`.
- Added explicit dual-path smoke entries to `docs/AUDIT_RUN_CHECKLIST.md`.
- Verified both smokes separately:
  - `scripts/julia_sys_smoke_test.py`
  - `scripts/compute_run_api_smoke_test.py --with-server`

Optimization target:
- Document two explicit paths:
  1) direct WSL shell execution via WslExecutorTool
  2) orchestrator JuliaBridge compute path
- Add separate smoke tests for each path and include both in audit checklist.

## Priority 5: Prevent Code Drift from build/lib Duplicates ✅ DONE (2026-04-26)
Problem:
- Duplicated generated code under build/lib can diverge from source and confuse packaging/runtime checks.

Resolution:
- Added ignore strategy via `.gitignore` entry for `build/lib/`.
- Added runtime guard tests in `tests/unit/test_build_lib_import_guard.py`:
  - fails if `sys.path` includes `build/lib`
  - fails if key `services.*` modules resolve from `build/lib`

## Suggested Order of Implementation
1. Numeric-only response hardening (highest user-visible quality gain)
2. Julia path configurability and telemetry
3. UTC routing regression tests
4. Path separation documentation and smoke checks
5. build/lib drift guard
