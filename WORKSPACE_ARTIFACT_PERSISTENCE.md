# LIARA Workspace Artifact Persistence

## Overview

This document describes LIARA's workspace artifact persistence system, which enables LIARA to store its self-generated work products (validation reports, governance decisions, memory consolidations, and chat outputs) in a persistent workspace directory.

## Architecture

### Workspace Structure (WSL Debian)

```
/home/liara/workspace/
├── .liara_artifacts/              # All LIARA-generated artifacts
│   ├── validation-reports/        # Validator job results
│   ├── governance-decisions/      # Governance proposal decisions
│   ├── memory-consolidations/     # Dreaming run outputs
│   └── chat-outputs/              # Generated code/configs
├── temp/                          # Ephemeral test files
└── [user data]
```

**Environment Variable**: `LIARA_WORKSPACE_PATH` (defaults to `/home/liara/workspace` in WSL)

### Artifact Persistence Module

**File**: `services/workspace/artifact_persistence.py`

Core functions for storing different artifact types:

```python
# Validation reports from validator jobs
persist_validation_report(
    job_id: str,
    scope: str,
    findings: list,
    exit_code: int,
    execution_mode: str,  # "mock" or "worker"
    session_id: str | None
) -> Path

# Governance decisions (approved/rejected proposals)
persist_governance_decision(
    governance_id: str,
    command: str,
    risk_tokens: list[str],
    decision_approved: bool,
    approver: str,
    reason: str,
    session_id: str | None
) -> Path

# Memory consolidation/dreaming results
persist_memory_consolidation(
    dreaming_run_id: str,
    proposals: list[dict],
    verified_facts: list[dict],
    session_id: str | None
) -> Path

# Chat-generated code/configs
persist_chat_output(
    output_type: str,  # "generated_code", "config", etc.
    content: str,
    metadata: dict | None,
    session_id: str | None
) -> Path

# Query artifacts
list_workspace_artifacts(
    artifact_type: str | None = None,  # "validation", "governance", "consolidation", "chat"
    limit: int = 10
) -> list[dict]

# Get workspace status
get_workspace_status() -> dict[str, Any]
```

## Integration Points

### 1. Validator Service ✅

**File**: `services/memory/store.py`

**Function**: `_execute_validator_job()` (line 146)

**Integration**:
- Added parameter: `session_id: str | None = None`
- After validator completes (mock or docker-compose):
  - Calls `persist_validation_report()` with job results
  - Non-blocking (wrapped in try-except)
  - Reports persisted to `.liara_artifacts/validation-reports/`

**Example Report**:
```json
{
  "job_id": "validator-abc123",
  "timestamp": "2024-07-13T15:30:45.123456",
  "scope": "quick",
  "execution_mode": "worker",
  "exit_code": 0,
  "findings_count": 2,
  "findings": [
    {"severity": "warning", "message": "..."}
  ],
  "session_id": "session-xyz789"
}
```

### 2. Governance Service ✅

**File**: `services/api/app.py`

**Endpoint**: POST `/tools/sys/governance/decisions` (line ~2100)

**Integration**:
- After `_append_sys_governance_event()`:
  - Calls `persist_governance_decision()` with proposal decision data
  - Non-blocking (wrapped in try-except)
  - Decisions persisted to `.liara_artifacts/governance-decisions/`

**Example Decision**:
```json
{
  "governance_id": "gov-def456",
  "timestamp": "2024-07-13T15:32:10.234567",
  "command": "memory-consolidate",
  "risk_tokens": ["memory:write", "session:modify"],
  "decision_approved": true,
  "approver": "orchestrator_service",
  "reason": "Automated governance approval",
  "session_id": "session-xyz789"
}
```

### 3. Memory Consolidation Service ✅

**File**: `services/memory/store.py`

**Method**: `BackedMemoryServiceStore.dreaming_run()` (line ~1950)

**Integration**:
- After `_audit_memory_executed()` in dreaming_run():
  - Calls `persist_memory_consolidation()` with proposals
  - Only persists if not dry_run and proposals exist
  - Non-blocking (wrapped in try-except)
  - Consolidations persisted to `.liara_artifacts/memory-consolidations/`

**Example Consolidation**:
```json
{
  "dreaming_run_id": "run-ghi789",
  "timestamp": "2024-07-13T15:35:20.345678",
  "proposals": [
    {
      "proposal_id": "prop-001",
      "session_id": "session-xyz789",
      "proposed_value": "fact content...",
      "proposed_status": "candidate"
    }
  ],
  "verified_facts": [],
  "session_id": "session-xyz789"
}
```

### 4. Chat Output (Optional)

**File**: `services/api/app.py` (POST `/chat` endpoint)

**Status**: ⏳ Can be added for generated code/configs

**Pattern**: Similar to consolidation - non-blocking call after response generation

## Artifact Format

All artifacts use JSON format with:

