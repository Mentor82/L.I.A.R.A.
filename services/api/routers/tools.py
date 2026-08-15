"""FastAPI router for tool discovery and execution endpoints."""

from __future__ import annotations

import asyncio
import json
import os
from hashlib import sha256
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from services.api.deps import get_governance_service, get_verified_principal
from services.api.exceptions import GovernanceConflictError, GovernanceNotFoundError
from services.api.models import ToolInvokeRequest
from services.api.security import Principal
from services.contracts import ToolExecutionRequest, ToolExecutionResult
from services.shared.types import MemoryTier
from services.tools.coordinator import ToolCoordinator
from services.tools.governance import (
    append_sys_governance_event,
    sys_governance_events_path,
    sys_governance_invocation_digest,
    sys_governance_mode,
)
from services.tools.registry import get_tool_registry


router = APIRouter(prefix="/tools", tags=["tools"])


async def _get_session_snapshot_best_effort(adapter: Any, session_id: str) -> dict[str, Any]:
    if not adapter or not hasattr(adapter, "get"):
        return {}
    try:
        snapshot = await adapter.get(MemoryTier.SESSION, f"session:{session_id}", default={})
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


_SYS_GOVERNANCE_RUNTIME_PARAMETER_KEYS = {
    "proposal_id",
    "request_id",
    "run_id",
    "session_id",
    "source",
    "context",
    "_governance_authorized",
}





def _is_public_tool_name(tool_name: str) -> bool:
    name = (tool_name or "").strip().lower()
    return bool(name) and not name.startswith("_") and name != "orientation"


def _cacheable_json_response(payload: dict[str, Any], request: Request, cache_control: str) -> Response:
    data_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    etag = f'"{sha256(data_bytes).hexdigest()[:16]}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return Response(
        content=data_bytes,
        status_code=200,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": cache_control},
    )


@router.get("")
async def list_tools(request: Request) -> Response:
    tool_registry = get_tool_registry()
    names = [name for name in tool_registry.list_tools() if _is_public_tool_name(name)]
    payload = {
        "status": "success",
        "count": len(names),
        "tools": [tool_registry.get_metadata(name) for name in names],
    }
    return _cacheable_json_response(payload, request, cache_control="public, max-age=300, stale-while-revalidate=600")


