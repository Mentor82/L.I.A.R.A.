# Audit Run Checklist

## Purpose

This checklist is a copy/paste-ready template for consistent backend audit runs.

## Pre-Run

- [ ] Confirm services are reachable (API, memory, redis, postgres).
- [ ] Confirm Python environment and pytest availability.
- [ ] Confirm required env vars for optional live checks are set.
- [ ] Create a new audit folder under logs/audits/[timestamp]/.

## Baseline Run

- [ ] Run environment baseline check.
- [ ] Run memory service health and backend checks.
- [ ] Run API health and backend checks.
- [ ] Restart LIARA services before live law-regression or replay audits.
- [ ] Run chat memory shortcut flow.
- [ ] Run LLM queue transport live check.
- [ ] Run embedding worker live check (or record explicit skip reason).
- [ ] Run inference queue contract checks.
- [ ] Run direct WSL Julia smoke (WslExecutorTool path).
- [ ] Run orchestrator JuliaBridge compute smoke (agent_julia_exec path).
- [ ] Run seeded law regression capture (`scripts/liara_law_regression_runner.py`).
- [ ] Run the supported assertion subset after capture (`scripts/liara_law_regression_runner.py --supported-only`).
- [ ] Expand semantic assertions only tranchewise: next law/threshold/audit semantics, then heavier response semantics.
- [ ] Run core Team1 suite.
- [ ] Run live stream regression.
- [ ] Run task reliability checks.

## Baseline Reporting

- [ ] Write baseline-only findings to AUDIT_REPORT_BASELINE.md.
- [ ] Record evidence filenames for every step.
- [ ] Assign component scores and compute total.
- [ ] List high-impact findings and recommended actions.

## Fix Cycle

- [ ] Apply minimal fixes for failed/skipped baseline findings.
- [ ] Re-run only affected checks.
- [ ] Save rerun evidence with distinct filenames (e.g. 08_*, 09_*, 10_*).

## Post-Fix Reporting

- [ ] Write post-fix-only outcomes to AUDIT_REPORT_POSTFIX.md.
- [ ] Add before/after score table.
- [ ] Keep open items explicit (known limitations).

## Index Update

- [ ] Update AUDIT_REPORT.md as index only.
- [ ] Link baseline and post-fix reports.
- [ ] Add quick summary and key evidence pointers.
- [ ] Add source-of-truth note.

## Source-Of-Truth Rule (Mandatory)

- [ ] Primary code citations must reference: services/**, workers/**, shared/**, tests/**, scripts/**, docs/**.
- [ ] Generated artifacts (build/**, build/lib/**, frontend/**/dist/**, liara.egg-info/**) are secondary evidence only.

## Traceability Rule (Mandatory)

- [ ] Every audit event should include request_id.
- [ ] Every audit event should include source.
- [ ] If missing in runtime input, audit output must show normalized defaults and missing-field markers.

## Final Validation

- [ ] Spot-check key evidence files exist.
- [ ] Ensure score math is correct.
- [ ] Ensure no baseline/post-fix content is mixed in one file.
- [ ] Ensure findings are ordered by severity.

## Quick Command Block

```powershell
# Example skeleton - adapt paths and task names to the current run
cd c:/ai/LIARA

# 1) Baseline core tests
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/unit/test_memory_stores.py tests/unit/test_tool_coordinator.py tests/unit/test_inference_gateway.py tests/integration/test_orchestrator_flow.py -q

# 2) Queue contract check
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/integration/test_inference_queue_mode.py -q

# 2a) Direct WSL Julia smoke (WslExecutorTool)
c:/ai/LIARA/.venv/Scripts/python.exe scripts/julia_sys_smoke_test.py

# 2b) Orchestrator JuliaBridge compute smoke
c:/ai/LIARA/.venv/Scripts/python.exe scripts/compute_run_api_smoke_test.py --with-server

# 3) Seeded law regression capture
c:/ai/LIARA/.venv/Scripts/python.exe scripts/liara_law_regression_runner.py --seed c:/ai/LIARA/tests/fixtures/liara_law_regression_seed_v1.json --base-url http://127.0.0.1:8010

# 3a) Supported assertion subset
c:/ai/LIARA/.venv/Scripts/python.exe scripts/liara_law_regression_runner.py --seed c:/ai/LIARA/tests/fixtures/liara_law_regression_seed_v1.json --base-url http://127.0.0.1:8010 --supported-only

# 4) Live stream regression (script-based)
c:/ai/LIARA/.venv/Scripts/python.exe scripts/live_stream_regression_check.py

# 5) Embedding worker live check (if prerequisites are set)
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/integration/test_embedding_worker_live.py -q
```