- **Timestamp**: UTC ISO format (required for all artifacts)
- **ID Fields**: Unique identifier + session_id for traceability
- **Metadata**: Context about origin, execution mode, decision maker, etc.
- **Content/Results**: Job-specific data (findings, proposals, code, etc.)

**Filename Pattern**: `<type>-<id_prefix>-<timestamp>.json`

Examples:
- `validation-abc12345-quick-20240713153045.json`
- `governance-def45678-20240713153210.json`
- `consolidation-ghi78901-20240713153520.json`

## Usage

### Viewing Artifacts

**CLI Utility**: `cli_workspace_artifacts.py`

```bash
# Show all artifacts
python cli_workspace_artifacts.py

# Show only validation reports
python cli_workspace_artifacts.py --type validation

# Use custom workspace
python cli_workspace_artifacts.py --workspace /path/to/workspace

# JSON output
python cli_workspace_artifacts.py --format json --limit 5
```

**Output Example**:
```
================================================================================
  LIARA Workspace Status
================================================================================
  📁 Workspace: /home/liara/workspace
  📦 Artifacts Dir: /home/liara/workspace/.liara_artifacts

  📈 Artifact Counts:
     • Validation Reports: 15
     • Governance Decisions: 8
     • Memory Consolidations: 3
     • Chat Outputs: 2

================================================================================
Recent Artifacts
================================================================================

📊 5 recent validation artifacts:
  📄 validation-a1b2c3-quick-20240713153045.json
     📅 2024-07-13 15:30:45 UTC
     🎯 Scope: quick
     ✅ Exit Code: 0
     🔍 Findings: 2
     🔗 Session: session-xyz789

...
```

### Programmatic Access

```python
from services.workspace import (
    list_workspace_artifacts,
    get_workspace_status,
)

# Get recent validation reports
reports = list_workspace_artifacts(artifact_type="validation", limit=10)
for report in reports:
    print(f"Validator {report['job_id']}: {report['exit_code']}")

# Get workspace status
status = get_workspace_status()
print(f"Total artifacts: {status['total_count']}")
print(f"Validation reports: {status['validation_count']}")
```

## Testing

### Unit Tests

**File**: `test_workspace_integration.py`

Tests artifact persistence module in isolation:
```bash
python test_workspace_integration.py
```

**Coverage**:
- Module imports
- Artifact persistence (all 4 types)
- Artifact listing/querying
- Workspace status

### End-to-End Tests

**File**: `test_workspace_e2e.py`

Tests complete integration: Validator → Governance → Memory:
```bash
python test_workspace_e2e.py
```

**Coverage**:
- Mock validator with persistence
- Governance decision storage
- Memory consolidation storage
- Chat output storage
- Artifact querying
- Workspace status

## Environment Variables

```bash
# Workspace root directory
LIARA_WORKSPACE_PATH=/home/liara/workspace

# Validator execution mode
LIARA_VALIDATOR_EXECUTION_MODE=worker  # or "mock" for testing

# Validator timeout
LIARA_VALIDATOR_TIMEOUT_SECONDS=1800
```

## Design Principles

1. **Non-Blocking**: All persistence calls wrapped in try-except, never blocks main execution
2. **Traceable**: Every artifact includes session_id, request_id, run_id for full traceability
3. **Immutable**: Artifacts are append-only, never modified after creation
4. **Audit-Ready**: Timestamped, with decision makers and reasons recorded
5. **Self-Describing**: Each artifact includes sufficient metadata to understand its origin

## Deployment Notes

- Workspace directory must be **writable** by LIARA processes
- In WSL Debian: `/home/liara/workspace/.liara_artifacts/` structure auto-created
- No external dependencies beyond Python pathlib/json
- Artifact persistence fails gracefully (non-blocking)

## Future Enhancements

1. **Artifact Rotation**: Auto-cleanup old artifacts based on TTL
2. **Workspace Browser UI**: Textual CLI tab showing recent artifacts
3. **Archive Format**: Compress old artifact bundles
4. **Artifact Hooks**: Custom callbacks when artifacts created
5. **Remote Sync**: Backup artifacts to external storage

## Key Files

| File | Purpose |
|------|---------|
| `services/workspace/artifact_persistence.py` | Core persistence API |
| `services/workspace/__init__.py` | Package exports |
| `services/memory/store.py` | Validator & memory integration |
| `services/api/app.py` | Governance integration |
| `cli_workspace_artifacts.py` | CLI utility for viewing artifacts |
| `test_workspace_integration.py` | Unit tests |
| `test_workspace_e2e.py` | End-to-end tests |

## Status

✅ **Production Ready**

- Core module: COMPLETE
- Validator integration: COMPLETE
- Governance integration: COMPLETE
- Memory consolidation integration: COMPLETE
- CLI utility: COMPLETE
- Test coverage: COMPLETE

All integration points are operational and non-blocking.
