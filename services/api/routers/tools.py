"""FastAPI router for tool discovery and execution endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request, Response

from services.api.deps import get_app_symbol
from services.api.models import ToolInvokeRequest
from services.contracts import ToolExecutionRequest, ToolExecutionResult
from services.shared.types import MemoryTier
from services.tools.coordinator import ToolCoordinator
from services.tools.governance import (
    append_sys_governance_event,
    load_sys_governance_proposals,
    persist_sys_governance_proposals,
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
async def invoke_tool(tool_name: str, request_body: ToolInvokeRequest, request: Request, response: Response) -> ToolExecutionResult:
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
            sync_fn = get_app_symbol("_sync_sys_governance_store", None)
            proposals = sync_fn(request.app.state) if sync_fn else getattr(request.app.state, "sys_tool_proposals", {})
            proposal = proposals.get(proposal_id)
            if proposal is None:
                raise HTTPException(status_code=404, detail=f"Unknown sys proposal: {proposal_id}")
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

    uuid4_fn = get_app_symbol("uuid4", uuid4)
    generated_trace_id = f"api-tool-{uuid4_fn().hex[:12]}"
    if not str(parameters.get("request_id") or "").strip():
        parameters["request_id"] = generated_trace_id
    if not str(parameters.get("run_id") or "").strip():
        parameters["run_id"] = str(parameters.get("request_id") or generated_trace_id)
    if not str(parameters.get("source") or "").strip():
        parameters["source"] = "api"
    if not str(parameters.get("context") or "").strip():
        parameters["context"] = f"api.tools.{tool_name}.invoke"

    if governance_proposal is not None:
        proposal_id = str(governance_proposal["proposal_id"])
        async with request.app.state.sys_tool_governance_lock:
            current = request.app.state.sys_tool_proposals.get(proposal_id)
            invocation = dict((current or {}).get("invocation") or {})
            if invocation.get("state") == "invoking":
                raise HTTPException(status_code=409, detail=f"Sys proposal invocation already in progress: {proposal_id}")
            attempt_count = int(invocation.get("attempt_count") or 0)
            success_count = int(invocation.get("success_count") or 0)
            max_invocations = int((current or {}).get("max_invocations") or 1)
            if attempt_count >= max_invocations:
                raise HTTPException(status_code=409, detail=f"Sys proposal invocation limit reached: {proposal_id}")
            invocation.update(
                {
                    "state": "invoking",
                    "attempt_count": attempt_count + 1,
                    "success_count": success_count,
                    "last_attempt_at": datetime.now(UTC).isoformat(),
                    "last_request_id": parameters.get("request_id"),
                    "last_run_id": parameters.get("run_id"),
                }
            )
            current["invocation"] = invocation
            current["updated_at"] = invocation["last_attempt_at"]
            persist_fn = get_app_symbol("_persist_sys_governance_proposals", None)
            if persist_fn:
                persist_fn(request.app.state.sys_tool_proposals_path, request.app.state.sys_tool_proposals)
            append_event_fn = get_app_symbol("_append_sys_governance_event", None)
            if append_event_fn:
                append_event_fn(
                    request.app.state.sys_tool_events_path,
                    {
                        "event_type": "invocation_attempted",
                        "proposal_id": proposal_id,
                        "tool_name": "sys",
                        "invocation_digest": current.get("invocation_digest"),
                        "attempt_count": invocation["attempt_count"],
                        "traceability": {
                            "request_id": parameters.get("request_id"),
                            "run_id": parameters.get("run_id"),
                            "session_id": parameters.get("session_id"),
                            "source": parameters.get("source"),
                            "context": parameters.get("context"),
                        },
                    },
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
        if governance_proposal is not None:
            proposal_id = str(governance_proposal["proposal_id"])
            async with request.app.state.sys_tool_governance_lock:
                current = request.app.state.sys_tool_proposals.get(proposal_id)
                if current:
                    invocation = dict(current.get("invocation") or {})
                    invocation.update({
                        "state": "failed",
                        "last_completed_at": datetime.now(UTC).isoformat(),
                        "last_error": str(exc),
                    })
                    current["invocation"] = invocation
                    current["updated_at"] = invocation["last_completed_at"]
                    persist_fn = get_app_symbol("_persist_sys_governance_proposals", None)
                    if persist_fn:
                        persist_fn(request.app.state.sys_tool_proposals_path, request.app.state.sys_tool_proposals)
                    else:
                        persist_sys_governance_proposals(request.app.state.sys_tool_proposals, request.app.state.sys_tool_proposals_path)
                    append_event_fn = get_app_symbol("_append_sys_governance_event", None)
                    if append_event_fn:
                        append_event_fn(
                            request.app.state.sys_tool_events_path,
                            {
                                "event_type": "invocation_failed",
                                "proposal_id": proposal_id,
                                "tool_name": "sys",
                                "error": str(exc),
                                "traceability": {
                                    "request_id": parameters.get("request_id"),
                                    "run_id": parameters.get("run_id"),
                                    "session_id": parameters.get("session_id"),
                                    "source": parameters.get("source"),
                                    "context": parameters.get("context"),
                                },
                            },
                        )
                    else:
                        append_sys_governance_event(
                            {
                                "event_type": "invocation_failed",
                                "proposal_id": proposal_id,
                                "tool_name": "sys",
                                "error": str(exc),
                                "traceability": {
                                    "request_id": parameters.get("request_id"),
                                    "run_id": parameters.get("run_id"),
                                    "session_id": parameters.get("session_id"),
                                    "source": parameters.get("source"),
                                    "context": parameters.get("context"),
                                },
                            },
                            request.app.state.sys_tool_events_path,
                        )
        raise

    if governance_proposal is not None:
        proposal_id = str(governance_proposal["proposal_id"])
        async with request.app.state.sys_tool_governance_lock:
            current = request.app.state.sys_tool_proposals.get(proposal_id)
            if current:
                invocation = dict(current.get("invocation") or {})
                succeeded = result.status == "success"
                success_count = int(invocation.get("success_count") or 0) + int(succeeded)
                invocation.update(
                    {
                        "state": "completed" if succeeded else "failed",
                        "success_count": success_count,
                        "last_completed_at": datetime.now(UTC).isoformat(),
                        "last_status": result.status,
                        "last_error": result.error,
                        "last_execution_ms": result.execution_ms,
                    }
                )
                current["invocation"] = invocation
                current["updated_at"] = invocation["last_completed_at"]
                persist_fn = get_app_symbol("_persist_sys_governance_proposals", None)
                if persist_fn:
                    persist_fn(request.app.state.sys_tool_proposals_path, request.app.state.sys_tool_proposals)
                else:
                    persist_sys_governance_proposals(request.app.state.sys_tool_proposals, request.app.state.sys_tool_proposals_path)
                append_event_fn = get_app_symbol("_append_sys_governance_event", None)
                if append_event_fn:
                    append_event_fn(
                        request.app.state.sys_tool_events_path,
                        {
                            "event_type": "invocation_completed" if succeeded else "invocation_failed",
                            "proposal_id": proposal_id,
                            "tool_name": "sys",
                            "status": result.status,
                            "error": result.error,
                            "execution_ms": result.execution_ms,
                            "success_count": success_count,
                            "traceability": {
                                "request_id": parameters.get("request_id"),
                                "run_id": parameters.get("run_id"),
                                "session_id": parameters.get("session_id"),
                                "source": parameters.get("source"),
                                "context": parameters.get("context"),
                            },
                        },
                    )
                else:
                    append_sys_governance_event(
                        {
                            "event_type": "invocation_completed" if succeeded else "invocation_failed",
                            "proposal_id": proposal_id,
                            "tool_name": "sys",
                            "status": result.status,
                            "error": result.error,
                            "execution_ms": result.execution_ms,
                            "success_count": success_count,
                            "traceability": {
                                "request_id": parameters.get("request_id"),
                                "run_id": parameters.get("run_id"),
                                "session_id": parameters.get("session_id"),
                                "source": parameters.get("source"),
                                "context": parameters.get("context"),
                            },
                        },
                        request.app.state.sys_tool_events_path,
                    )

    if governance_proposal and result.metadata:
        result.metadata["governance_proposal_id"] = str(governance_proposal.get("proposal_id"))
        result.metadata["governance_decided_by"] = str(governance_proposal.get("decided_by"))

    return result
