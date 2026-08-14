"""FastAPI router for SYS governance and audit endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from services.api.deps import get_orchestrator, get_governance_service, get_verified_principal
from services.api.security import Principal
from services.api.exceptions import (
    AuditPersistenceError,
    ForbiddenPrincipalError,
    GovernanceConflictError,
    GovernanceError,
    GovernanceNotFoundError,
    PolicyViolationError,
    UnauthorizedPrincipalError,
)
from services.api.models import (
    SysToolProposalActionRequest,
    SysToolProposalDecisionRequest,
    SysToolProposalRequest,
    ToolInvokeRequest,
)
from services.contracts import ToolExecutionRequest, ToolExecutionResult
from services.tools.builtin.sys_audit import (
    count_entries as count_sys_audit_entries,
    filter_entries as filter_sys_audit_entries,
    find_suspicious_entries,
    log_judge_pre_action,
    load_entries as load_sys_audit_entries,
    summarize_entries as summarize_sys_audit_entries,
)
from services.tools.coordinator import ToolCoordinator
from services.tools.governance import (
    append_sys_governance_event,
    load_sys_governance_proposals,
    persist_sys_governance_proposals,
    sys_governance_events_path as _default_sys_events_path,
    sys_governance_invocation_digest,
    sys_governance_mode,
    sys_governance_store_path as _default_sys_store_path,
)
from services.workspace import persist_governance_decision


router = APIRouter(tags=["governance"])


def _sys_governance_store_path(app_state: Any = None) -> Path:
    if app_state and getattr(app_state, "sys_tool_proposals_path", None):
        return Path(app_state.sys_tool_proposals_path)
    return _default_sys_store_path()


def _sys_governance_events_path(app_state: Any = None) -> Path:
    if app_state and getattr(app_state, "sys_tool_events_path", None):
        return Path(app_state.sys_tool_events_path)
    return _default_sys_events_path()


def _sync_sys_governance_store(app_state: Any) -> dict[str, Any]:
    path = _sys_governance_store_path(app_state)
    proposals = load_sys_governance_proposals(path)
    if app_state is not None:
        app_state.sys_tool_proposals = proposals
    return proposals


def _persist_sys_governance_proposals(path: Path, proposals: dict[str, Any]) -> None:
    persist_sys_governance_proposals(proposals, path)


def _append_sys_governance_event(path: Path, event: dict[str, Any]) -> None:
    append_sys_governance_event(event, path)


def _load_sys_governance_events(path: Path, *, proposal_id: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-5000:]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if proposal_id and str(event.get("proposal_id") or "") != proposal_id:
            continue
        events.append(event)
    return events


def _evaluate_sys_policy(command: str) -> dict[str, Any]:
    normalized = (command or "").strip().lower()
    blocked_tokens = ("rm", "del", "remove-item", "shutdown", "reboot", "format")
    network_tokens = ("curl", "invoke-webrequest", "wget")
    mutation_tokens = ("tee", "mkdir", "touch", "cp", "mv", "venv-pip")

    reasons: list[str] = []
    allowed = True
    risk_level = "low"

    if any(token in normalized for token in blocked_tokens):
        allowed = False
        risk_level = "high"
        reasons.append("blocked_command_family")
    elif any(token in normalized for token in mutation_tokens):
        risk_level = "high"
        reasons.append("mutation_requires_review")
    elif any(token in normalized for token in network_tokens):
        risk_level = "medium"
        reasons.append("network_command_requires_review")

    return {
        "allowed": allowed,
        "reasons": reasons,
        "command_name": normalized,
        "risk_level": risk_level,
    }


def _sys_governance_rollback_path(proposals_path: Path, proposal_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", proposal_id).strip("._")
    if not safe_id:
        raise ValueError("rollback proposal id is invalid")
    return proposals_path.parent / "sys_governance_rollback" / f"{safe_id}.json"


def _persist_sys_governance_rollback_snapshot(
    proposals_path: Path,
    proposal_id: str,
    *,
    target_path: str,
    content: str,
) -> dict[str, Any]:
    payload_bytes = content.encode("utf-8")
    max_bytes = max(1, int(os.getenv("LIARA_SYS_ROLLBACK_MAX_BYTES", str(64 * 1024))))
    if len(payload_bytes) > max_bytes:
        raise ValueError(f"rollback snapshot exceeds {max_bytes} bytes")
    artifact_path = _sys_governance_rollback_path(proposals_path, proposal_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "target_path": target_path,
        "content": content,
        "size_bytes": len(payload_bytes),
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    temporary = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(artifact_path)
    return {
        "schema_version": 1,
        "artifact_path": str(artifact_path),
        "target_path": target_path,
        "size_bytes": payload["size_bytes"],
        "sha256": payload["sha256"],
        "captured_at": payload["captured_at"],
    }


def _load_sys_governance_rollback_snapshot(
    proposals_path: Path,
    proposal_id: str,
    reference: dict[str, Any],
) -> dict[str, Any]:
    artifact_path = Path(str(reference.get("artifact_path") or ""))
    expected_path = _sys_governance_rollback_path(proposals_path, proposal_id)
    if artifact_path.resolve() != expected_path.resolve():
        raise ValueError("rollback snapshot artifact path mismatch")
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported rollback snapshot schema")
    content = str(payload.get("content") or "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if digest != str(reference.get("sha256") or "") or digest != str(payload.get("sha256") or ""):
        raise ValueError("rollback snapshot digest mismatch")
    if str(payload.get("target_path") or "") != str(reference.get("target_path") or ""):
        raise ValueError("rollback snapshot target mismatch")
    return payload


def _reversible_sys_target(parameters: dict[str, Any]) -> tuple[str | None, str]:
    command = str(parameters.get("command") or "").strip().lower()
    if command != "tee":
        return None, "only tee overwrite of an existing workspace file is reversible"
    if str(parameters.get("write_mode") or "overwrite").strip().lower() == "append":
        return None, "append mutations are not reversible"
    target_path = str(parameters.get("target_path") or "").strip()
    workspace_root = os.getenv("LIARA_AGENT_WORKSPACE_ROOT", "/home/liara/workspace").rstrip("/")
    try:
        target = PurePosixPath(target_path)
        root = PurePosixPath(workspace_root)
        target.relative_to(root)
    except (TypeError, ValueError):
        return None, "target is outside the managed workspace root"
    if target == root:
        return None, "workspace root is not a reversible file target"
    if str(parameters.get("storage_scope") or "") != "wsl_workspace":
        return None, "rollback requires storage_scope=wsl_workspace"
    return str(target), ""


@router.post("/tools/sys/governance/proposals")
async def create_sys_governance_proposal(
    request_body: SysToolProposalRequest,
    request: Request,
    response: Response,
    service: Any = Depends(get_governance_service),
    principal: Principal = Depends(get_verified_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        proposal = await service.create_proposal(
            command=request_body.command,
            parameters=dict(request_body.parameters or {}),
            principal=principal,
            capability=request_body.capability,
            rationale=request_body.rationale,
            max_invocations=request_body.max_invocations,
            session_id=request_body.session_id,
            request_id=request_body.request_id,
            run_id=request_body.run_id,
            source=request_body.source or "api",
            context=request_body.context or "api.tools.sys.governance.proposal",
        )
        app_state = request.app.state
        policy = proposal.get("policy_check") if isinstance(proposal.get("policy_check"), dict) else {}
        _append_sys_governance_event(
            _sys_governance_events_path(app_state),
            {
                "event_type": "proposal_created",
                "proposal_id": proposal["proposal_id"],
                "tool_name": "sys",
                "command": request_body.command,
                "policy_allowed": bool(policy.get("allowed")),
                "policy_risk_level": str(policy.get("risk_level") or "unknown"),
                "traceability": {
                    "request_id": request_body.request_id or proposal["proposal_id"],
                    "run_id": request_body.run_id or request_body.request_id or proposal["proposal_id"],
                    "session_id": request_body.session_id,
                    "source": request_body.source or "api",
                    "context": request_body.context or "api.tools.sys.governance.proposal",
                },
            },
        )
        return {
            "status": "success",
            "item": proposal,
        }
    except UnauthorizedPrincipalError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ForbiddenPrincipalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (GovernanceConflictError, PolicyViolationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GovernanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuditPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tools/sys/governance/proposals")
async def list_sys_governance_proposals(
    request: Request,
    response: Response,
    decision: str = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    app_state = request.app.state
    all_items = list(_sync_sys_governance_store(app_state).values())
    decision_counts = {"pending": 0, "approved": 0, "rejected": 0}
    invocation_states: dict[str, int] = {}
    policy_blocked = 0
    consumed = 0
    for item in all_items:
        item_decision = str(item.get("decision") or "pending")
        if item_decision in decision_counts:
            decision_counts[item_decision] += 1
        invocation = item.get("invocation") if isinstance(item.get("invocation"), dict) else {}
        invocation_state = str(invocation.get("state") or "not_invoked")
        invocation_states[invocation_state] = invocation_states.get(invocation_state, 0) + 1
        policy_blocked += int(not bool((item.get("policy_check") or {}).get("allowed", True)))
        consumed += int(int(invocation.get("attempt_count") or 0) >= int(item.get("max_invocations") or 1))
    items = all_items
    if decision != "all":
        items = [item for item in items if str(item.get("decision") or "") == decision]
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    sliced = []
    for raw_item in items[:limit]:
        item = dict(raw_item)
        item["audit_reference"] = {
            "endpoint": f"/tools/sys/governance/events?proposal_id={item.get('proposal_id')}",
            "proposal_id": item.get("proposal_id"),
        }
        sliced.append(item)
    return {
        "status": "success",
        "count": len(sliced),
        "total": len(items),
        "items": sliced,
        "summary": {
            "decisions": decision_counts,
            "invocation_states": invocation_states,
            "policy_blocked": policy_blocked,
            "consumed": consumed,
            "enforcement_mode": sys_governance_mode(),
        },
        "filters": {"decision": decision, "limit": limit},
    }


@router.get("/tools/sys/governance/events")
async def list_sys_governance_events(
    request: Request,
    response: Response,
    proposal_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """List governance events from the legacy JSONL file.

    Stays file-backed for now: invoke_tool()'s own invocation lifecycle events
    (Phase 4 territory) and the compensating rollback proposal (see
    act_on_sys_governance_proposal's docstring) are still JSONL-only, so
    switching this read endpoint to the Postgres-backed governance_events table
    before those producers migrate would silently hide their events. Apply/
    rollback below dual-write into both stores for this reason.
    """
    response.headers["Cache-Control"] = "no-store"
    app_state = request.app.state
    events = _load_sys_governance_events(_sys_governance_events_path(app_state), proposal_id=proposal_id)
    events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return {
        "status": "success",
        "count": min(len(events), limit),
        "total": len(events),
        "items": events[:limit],
        "filters": {"proposal_id": proposal_id, "limit": limit},
    }


@router.post("/tools/sys/governance/decisions")
async def decide_sys_governance_proposal(
    request_body: SysToolProposalDecisionRequest,
    request: Request,
    response: Response,
    service: Any = Depends(get_governance_service),
    principal: Principal = Depends(get_verified_principal),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        proposal = await service.decide_proposal(
            proposal_id=request_body.proposal_id,
            decision=request_body.decision,
            principal=principal,
            decision_reason=request_body.decision_reason,
            session_id=request_body.session_id,
            request_id=request_body.request_id,
            run_id=request_body.run_id,
            source=request_body.source or "api",
            context=request_body.context or "api.tools.sys.governance.decision",
        )
    except GovernanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (GovernanceConflictError, PolicyViolationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnauthorizedPrincipalError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ForbiddenPrincipalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AuditPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app_state = request.app.state
    now = datetime.now(UTC).isoformat()
    proposals = _sync_sys_governance_store(app_state)
    request_id = request_body.request_id or proposal["proposal_id"]
    run_id = request_body.run_id or request_id
    session_id = request_body.session_id
    source = request_body.source or "api"
    context = request_body.context or "api.tools.sys.governance.decision"

    _append_sys_governance_event(
        _sys_governance_events_path(app_state),
        {
            "event_type": "proposal_decided",
            "proposal_id": request_body.proposal_id,
            "tool_name": "sys",
            "decision": request_body.decision,
            "decided_by": principal.actor_id,
            "decision_reason": request_body.decision_reason,
            "command": proposal.get("command"),
            "traceability": {
                "request_id": request_id,
                "run_id": run_id,
                "session_id": session_id,
                "source": source,
                "context": context,
            },
        },
    )

    # Persist governance decision to workspace
    try:
        artifact_path = await asyncio.to_thread(
            persist_governance_decision,
            governance_id=request_body.proposal_id,
            command=str(proposal.get("command") or "unknown"),
            risk_tokens=list(proposal.get("risk_tokens") or []),
            decision_approved=(request_body.decision == "approved"),
            approver=request_body.decided_by,
            reason=request_body.decision_reason,
            session_id=session_id,
            request_id=request_id,
            run_id=run_id,
            source=source,
        )
        proposal["artifact_persistence"] = {
            "status": "verified",
            "path": str(artifact_path),
        }
    except Exception as exc:
        proposal["artifact_persistence"] = {
            "status": "failed",
            "error": str(exc),
        }

    handoff = proposal.get("handoff") if isinstance(proposal.get("handoff"), dict) else {}
    checkpoint = handoff.get("checkpoint") if isinstance(handoff.get("checkpoint"), dict) else {}
    resume_payload: dict[str, Any] | None = None
    if checkpoint:
        if request_body.decision == "rejected":
            handoff["state"] = "rejected"
            handoff["resume"] = {
                "status": "rejected",
                "decided_at": now,
                "reason": request_body.decision_reason,
            }
        else:
            orch = getattr(app_state, "orchestrator", None)
            workspace_agent = getattr(orch, "workspace_agent", None)
            if workspace_agent is None or not hasattr(workspace_agent, "resume_from_governance_proposal"):
                handoff["state"] = "resume_unavailable"
                handoff["resume"] = {
                    "status": "unavailable",
                    "error": "orchestrator has no resumable workspace agent",
                }
            else:
                handoff["state"] = "resuming"
                proposal["handoff"] = handoff
                proposal["updated_at"] = datetime.now(UTC).isoformat()
                _persist_sys_governance_proposals(_sys_governance_store_path(app_state), proposals)
                try:
                    from services.api.routers.tools import invoke_tool
                    invoke_parameters = dict(proposal.get("parameters") or {})
                    invoke_parameters["proposal_id"] = request_body.proposal_id
                    approved_execution = await invoke_tool(
                        "sys",
                        ToolInvokeRequest(parameters=invoke_parameters),
                        request,
                        Response(),
                    )
                    refreshed = _sync_sys_governance_store(app_state).get(request_body.proposal_id)
                    if refreshed is None:
                        raise RuntimeError("approved workspace proposal disappeared during invocation")
                    workspace_result = await workspace_agent.resume_from_governance_proposal(
                        refreshed,
                        approved_execution,
                    )
                    resume_payload = workspace_result.model_dump(mode="json")
                    persistence: dict[str, Any]
                    try:
                        persistence = await workspace_agent.persist_run_artifact(
                            workspace_result,
                            session_id=str(session_id or ""),
                            run_id=run_id,
                        )
                    except Exception as persist_error:
                        persistence = {"status": "failed", "error": str(persist_error)}
                    resume_payload["persistence"] = persistence
                    proposals = _sync_sys_governance_store(app_state)
                    proposal = proposals[request_body.proposal_id]
                    handoff = dict(proposal.get("handoff") or {})
                    handoff["state"] = (
                        "resume_completed" if workspace_result.status == "completed" else workspace_result.status
                    )
                    handoff["resume"] = {
                        "status": workspace_result.status,
                        "completed_at": datetime.now(UTC).isoformat(),
                        "step_count": len(workspace_result.steps),
                        "validator": dict(workspace_result.validator or {}),
                        "persistence": persistence,
                    }
                except Exception as exc:
                    proposals = _sync_sys_governance_store(app_state)
                    proposal = proposals[request_body.proposal_id]
                    handoff = dict(proposal.get("handoff") or {})
                    handoff["state"] = "resume_failed"
                    handoff["resume"] = {
                        "status": "failed",
                        "failed_at": datetime.now(UTC).isoformat(),
                        "error": str(exc),
                    }
                    resume_payload = dict(handoff["resume"])
                proposal["handoff"] = handoff
                proposal["updated_at"] = datetime.now(UTC).isoformat()
                _append_sys_governance_event(
                    _sys_governance_events_path(app_state),
                    {
                        "event_type": {
                            "resume_completed": "workspace_resume_completed",
                            "awaiting_decision": "workspace_resume_paused",
                        }.get(str(handoff.get("state") or ""), "workspace_resume_failed"),
                        "proposal_id": request_body.proposal_id,
                        "tool_name": "sys",
                        "resume_status": (handoff.get("resume") or {}).get("status"),
                        "traceability": {
                            "request_id": request_id,
                            "run_id": run_id,
                            "session_id": session_id,
                            "source": source,
                            "context": "api.tools.sys.governance.workspace_resume",
                        },
                    },
                )

        proposal["handoff"] = handoff
        proposal["updated_at"] = datetime.now(UTC).isoformat()

    proposals[request_body.proposal_id] = proposal
    _persist_sys_governance_proposals(_sys_governance_store_path(app_state), proposals)

    return {
        "status": "success",
        "item": proposal,
        "workspace_resume": resume_payload,
    }


@router.post("/tools/sys/governance/actions")
async def act_on_sys_governance_proposal(
    request_body: SysToolProposalActionRequest,
    request: Request,
    response: Response,
    service: Any = Depends(get_governance_service),
    principal: Principal = Depends(get_verified_principal),
) -> dict[str, Any]:
    """Apply or roll back an approved sys proposal.

    Claim/complete against PostgresGovernanceRepository (claim_apply/complete_apply,
    claim_rollback/complete_rollback) provide the single-use, cross-process-safe
    guarantee that app_state.sys_tool_governance_lock (a single-process
    asyncio.Lock) previously only approximated. acted_by/action_reason in the
    request body are wire-compatible hints only; principal.actor_id is the sole
    authoritative actor recorded against the claimed operation.

    The compensating rollback proposal (a new, separate, auto-approved proposal
    that performs the actual restore) intentionally still uses the legacy
    file-backed governance store -- that mechanism, and invoke_tool()'s own
    governance-authorization bookkeeping for it, are unchanged here; only the
    ORIGINAL proposal's apply/rollback transaction bookkeeping moves to Postgres.
    """
    response.headers["Cache-Control"] = "no-store"
    app_state = request.app.state
    try:
        proposal = await service.get_proposal(request_body.proposal_id)
    except GovernanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    traceability = dict(proposal.get("traceability") or {})
    request_id = request_body.request_id or str(traceability.get("request_id") or request_body.proposal_id)
    run_id = request_body.run_id or str(traceability.get("run_id") or request_id)
    session_id = request_body.session_id or traceability.get("session_id")
    source = request_body.source or str(traceability.get("source") or "api")
    context = request_body.context or f"api.tools.sys.governance.{request_body.action}"
    trace = {
        "request_id": request_id,
        "run_id": run_id,
        "session_id": session_id,
        "source": source,
        "context": context,
    }

    if request_body.action == "apply":
        idempotency_key = request_body.request_id or f"apply-{uuid4().hex[:12]}"
        try:
            claimed_proposal, operation = await service.claim_apply(
                request_body.proposal_id,
                principal,
                idempotency_key,
                action_reason=request_body.action_reason,
            )
        except GovernanceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except GovernanceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        operation_id = operation["operation_id"]
        _append_sys_governance_event(
            _sys_governance_events_path(app_state),
            {
                "event_type": "governance_apply_attempted",
                "proposal_id": request_body.proposal_id,
                "tool_name": "sys",
                "acted_by": principal.actor_id,
                "action_reason": request_body.action_reason,
                "traceability": trace,
            },
        )

        parameters = dict(proposal.get("parameters") or {})
        parameters.setdefault("command", str(proposal.get("command") or ""))
        target_path, unsupported_reason = _reversible_sys_target(parameters)
        rollback: dict[str, Any] = {
            "supported": False,
            "state": "unavailable",
            "reason": unsupported_reason,
        }
        tool_coordinator = ToolCoordinator()
        if target_path:
            preflight_parameters = {
                "command": "cat",
                "args": [target_path],
                "workdir": str(parameters.get("workdir") or os.getenv("LIARA_AGENT_WORKSPACE_ROOT", "/home/liara/workspace")),
                **trace,
                "source": "governance_apply_preflight",
                "context": "api.tools.sys.governance.rollback_capture",
            }
            try:
                preflight = await tool_coordinator.execute_tool(
                    ToolExecutionRequest(tool_name="sys", parameters=preflight_parameters)
                )
            except Exception as exc:
                preflight = ToolExecutionResult(
                    tool_name="sys",
                    status="error",
                    error=str(exc),
                    execution_ms=0.0,
                )
            if preflight.status == "success" and isinstance(preflight.output, str):
                try:
                    reference = _persist_sys_governance_rollback_snapshot(
                        _sys_governance_store_path(app_state),
                        request_body.proposal_id,
                        target_path=target_path,
                        content=preflight.output,
                    )
                    rollback = {
                        "supported": True,
                        "state": "captured",
                        "snapshot": reference,
                    }
                except (OSError, ValueError) as exc:
                    rollback["reason"] = str(exc)
            else:
                rollback["reason"] = "target did not exist as a readable regular file before apply"

        # Perform the mutation directly via ToolCoordinator rather than the HTTP
        # invoke_tool() endpoint: claim_apply above is already this call's
        # governance authorization, so invoke_tool()'s own (file-backed,
        # unrelated) proposal re-validation is neither needed nor reachable for
        # a Postgres-only proposal.
        try:
            execution = await tool_coordinator.execute_tool(
                ToolExecutionRequest(tool_name="sys", parameters=parameters, timeout_seconds=120)
            )
            if execution.status != "success":
                raise RuntimeError(execution.error or "approved SYS action failed")
        except Exception as exc:
            await service.complete_apply(
                operation_id,
                request_body.proposal_id,
                principal,
                success=False,
                details={"acted_by": principal.actor_id, "reason": request_body.action_reason, "error": str(exc)},
            )
            _append_sys_governance_event(
                _sys_governance_events_path(app_state),
                {
                    "event_type": "governance_apply_failed",
                    "proposal_id": request_body.proposal_id,
                    "tool_name": "sys",
                    "error": str(exc),
                    "traceability": trace,
                },
            )
            raise HTTPException(status_code=409, detail=f"Governance apply failed: {exc}") from exc

        now = datetime.now(UTC).isoformat()
        updated_proposal = await service.complete_apply(
            operation_id,
            request_body.proposal_id,
            principal,
            success=True,
            details={
                "acted_by": principal.actor_id,
                "reason": request_body.action_reason,
                "completed_at": now,
                "status": execution.status,
                "rollback": rollback,
            },
        )
        if execution.metadata is not None:
            execution.metadata["governance_proposal_id"] = request_body.proposal_id
        _append_sys_governance_event(
            _sys_governance_events_path(app_state),
            {
                "event_type": "governance_apply_completed",
                "proposal_id": request_body.proposal_id,
                "tool_name": "sys",
                "rollback_supported": bool(rollback.get("supported")),
                "traceability": trace,
            },
        )
        item = dict(updated_proposal)
        item["transaction"] = {
            "state": "applied",
            "apply": {
                "acted_by": principal.actor_id,
                "reason": request_body.action_reason,
                "completed_at": now,
                "status": execution.status,
            },
            "rollback": rollback,
        }
        return {
            "status": "success",
            "action": "apply",
            "item": item,
            "execution": execution.model_dump(mode="json"),
        }

    # action == "rollback"
    if str(proposal.get("state") or "") != "applied":
        raise HTTPException(status_code=409, detail=f"Proposal is not in applied state: {request_body.proposal_id}")
    apply_operation = await service.get_latest_operation(request_body.proposal_id, "apply")
    apply_rollback_info = dict((apply_operation or {}).get("details", {}).get("rollback") or {})
    if not bool(apply_rollback_info.get("supported")) or str(apply_rollback_info.get("state") or "") != "captured":
        reason = str(apply_rollback_info.get("reason") or "rollback is unavailable")
        raise HTTPException(status_code=409, detail=reason)
    try:
        snapshot = _load_sys_governance_rollback_snapshot(
            _sys_governance_store_path(app_state),
            request_body.proposal_id,
            dict(apply_rollback_info.get("snapshot") or {}),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=f"Rollback snapshot is invalid: {exc}") from exc

    idempotency_key = request_body.request_id or f"rollback-{uuid4().hex[:12]}"
    try:
        _claimed_proposal, rollback_operation = await service.claim_rollback(
            request_body.proposal_id,
            principal,
            idempotency_key,
            action_reason=request_body.action_reason,
        )
    except GovernanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GovernanceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rollback_operation_id = rollback_operation["operation_id"]
    _append_sys_governance_event(
        _sys_governance_events_path(app_state),
        {
            "event_type": "governance_rollback_attempted",
            "proposal_id": request_body.proposal_id,
            "tool_name": "sys",
            "traceability": trace,
        },
    )

    rollback_parameters = {
        "command": "tee",
        "args": [snapshot["target_path"]],
        "stdin_text": snapshot["content"],
        "target_path": snapshot["target_path"],
        "write_mode": "overwrite",
        "storage_scope": "wsl_workspace",
        "workdir": str((proposal.get("parameters") or {}).get("workdir") or os.getenv("LIARA_AGENT_WORKSPACE_ROOT", "/home/liara/workspace")),
        **trace,
        "source": "governance_rollback",
        "context": "api.tools.sys.governance.rollback_compensation",
    }
    # Compensating proposal creation/auto-approval intentionally stays on the
    # legacy file-backed store (see docstring) -- unchanged from before Phase 1.
    from services.tools.governance import create_pending_sys_governance_proposal
    rollback_proposal = await asyncio.to_thread(
        create_pending_sys_governance_proposal,
        command="tee",
        parameters=rollback_parameters,
        capability="governance_rollback",
        rationale=f"Compensate applied proposal {request_body.proposal_id}",
        requested_by=principal.actor_id,
        traceability=trace,
        handoff={
            "state": "rollback_pending",
            "step_id": f"rollback-{request_body.proposal_id}",
            "rollback_of": request_body.proposal_id,
        },
        origin="governance_rollback",
    )
    file_proposals = _sync_sys_governance_store(app_state)
    rollback_proposal = file_proposals[str(rollback_proposal["proposal_id"])]
    now = datetime.now(UTC).isoformat()
    rollback_proposal["decision"] = "approved"
    rollback_proposal["decided_by"] = principal.actor_id
    rollback_proposal["decision_reason"] = request_body.action_reason
    rollback_proposal["decision_at"] = now
    rollback_proposal["rollback_of"] = request_body.proposal_id
    rollback_proposal["updated_at"] = now
    file_proposals[str(rollback_proposal["proposal_id"])] = rollback_proposal
    _persist_sys_governance_proposals(_sys_governance_store_path(app_state), file_proposals)
    _append_sys_governance_event(
        _sys_governance_events_path(app_state),
        {
            "event_type": "proposal_decided",
            "proposal_id": rollback_proposal["proposal_id"],
            "tool_name": "sys",
            "decision": "approved",
            "decided_by": principal.actor_id,
            "decision_reason": request_body.action_reason,
            "command": "tee",
            "rollback_of": request_body.proposal_id,
            "traceability": trace,
        },
    )

    try:
        from services.api.routers.tools import invoke_tool
        rollback_invoke_parameters = dict(rollback_parameters)
        rollback_invoke_parameters["proposal_id"] = rollback_proposal["proposal_id"]
        execution = await invoke_tool(
            "sys",
            ToolInvokeRequest(parameters=rollback_invoke_parameters, timeout_seconds=120),
            request,
            Response(),
        )
        evidence = dict(execution.metadata.get("mutation_evidence") or {})
        if execution.status != "success" or not bool(execution.metadata.get("mutation_verified")):
            raise RuntimeError(execution.error or "rollback mutation was not verified")
        if str(evidence.get("sha256") or "") != str(snapshot.get("sha256") or ""):
            raise RuntimeError("rollback content digest was not restored")
    except Exception as exc:
        await service.complete_rollback(
            rollback_operation_id,
            request_body.proposal_id,
            principal,
            success=False,
            details={"acted_by": principal.actor_id, "reason": request_body.action_reason, "error": str(exc)},
        )
        _append_sys_governance_event(
            _sys_governance_events_path(app_state),
            {
                "event_type": "governance_rollback_failed",
                "proposal_id": request_body.proposal_id,
                "rollback_proposal_id": rollback_proposal["proposal_id"],
                "tool_name": "sys",
                "error": str(exc),
                "traceability": trace,
            },
        )
        raise HTTPException(status_code=409, detail=f"Governance rollback failed: {exc}") from exc

    now = datetime.now(UTC).isoformat()
    updated_proposal = await service.complete_rollback(
        rollback_operation_id,
        request_body.proposal_id,
        principal,
        success=True,
        details={
            "acted_by": principal.actor_id,
            "reason": request_body.action_reason,
            "completed_at": now,
            "restored_sha256": snapshot["sha256"],
            "rollback_proposal_id": rollback_proposal["proposal_id"],
        },
    )
    item = dict(updated_proposal)
    item["transaction"] = {
        "state": "rolled_back",
        "rollback": {
            "state": "completed",
            "completed_at": now,
            "restored_sha256": snapshot["sha256"],
        },
    }
    _append_sys_governance_event(
        _sys_governance_events_path(app_state),
        {
            "event_type": "governance_rollback_completed",
            "proposal_id": request_body.proposal_id,
            "rollback_proposal_id": rollback_proposal["proposal_id"],
            "tool_name": "sys",
            "restored_sha256": snapshot["sha256"],
            "traceability": trace,
        },
    )
    # Re-sync from the file store: invoke_tool() above applied its own
    # invocation-bookkeeping to the compensating rollback_proposal record and
    # persisted it -- our earlier in-memory `file_proposals` reference predates
    # that update.
    file_proposals = _sync_sys_governance_store(app_state)
    return {
        "status": "success",
        "action": "rollback",
        "item": item,
        "rollback_proposal": file_proposals.get(str(rollback_proposal["proposal_id"]), rollback_proposal),
        "execution": execution.model_dump(mode="json"),
    }


@router.get("/admin/sys-audit/summary")
async def sys_audit_summary(
    response: Response,
    limit: int = Query(default=500, ge=1, le=5000),
    blocked_only: bool = Query(default=False),
    source: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    command_family: str | None = Query(default=None),
    log_path: str | None = Query(default=None),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    audit_path = Path(log_path) if log_path else None
    entries = load_sys_audit_entries(audit_path, limit=limit)
    filtered = filter_sys_audit_entries(
        entries,
        blocked_only=blocked_only,
        source=source,
        risk_level=risk_level,
        command_family=command_family,
    )
    summary = summarize_sys_audit_entries(filtered)
    summary["available_entries"] = count_sys_audit_entries(audit_path)
    summary["inspected_entries"] = len(entries)
    summary["filtered_entries"] = len(filtered)
    return {
        "status": "success",
        "summary": summary,
        "filters": {
            "blocked_only": blocked_only,
            "source": source or "all",
            "risk_level": risk_level or "all",
            "command_family": command_family or "all",
            "limit": limit,
            "log_path": log_path,
        },
    }


@router.get("/admin/sys-audit/suspicious")
async def sys_audit_suspicious(
    response: Response,
    limit: int = Query(default=500, ge=1, le=5000),
    max_items: int = Query(default=30, ge=1, le=200),
    blocked_only: bool = Query(default=False),
    source: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    command_family: str | None = Query(default=None),
    log_path: str | None = Query(default=None),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    entries = load_sys_audit_entries(Path(log_path) if log_path else None, limit=limit)
    filtered = filter_sys_audit_entries(
        entries,
        blocked_only=blocked_only,
        source=source,
        risk_level=risk_level,
        command_family=command_family,
    )
    suspicious = find_suspicious_entries(filtered, limit=max_items)
    return {
        "status": "success",
        "count": len(suspicious),
        "items": suspicious,
        "filters": {
            "blocked_only": blocked_only,
            "source": source or "all",
            "risk_level": risk_level or "all",
            "command_family": command_family or "all",
            "limit": limit,
            "max_items": max_items,
            "log_path": log_path,
        },
    }


@router.get("/admin/sys-audit/presets/{preset_name}")
async def sys_audit_preset(
    preset_name: str,
    response: Response,
    log_path: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=5000),
    max_items: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    presets: dict[str, dict[str, Any]] = {
        "top-risk": {
            "blocked_only": False,
            "source": "all",
            "risk_level": "high",
            "command_family": "all",
            "limit": 500,
            "max_items": 30,
        },
        "blocked-only": {
            "blocked_only": True,
            "source": "all",
            "risk_level": "all",
            "command_family": "all",
            "limit": 500,
            "max_items": 30,
        },
        "orchestrator-network-risk": {
            "blocked_only": False,
            "source": "orchestrator",
            "risk_level": "high",
            "command_family": "network",
            "limit": 800,
            "max_items": 40,
        },
    }

    preset_key = preset_name.strip().lower()
    if preset_key not in presets:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Unknown preset: {preset_name}",
                "available_presets": sorted(presets.keys()),
            },
        )

    selected = dict(presets[preset_key])
    selected["limit"] = int(limit or selected["limit"])
    selected["max_items"] = int(max_items or selected["max_items"])

    entries = load_sys_audit_entries(Path(log_path) if log_path else None, limit=selected["limit"])
    filtered = filter_sys_audit_entries(
        entries,
        blocked_only=selected["blocked_only"],
        source=selected["source"],
        risk_level=selected["risk_level"],
        command_family=selected["command_family"],
    )

    summary = summarize_sys_audit_entries(filtered)
    summary["inspected_entries"] = len(entries)
    summary["filtered_entries"] = len(filtered)
    suspicious = find_suspicious_entries(filtered, limit=selected["max_items"])

    return {
        "status": "success",
        "preset": preset_key,
        "config": {
            **selected,
            "log_path": log_path,
        },
        "summary": summary,
        "count": len(suspicious),
        "items": suspicious,
    }
