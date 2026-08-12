# Build History

Track what changed, when, and why.

## Latest Snapshot (2026-05-01)

- Date: 2026-05-01
- Change:
  - Heartbeat protocol migrated to `liara-inference/common`.
  - `openvino_probe` extended with `--infer-smoke` and `--smoke-seq-len`.
  - Helper/Scheduler contracts enforce dual profile readiness (`Instruct`, `Coder`).
  - Warm-hold model state and metrics added (`warm_age_ms`, `reload_count`).
  - Contract and regression binaries verified (`heartbeat_demo`, `HelperContractTests`).
- Reason: Stabilize first end-to-end local runtime baseline for NPU helper operation.
- Impact: Reproducible local build/test flow and explicit runtime readiness checks.
- Validation: Local build and tests green; NPU probe PASS for compile + infer smoke on valid model path.

## Suggested Files

- `changelog.md`
- `release-notes-YYYY-MM.md`

## Entry Template

- Date:
- Change:
- Reason:
- Impact:
- Validation:
