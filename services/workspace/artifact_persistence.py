"""Workspace artifact persistence for LIARA self-validation and governance.

LIARA uses the workspace directory to collect all self-generated artifacts:
- Validation reports from the ai-validator
- Governance proposals and decisions
- Memory staging/dreaming consolidations
- Chat-generated code and outputs

Workspace structure:
  workspace/
  ├── .liara_artifacts/
  │   ├── validation-reports/      (dated validation runs)
  │   ├── governance-decisions/    (proposals & approvals)
  │   ├── memory-consolidations/   (staging/dreaming outputs)
  │   └── chat-outputs/            (code, configs, etc.)
  ├── temp/                        (ephemeral test data)
  └── [user-created files]
"""

import hashlib
import os
import json
from pathlib import Path, PurePosixPath
from datetime import UTC, datetime
from typing import Any, Optional

from .artifact_store import build_artifact_store, safe_filename_token

# Default workspace path is in WSL (test/simulation layer)
_WORKSPACE_ROOT = Path(os.getenv("LIARA_WORKSPACE_PATH", "/home/liara/workspace"))
_ARTIFACTS_DIR = _WORKSPACE_ROOT / ".liara_artifacts"

# Subdirectories for organized artifact storage
_VALIDATION_REPORTS_DIR = _ARTIFACTS_DIR / "validation-reports"
_GOVERNANCE_DIR = _ARTIFACTS_DIR / "governance-decisions"
_MEMORY_DIR = _ARTIFACTS_DIR / "memory-consolidations"
_CHAT_OUTPUTS_DIR = _ARTIFACTS_DIR / "chat-outputs"


def _utc_timestamps() -> tuple[str, str]:
    """Return an aware ISO UTC timestamp and a filesystem-safe UTC stamp."""
    now = datetime.now(UTC)
    return now.isoformat(), now.strftime("%Y%m%dT%H%M%SZ")


def _store():
    return build_artifact_store(local_workspace_root=_WORKSPACE_ROOT)


def _traceability(
    *,
    request_id: str | None,
    run_id: str | None,
    session_id: str | None,
    source: str | None,
    fallback_id: str,
) -> dict[str, str | None]:
    normalized_run_id = str(run_id or request_id or fallback_id)
    normalized_request_id = str(request_id or normalized_run_id)
    return {
        "request_id": normalized_request_id,
        "run_id": normalized_run_id,
        "session_id": session_id,
        "source": source or "workspace_artifact_persistence",
    }


