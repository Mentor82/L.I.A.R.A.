"""FastAPI router for operations, dreaming, workspace and heartbeat endpoints."""

from __future__ import annotations

import os
import sys
from typing import Any, Literal
from fastapi import APIRouter, HTTPException, Query, Request, Response

from services.api.deps import get_app_symbol
import httpx

from services.contracts import (
    GraphSubgraphRequest,
    GraphSubgraphResponse,
    HeartbeatOperationsResponse,
    HeartbeatSnapshot,
    MemoryDreamingProposalListRequest,
    MemoryHealthResponse,
    MemoryServiceStatus,
    RelationCleanupExpiredRequest,
    RelationCleanupExpiredResponse,
    SelfObserverOperationsResponse,
    SelfInspectionDecision,
    SystemStateEnvelope,
    StateCurve,
)
from services.memory_adapter import RemoteMemoryAdapter


router = APIRouter(tags=["operations"])





def _dreaming_assurance_projection(proposal: dict[str, Any]) -> dict[str, Any]:
    metadata = proposal.get("metadata") if isinstance(proposal.get("metadata"), dict) else {}
    validator_evidence = next(
        (
            item
            for item in reversed(list(proposal.get("evidence") or []))
            if isinstance(item, dict) and item.get("source") == "validator_report"
        ),
        None,
    )
    evidence_metadata = (
        validator_evidence.get("metadata")
        if isinstance(validator_evidence, dict) and isinstance(validator_evidence.get("metadata"), dict)
        else {}
    )
    verdict = str(metadata.get("assurance_verdict") or evidence_metadata.get("verdict") or "pending")
    if verdict not in {"pending", "passed", "attention", "failed"}:
        verdict = "pending"
    artifacts = metadata.get("assurance_artifacts") or evidence_metadata.get("artifacts") or []
    artifact_refs = [
        {"path": str(path), "kind": "validator_report"}
        for path in dict.fromkeys(str(path) for path in artifacts if str(path).strip())
    ]
    required = bool(metadata.get("assurance_required", False))
    decision = str(proposal.get("decision") or "pending")
    return {
        "required": required,
        "verdict": verdict,
        "blocked": required and decision == "pending" and verdict != "passed",
        "validator_job_id": metadata.get("assurance_job_id") or (
            validator_evidence.get("reference") if isinstance(validator_evidence, dict) else None
        ),
        "findings_count": int(metadata.get("assurance_findings_count") or evidence_metadata.get("findings_count") or 0),
        "highest_severity": evidence_metadata.get("highest_severity") or "none",
        "artifacts": artifact_refs,
        "audit_reference": {
            "operation": "dreaming_attach_assurance",
            "proposal_id": proposal.get("proposal_id"),
        },
    }


def _dreaming_quality_projection(proposal: dict[str, Any]) -> dict[str, Any]:
    quality_evidence = next(
        (
            item
            for item in reversed(list(proposal.get("evidence") or []))
            if isinstance(item, dict) and item.get("source") == "proposal_quality_signals"
        ),
        None,
    )
    metadata = (
        quality_evidence.get("metadata")
        if isinstance(quality_evidence, dict) and isinstance(quality_evidence.get("metadata"), dict)
        else {}
    )
    complexity = metadata.get("complexity") if isinstance(metadata.get("complexity"), dict) else {}
    coverage = metadata.get("coverage") if isinstance(metadata.get("coverage"), dict) else {}
    level = str(complexity.get("level") or "unavailable")
    if level not in {"low", "moderate", "high"}:
        level = "unavailable"
    coverage_status = str(coverage.get("status") or "unavailable")
    if coverage_status not in {"measured", "not_applicable"}:
        coverage_status = "unavailable"

    def _optional_ratio(value: Any) -> float | None:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            return None
        return round(max(0.0, min(ratio, 1.0)), 3)

    def _non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    return {
        "available": quality_evidence is not None,
        "schema_version": _non_negative_int(metadata.get("schema_version")) or None,
        "interpretation": str(metadata.get("interpretation") or "validator_evidence_only"),
        "complexity": {
            "score": _optional_ratio(complexity.get("score")),
            "level": level,
            "character_count": _non_negative_int(complexity.get("character_count")),
            "line_count": _non_negative_int(complexity.get("line_count")),
            "declared_source_count": _non_negative_int(complexity.get("declared_source_count")),
            "evidence_count": _non_negative_int(complexity.get("evidence_count")),
            "accepted_relation_count": _non_negative_int(complexity.get("accepted_relation_count")),
        },
        "coverage": {
            "status": coverage_status,
            "source_coverage_ratio": _optional_ratio(coverage.get("source_coverage_ratio")),
            "relation_coverage_ratio": _optional_ratio(coverage.get("relation_coverage_ratio")),
            "uncovered_source_ids": [str(value) for value in list(coverage.get("uncovered_source_ids") or [])],
            "relation_uncovered_source_ids": [
                str(value) for value in list(coverage.get("relation_uncovered_source_ids") or [])
            ],
        },
    }


