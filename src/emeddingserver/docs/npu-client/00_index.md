# Liara Documentation Index

This is the entry point for all project documentation.

## Structure

- [01_architektur/README.md](01_architektur/README.md): Why the system is built this way.
- [01_architektur/system-overview.md](01_architektur/system-overview.md): Core goals, roles, and topology.
- [01_architektur/scheduler-consensus.md](01_architektur/scheduler-consensus.md): Scheduling and consensus behavior.
- [02_services/README.md](02_services/README.md): Service-by-service documentation.
- [02_services/npu-helper-service.md](02_services/npu-helper-service.md): NPU helper profile and OpenVINO runtime requirements.
- [02_services/plugin-host-service.md](02_services/plugin-host-service.md): DLL plugin host contracts.
- [03_apis/README.md](03_apis/README.md): API endpoints, contracts, and examples.
- [04_runbooks/README.md](04_runbooks/README.md): Operational playbooks and incident handling.
- [04_runbooks/openvino-worker-readiness.md](04_runbooks/openvino-worker-readiness.md): Worker startup readiness checks.
- [05_decisions/README.md](05_decisions/README.md): Architecture Decision Records (ADRs).
- [05_decisions/ADR-0001-device-selection-and-runtime-gate.md](05_decisions/ADR-0001-device-selection-and-runtime-gate.md): Explicit device policy and runtime startup gate.
- [06_build-history/README.md](06_build-history/README.md): Build and change timeline.
- [07_tests/README.md](07_tests/README.md): Smoke, regression, and benchmark test docs.
- [08_security/README.md](08_security/README.md): Auth, roles, policies, and guardrails.
- [08_security/security-principles.md](08_security/security-principles.md): Component trust boundaries.
- [09_reference/README.md](09_reference/README.md): Commands, ports, config references.
- [09_reference/heartbeat-protocol.md](09_reference/heartbeat-protocol.md): 12-byte heartbeat packet layout.

## Current Core Spec

- [desc.md](desc.md): Existing full-system specification (legacy consolidated source).

## Current Implementation Snapshot

- Build system: CMake + Ninja via `build.ps1` (MSVC environment is imported automatically).
- OpenVINO: optional at configure time; `openvino_probe` is only built when OpenVINO is found.
- Probe features: explicit `--device=npu|cpu`, optional `--infer-smoke`, optional `--smoke-seq-len` for dynamic-shape smoke runs.
- Helper contract: two profiles must be available and warm in memory (`Instruct`, `Coder`).
- Routing: `quick_extract` -> `Instruct`, `code_*` -> `Coder`.
- Runtime metrics: `warm_age_ms` and `reload_count` are exposed in helper/scheduler startup output.
- Tests: `heartbeat_demo` and `HelperContractTests` are part of the verified baseline.

## Documentation Rules

- Keep files short and focused.
- Prefer one topic per file.
- Link between related docs.
- Record decisions in ADR format under `05_decisions`.