def persist_validation_report(
    job_id: str,
    scope: str,
    findings: list[Any],
    exit_code: int,
    execution_mode: str,
    session_id: Optional[str] = None,
    request_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> Path | PurePosixPath:
    """
    Persist a validation report to the workspace.
    
    Args:
        job_id: Unique validator job ID
        scope: Validation scope (quick, validate, python, security, all)
        findings: List of validation findings (dict or ValidatorFinding)
        exit_code: Exit code from validator
        execution_mode: mock or worker
        session_id: Optional session context
    
    Returns:
        Path to the persisted report file
    """
    from services.validator.parser import parse_validator_findings

    timestamp, filename_timestamp = _utc_timestamps()
    safe_job = safe_filename_token(job_id, fallback="job", limit=16)
    safe_scope = safe_filename_token(scope, fallback="unknown", limit=32)
    filename = f"validation-{safe_job[:8]}-{safe_scope}-{filename_timestamp}.json"
    traceability = _traceability(
        request_id=request_id,
        run_id=run_id,
        session_id=session_id,
        source=source,
        fallback_id=job_id,
    )
    
    normalized_findings = [
        f.model_dump(mode="json") if hasattr(f, "model_dump") else f
        for f in parse_validator_findings(findings)
    ]

    report = {
        "job_id": job_id,
        "timestamp": timestamp,
        "scope": scope,
        "execution_mode": execution_mode,
        "exit_code": exit_code,
        "findings_count": len(normalized_findings),
        "findings": normalized_findings,
        "session_id": session_id,
        "artifact_type": "validation",
        "traceability": traceability,
    }

    return _store().write_json(
        artifact_dir="validation-reports",
        filename=filename,
        payload=report,
        **traceability,
    )


def persist_governance_decision(
    governance_id: str,
    command: str,
    risk_tokens: list[str],
    decision_approved: bool,
    approver: str,
    reason: str,
    session_id: Optional[str] = None,
    request_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> Path | PurePosixPath:
    """
    Persist a governance decision to the workspace.
    
    Args:
        governance_id: Unique governance proposal ID
        command: The sys command that was governed
        risk_tokens: Detected risk tokens (rm, del, curl, etc.)
        decision_approved: True if approved, False if rejected
        approver: Who approved (human, system)
        reason: Justification for decision
        session_id: Optional session context
    
    Returns:
        Path to the persisted decision file
    """
    timestamp, filename_timestamp = _utc_timestamps()
    decision_str = "approved" if decision_approved else "rejected"
    safe_governance_id = safe_filename_token(governance_id, fallback="proposal", limit=16)
    filename = f"governance-{safe_governance_id[:8]}-{decision_str}-{filename_timestamp}.json"
    traceability = _traceability(
        request_id=request_id,
        run_id=run_id,
        session_id=session_id,
        source=source,
        fallback_id=governance_id,
    )
    
    decision = {
        "governance_id": governance_id,
        "timestamp": timestamp,
        "command": command,
        "risk_tokens": risk_tokens,
        "approved": decision_approved,
        "approver": approver,
        "reason": reason,
        "session_id": session_id,
        "artifact_type": "governance",
        "traceability": traceability,
    }

    return _store().write_json(
        artifact_dir="governance-decisions",
        filename=filename,
        payload=decision,
        **traceability,
    )


def persist_memory_consolidation(
    dreaming_run_id: str,
    proposals: list[dict[str, Any]],
    verified_facts: list[dict[str, Any]],
    session_id: Optional[str] = None,
    request_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> Path | PurePosixPath:
    """
    Persist a memory consolidation/dreaming run to the workspace.
    
    Args:
        dreaming_run_id: Unique dreaming run ID
        proposals: Proposals created during consolidation
        verified_facts: Facts that were verified
        session_id: Optional session context
    
    Returns:
        Path to the persisted consolidation file
    """
    timestamp, filename_timestamp = _utc_timestamps()
    safe_run_id = safe_filename_token(dreaming_run_id, fallback="dream", limit=16)
    filename = f"consolidation-{safe_run_id[:8]}-{filename_timestamp}.json"
    traceability = _traceability(
        request_id=request_id,
        run_id=run_id or dreaming_run_id,
        session_id=session_id,
        source=source,
        fallback_id=dreaming_run_id,
    )
    
    consolidation = {
        "dreaming_run_id": dreaming_run_id,
        "timestamp": timestamp,
        "proposals_count": len(proposals),
        "proposals": proposals,
        "verified_facts_count": len(verified_facts),
        "verified_facts": verified_facts,
        "session_id": session_id,
        "artifact_type": "memory",
        "traceability": traceability,
    }

    return _store().write_json(
        artifact_dir="memory-consolidations",
        filename=filename,
        payload=consolidation,
        **traceability,
    )


def persist_chat_output(
    output_type: str,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
    session_id: Optional[str] = None,
    request_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> Path | PurePosixPath:
    """
    Persist a chat-generated output to the workspace.
    
    Args:
        output_type: Type of output (code, config, report, etc.)
        content: The actual content/code
        metadata: Optional metadata (tags, context, etc.)
        session_id: Optional session context
    
    Returns:
        Path to the persisted output file
    """
    timestamp, filename_timestamp = _utc_timestamps()
    safe_output_type = safe_filename_token(output_type, fallback="output", limit=32)
    filename = f"output-{safe_output_type}-{filename_timestamp}.json"
    traceability = _traceability(
        request_id=request_id,
        run_id=run_id,
        session_id=session_id,
        source=source,
        fallback_id=f"chat-{filename_timestamp}",
    )
    payload = {
        "timestamp": timestamp,
        "artifact_type": "chat",
        "output_type": output_type,
        "content": content,
        "metadata": metadata or {},
        "session_id": session_id,
        "traceability": traceability,
    }
    return _store().write_json(
        artifact_dir="chat-outputs",
        filename=filename,
        payload=payload,
        **traceability,
    )


def list_workspace_artifacts(
    artifact_type: Optional[str] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    List artifacts in the workspace.
    
    Args:
        artifact_type: Filter by type (validation, governance, memory, chat) or None for all
        limit: Maximum number of artifacts to return
    
    Returns:
        List of artifact info dicts {path, type, timestamp, session_id}
    """
    artifacts = []
    
    store = _store()
    type_dirs = {
        "validation": store.read_directory("validation-reports"),
        "governance": store.read_directory("governance-decisions"),
        "memory": store.read_directory("memory-consolidations"),
        "chat": store.read_directory("chat-outputs"),
    }
    
    dirs_to_scan = (
        [type_dirs[artifact_type]] if artifact_type and artifact_type in type_dirs
        else list(type_dirs.values())
    )
    
    for directory in dirs_to_scan:
        for filepath in sorted(directory.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                raw = filepath.read_bytes()
                artifacts.append({
                    "path": str(
                        store.canonical_artifacts_root / directory.name / filepath.name
                        if store.mode == "wsl"
                        else filepath
                    ),
                    "type": directory.name.replace("-", "_").rstrip("s"),
                    "timestamp": data.get("timestamp"),
                    "session_id": data.get("session_id"),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "traceability": data.get("traceability") or {},
                    "summary": {
                        k: v for k, v in data.items()
                        if k in ["job_id", "governance_id", "dreaming_run_id", "command", "findings_count"]
                    },
                })
            except (json.JSONDecodeError, OSError):
                pass
    
    return sorted(artifacts, key=lambda x: x.get("timestamp", ""), reverse=True)[:limit]


def get_workspace_status() -> dict[str, Any]:
    """
    Get overall workspace status (count of artifacts by type, disk usage, etc.).
    
    Returns:
        Status dict with artifact counts and metadata
    """
    store = _store()
    status = {
        "store_mode": store.mode,
        "workspace_root": str(store.canonical_root if store.mode == "wsl" else store.local_root),
        "artifacts_dir": str(store.canonical_artifacts_root if store.mode == "wsl" else store.local_artifacts_root),
        "local_access_root": str(store.local_root),
        "exists": store.local_root.exists(),
        "artifact_counts": {},
    }
    
    for name, directory in [
        ("validation", store.read_directory("validation-reports")),
        ("governance", store.read_directory("governance-decisions")),
        ("memory", store.read_directory("memory-consolidations")),
        ("chat", store.read_directory("chat-outputs")),
    ]:
        count = len(list(directory.glob("*.json")))
        status["artifact_counts"][name] = count
    
    return status


if __name__ == "__main__":
    # Example usage
    print("LIARA Workspace Artifact Persistence")
    print("=" * 60)
    
    status = get_workspace_status()
    print(f"Workspace: {status['workspace_root']}")
    print(f"Artifacts: {status['artifact_counts']}")
    
    # List recent artifacts
    print("\nRecent artifacts:")
    artifacts = list_workspace_artifacts(limit=5)
    for artifact in artifacts:
        print(f"  - {artifact['type']}: {artifact['path']}")