async def _fetch_heartbeat_operations(base_url: str, window_seconds: int, timeout_seconds: float) -> HeartbeatOperationsResponse:
    url = f"{base_url.rstrip('/')}/operations/heartbeat?window_seconds={window_seconds}"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        res = await client.get(url)
        res.raise_for_status()
        return HeartbeatOperationsResponse(**res.json())


async def _fetch_self_observer_operations(base_url: str, history_limit: int, timeout_seconds: float) -> SelfObserverOperationsResponse:
    url = f"{base_url.rstrip('/')}/operations/self-observer?history_limit={history_limit}"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        res = await client.get(url)
        res.raise_for_status()
        return SelfObserverOperationsResponse(**res.json())


@router.post("/memory/relations/cleanup-expired", response_model=RelationCleanupExpiredResponse)
async def memory_relations_cleanup_expired(request_body: RelationCleanupExpiredRequest, request: Request, response: Response) -> RelationCleanupExpiredResponse:
    response.headers["Cache-Control"] = "no-store"
    adapter = getattr(request.app.state, "memory_adapter", None)
    if isinstance(adapter, RemoteMemoryAdapter):
        try:
            payload = await adapter._post_json("/relations/cleanup-expired", request_body.model_dump())
            return RelationCleanupExpiredResponse(**payload)
        except Exception as exc:
            return RelationCleanupExpiredResponse(
                removed=0,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=f"relation_cleanup_proxy_error: {exc}",
                ),
            )

    from services.memory.store import BackedMemoryServiceStore
    store_cls = get_app_symbol("BackedMemoryServiceStore", BackedMemoryServiceStore)
    store = store_cls()
    try:
        return await store.relation_cleanup_expired(request_body)
    finally:
        await store.close()