@router.get("/{tool_name}")
async def tool_metadata(tool_name: str, request: Request) -> Response:
    tool_registry = get_tool_registry()
    if not _is_public_tool_name(tool_name):
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    try:
        metadata = tool_registry.get_metadata(tool_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = {
        "status": "success",
        "tool": metadata,
    }
    return _cacheable_json_response(payload, request, cache_control="public, max-age=300, stale-while-revalidate=600")


@router.post("/{tool_name}/invoke", response_model=ToolExecutionResult)
async def invoke_tool(
    tool_name: str,
    request_body: ToolInvokeRequest,
    request: Request,
    response: Response,
    service: Any = Depends(get_governance_service),
    principal: Principal = Depends(get_verified_principal),
) -> ToolExecutionResult:
    """Invoke a tool, with governance-bound authorization and invocation-count
    bookkeeping for the "sys" tool when a proposal_id is supplied.

    The proposal lookup and invocation claim/complete are Postgres-backed
    (GovernanceService.get_proposal/claim_invocation/complete_invocation)
    rather than the legacy file store -- a proposal that only exists in
    Postgres (the normal case once a real pool is configured) previously
    404'd here unconditionally, since the file-based lookup never saw it.
    """
    response.headers["Cache-Control"] = "no-store"
    tool_registry = get_tool_registry()
    if not _is_public_tool_name(tool_name) or tool_name not in tool_registry.list_tools():
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    parameters = dict(request_body.parameters)
    parameters.pop("_governance_authorized", None)
    governance_proposal: dict[str, Any] | None = None
    if tool_name == "sys":
        governance_mode = sys_governance_mode()
        proposal_id = str(parameters.get("proposal_id") or "").strip()
        if governance_mode == "all" and not proposal_id:
            raise HTTPException(
                status_code=422,
                detail="SYS governance mode 'all' requires proposal_id",
            )
        if proposal_id:
            try:
                proposal = await service.get_proposal(proposal_id)
            except GovernanceNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if str(proposal.get("decision") or "") != "approved":
                raise HTTPException(
                    status_code=409,
                    detail=f"Sys proposal is not approved: {proposal_id}",
                )
            expected_command = str(proposal.get("command") or "").strip()
            approved_parameters = dict(proposal.get("parameters") or {})
            for key, value in approved_parameters.items():
                if key in _SYS_GOVERNANCE_RUNTIME_PARAMETER_KEYS:
                    continue
                if key in parameters and parameters[key] != value:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Sys invoke parameter does not match approved proposal: {key}",
                    )
                parameters.setdefault(key, value)
            incoming_command = str(parameters.get("command") or "").strip()
            expected_digest = str(proposal.get("invocation_digest") or "").strip() or sys_governance_invocation_digest(
                expected_command,
                approved_parameters,
            )
            incoming_digest = sys_governance_invocation_digest(incoming_command, parameters)
            if expected_command != incoming_command or expected_digest != incoming_digest:
                raise HTTPException(
                    status_code=409,
                    detail="Sys invoke action does not match approved proposal",
                )
            governance_proposal = proposal
            parameters["_governance_authorized"] = True

    if tool_name == "sys" and not parameters.get("workdir") and not parameters.get("workspace_session_id"):
        session_id = parameters.get("session_id")
        if session_id:
            adapter = getattr(request.app.state, "memory_adapter", None)
            if adapter:
                snapshot = await _get_session_snapshot_best_effort(adapter, session_id)
                metadata = dict(snapshot.get("metadata") or {})
                sandbox_root = metadata.get("sandbox_root")
                if sandbox_root:
                    parameters["workdir"] = sandbox_root

    generated_trace_id = f"api-tool-{uuid4().hex[:12]}"
    if not str(parameters.get("request_id") or "").strip():
        parameters["request_id"] = generated_trace_id
    if not str(parameters.get("run_id") or "").strip():
        parameters["run_id"] = str(parameters.get("request_id") or generated_trace_id)
    if not str(parameters.get("source") or "").strip():
        parameters["source"] = "api"
    if not str(parameters.get("context") or "").strip():
        parameters["context"] = f"api.tools.{tool_name}.invoke"

    def _invocation_traceability() -> dict[str, Any]:
        return {
            "request_id": parameters.get("request_id"),
            "run_id": parameters.get("run_id"),
            "session_id": parameters.get("session_id"),
            "source": parameters.get("source"),
            "context": parameters.get("context"),
        }

    claimed_revision: int | None = None
    if governance_proposal is not None:
        proposal_id = str(governance_proposal["proposal_id"])
        try:
            claimed = await service.claim_invocation(
                proposal_id, principal, parameters.get("request_id"), parameters.get("run_id"),
            )
        except GovernanceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        claimed_revision = claimed["revision"]
        # Dual-write into the legacy JSONL feed: GET /tools/sys/governance/events
        # still reads from there (Phase 4/6 territory to migrate that read
        # endpoint), so consumers of that feed must keep seeing invocation
        # activity even though Postgres is now the authoritative store.
        append_sys_governance_event(
            {
                "event_type": "invocation_attempted",
                "proposal_id": proposal_id,
                "tool_name": "sys",
                "invocation_digest": governance_proposal.get("invocation_digest"),
                "attempt_count": (claimed.get("invocation") or {}).get("attempt_count"),
                "traceability": _invocation_traceability(),
            },
            sys_governance_events_path(),
        )

    coordinator = ToolCoordinator()
    exec_request = ToolExecutionRequest(
        tool_name=tool_name,
        parameters=parameters,
        timeout_seconds=request_body.timeout_seconds,
        simulation_mode=request_body.simulation_mode,
    )

    try:
        result = await coordinator.execute_tool(exec_request)
    except Exception as exc:
        if governance_proposal is not None and claimed_revision is not None:
            try:
                await service.complete_invocation(
                    str(governance_proposal["proposal_id"]), claimed_revision,
                    success=False, error=str(exc), actor_id=principal.actor_id,
                )
                append_sys_governance_event(
                    {
                        "event_type": "invocation_failed",
                        "proposal_id": str(governance_proposal["proposal_id"]),
                        "tool_name": "sys",
                        "error": str(exc),
                        "traceability": _invocation_traceability(),
                    },
                    sys_governance_events_path(),
                )
            except GovernanceConflictError:
                pass
        raise

    if governance_proposal is not None and claimed_revision is not None:
        try:
            succeeded = result.status == "success"
            await service.complete_invocation(
                str(governance_proposal["proposal_id"]), claimed_revision,
                success=succeeded, status=result.status,
                error=result.error, execution_ms=result.execution_ms,
                actor_id=principal.actor_id,
            )
            append_sys_governance_event(
                {
                    "event_type": "invocation_completed" if succeeded else "invocation_failed",
                    "proposal_id": str(governance_proposal["proposal_id"]),
                    "tool_name": "sys",
                    "status": result.status,
                    "error": result.error,
                    "execution_ms": result.execution_ms,
                    "traceability": _invocation_traceability(),
                },
                sys_governance_events_path(),
            )
        except GovernanceConflictError:
            pass

    if governance_proposal and result.metadata:
        result.metadata["governance_proposal_id"] = str(governance_proposal.get("proposal_id"))
        result.metadata["governance_decided_by"] = str(governance_proposal.get("decided_by"))

    return result