@router.get("/operations/dreaming")
async def operations_dreaming(
    request: Request,
    response: Response,
    decision: Literal["pending", "approved", "rejected", "all"] = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Read-only Dreaming/Staging operations snapshot for local UIs."""
    response.headers["Cache-Control"] = "no-store"
    dreaming_request = MemoryDreamingProposalListRequest(decision=decision, limit=limit)
    adapter = getattr(request.app.state, "memory_adapter", None)

    try:
        if isinstance(adapter, RemoteMemoryAdapter):
            status_payload = await adapter._get_json("/dreaming/status")
            proposals_payload = await adapter._post_json("/dreaming/proposals", dreaming_request.model_dump())
        else:
            from services.memory.store import BackedMemoryServiceStore
            store_cls = get_app_symbol("BackedMemoryServiceStore", BackedMemoryServiceStore)
            store = store_cls()
            try:
                status = await store.dreaming_status()
                proposals = await store.dreaming_proposals(dreaming_request)
                status_payload = status.model_dump(mode="json")
                proposals_payload = proposals.model_dump(mode="json")
            finally:
                await store.close()
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"dreaming_operations_error: {exc}",
            "scheduler_enabled": False,
            "mode": "manual_only",
            "pending_staged_items": 0,
            "pending_proposals": 0,
            "proposal_count": 0,
            "proposals": [],
            "assurance": {
                "required": 0,
                "blocked": 0,
                "verdicts": {"pending": 0, "passed": 0, "attention": 0, "failed": 0},
            },
            "quality_signals": {
                "available": 0,
                "complexity_levels": {"low": 0, "moderate": 0, "high": 0},
            },
        }

    proposals_items = []
    for raw_item in list(proposals_payload.get("items") or []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        item["assurance"] = _dreaming_assurance_projection(item)
        item["quality_signals"] = _dreaming_quality_projection(item)
        proposals_items.append(item)
    pending_count = sum(1 for item in proposals_items if item.get("decision") == "pending")
    verdict_counts = {"pending": 0, "passed": 0, "attention": 0, "failed": 0}
    required_count = 0
    blocked_count = 0
    quality_available = 0
    complexity_levels = {"low": 0, "moderate": 0, "high": 0}
    for item in proposals_items:
        assurance = item["assurance"]
        verdict_counts[assurance["verdict"]] += 1
        required_count += int(assurance["required"])
        blocked_count += int(assurance["blocked"])
        quality = item["quality_signals"]
        quality_available += int(quality["available"])
        quality_level = quality["complexity"]["level"]
        if quality_level in complexity_levels:
            complexity_levels[quality_level] += 1
    return {
        "status": "success",
        "scheduler_enabled": bool(status_payload.get("scheduler_enabled", False)),
        "mode": status_payload.get("mode", "manual_only"),
        "last_run_id": status_payload.get("last_run_id"),
        "last_run_at": status_payload.get("last_run_at"),
        "last_run_state": status_payload.get("last_run_state", "idle"),
        "pending_staged_items": int(status_payload.get("pending_staged_items") or 0),
        "pending_proposals": int(status_payload.get("pending_proposals") or pending_count),
        "proposal_count": len(proposals_items),
        "proposals": proposals_items,
        "assurance": {
            "required": required_count,
            "blocked": blocked_count,
            "verdicts": verdict_counts,
        },
        "quality_signals": {
            "available": quality_available,
            "complexity_levels": complexity_levels,
        },
        "memory_status": status_payload.get("status") or proposals_payload.get("status"),
        "filters": {
            "decision": decision,
            "limit": limit,
        },
    }


@router.get("/operations/workspace")
async def operations_workspace(
    response: Response,
    artifact_type: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    """Return read-only workspace and validation evidence for local operations UIs."""
    allowed_types = {"validation", "governance", "memory", "chat"}
    normalized_type = artifact_type.strip().lower() if artifact_type else None
    if normalized_type and normalized_type not in allowed_types:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Unknown artifact type: {artifact_type}",
                "allowed_types": sorted(allowed_types),
            },
        )

    from services.workspace import get_workspace_status, list_workspace_artifacts
    get_ws_status_fn = get_app_symbol("get_workspace_status", get_workspace_status)
    list_ws_artifacts_fn = get_app_symbol("list_workspace_artifacts", list_workspace_artifacts)

    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "success",
        "workspace": get_ws_status_fn(),
        "artifacts": list_ws_artifacts_fn(
            artifact_type=normalized_type,
            limit=limit,
        ),
        "filters": {
            "artifact_type": normalized_type or "all",
            "limit": limit,
        },
    }


@router.get("/operations/graph/subgraph", response_model=GraphSubgraphResponse)
async def operations_graph_subgraph(
    request: Request,
    response: Response,
    component: Literal["orchestrator", "memory"] = Query(...),
    limit: int = Query(default=20, ge=1, le=25),
) -> GraphSubgraphResponse:
    """Return one bounded, property-filtered Neo4j view for the architecture UI."""
    response.headers["Cache-Control"] = "no-store"
    subgraph_request = GraphSubgraphRequest(component=component, limit=limit)
    adapter = getattr(request.app.state, "memory_adapter", None)

    if isinstance(adapter, RemoteMemoryAdapter):
        try:
            payload = await adapter._post_json(
                "/graph/architecture/subgraph",
                subgraph_request.model_dump(),
            )
            return GraphSubgraphResponse(**payload)
        except Exception as exc:
            return GraphSubgraphResponse(
                component=component,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=f"architecture_subgraph_proxy_error: {exc}",
                ),
            )

    from services.memory.store import BackedMemoryServiceStore
    store_cls = get_app_symbol("BackedMemoryServiceStore", BackedMemoryServiceStore)
    store = store_cls()
    try:
        return await store.architecture_subgraph(subgraph_request)
    finally:
        await store.close()


@router.get("/operations/heartbeat", response_model=HeartbeatOperationsResponse)
async def operations_heartbeat(
    response: Response,
    window_seconds: int = Query(default=300, ge=10, le=900),
) -> HeartbeatOperationsResponse:
    """Proxy the read-only state curve of the independent heartbeat instance."""
    response.headers["Cache-Control"] = "no-store"
    base_url = os.getenv("LIARA_HEARTBEAT_BASE_URL", "http://127.0.0.1:8050")
    timeout_seconds = max(0.2, float(os.getenv("LIARA_HEARTBEAT_PROXY_TIMEOUT_SECONDS", "3")))
    try:
        return await _fetch_heartbeat_operations(
            base_url=base_url,
            window_seconds=window_seconds,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return HeartbeatOperationsResponse(
            status="failed",
            error=f"heartbeat_proxy_error: {exc}",
        )


@router.get("/operations/self-observer", response_model=SelfObserverOperationsResponse)
async def operations_self_observer(
    response: Response,
    history_limit: int = Query(default=60, ge=1, le=240),
) -> SelfObserverOperationsResponse:
    """Expose the observer's read-only state and bounded history."""
    response.headers["Cache-Control"] = "no-store"
    base_url = os.getenv("LIARA_SELF_OBSERVER_BASE_URL", "http://127.0.0.1:8060")
    timeout_seconds = max(0.2, float(os.getenv("LIARA_SELF_OBSERVER_PROXY_TIMEOUT_SECONDS", "4")))
    try:
        return await _fetch_self_observer_operations(
            base_url=base_url,
            history_limit=history_limit,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return SelfObserverOperationsResponse(
            status="failed",
            error=f"self_observer_proxy_error: {exc}",
        )
