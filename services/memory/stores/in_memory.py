"""In-memory backing store for testing, development, and offline execution."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import UTC, datetime
from typing import Any, Dict, List, Literal, Tuple
from uuid import uuid4

from services.contracts import (
    ContextDocument,
    ContextScope,
    ContextSearchRequest,
    ContextSearchResponse,
    ContextUpsertRequest,
    EmbeddingVector,
    GraphAgentUpsertRequest,
    GraphContextGraphRequest,
    GraphContextGraphResponse,
    GraphContextUpsertRequest,
    GraphEmbeddingUpsertRequest,
    GraphFactLinkRequest,
    GraphFactUpsertRequest,
    GraphNodeResponse,
    GraphSemanticLinkRequest,
    GraphSubgraphRequest,
    GraphSubgraphResponse,
    GraphTaskUpsertRequest,
    GraphToolUpsertRequest,
    MemoryDreamingCleanupRequest,
    MemoryDreamingCleanupResponse,
    MemoryDreamingProposalAssuranceRequest,
    MemoryDreamingProposalAssuranceResponse,
    MemoryDreamingProposalDecisionRequest,
    MemoryDreamingProposalDecisionResponse,
    MemoryDreamingProposalListRequest,
    MemoryDreamingProposalListResponse,
    MemoryDreamingProposalRecord,
    MemoryDreamingRunRequest,
    MemoryDreamingRunResponse,
    MemoryDreamingStatusResponse,
    MemoryEmbeddingRequest,
    MemoryEmbeddingResponse,
    MemoryEvidence,
    MemoryFactQueryRequest,
    MemoryFactRecord,
    MemoryFactResponse,
    MemoryFactUpsertRequest,
    MemoryHealthResponse,
    MemoryHistoryAppendRequest,
    MemoryHistoryQueryRequest,
    MemoryHistoryResponse,
    MemoryLifecycleStatus,
    MemoryMessageRecord,
    MemoryRetrievalQueryRequest,
    MemoryRetrievalResponse,
    MemoryRetrievalUpsertRequest,
    MemoryServiceStatus,
    MemoryStagingDiscardRequest,
    MemoryStagingListRequest,
    MemoryStagingRecord,
    MemoryStagingResponse,
    MemoryStagingStageRequest,
    MemoryStagingTouchRequest,
    RelationCleanupExpiredRequest,
    RelationCleanupExpiredResponse,
    RelationEdge,
    RelationExpandRequest,
    RelationExpandResponse,
    RelationUpsertRequest,
    RetrievalDocument,
    ValidatorFinding,
    ValidatorJobSubject,
    ValidatorResultRequest,
    ValidatorResultResponse,
    ValidatorStatusRequest,
    ValidatorStatusResponse,
    ValidatorSubmitRequest,
    ValidatorSubmitResponse,
    can_set_verified,
)
from services.memory.governance import MemoryLifecycleGovernance
from services.memory.stores.base import (
    MemoryServiceStore,
    _append_embedding_request_audit,
    _build_session_summary_candidate,
    _context_scope_present,
    _estimate_token_count,
    _get_store_symbol,
    _is_truthy,
    _parse_aware_datetime,
)
from services.memory.stores.quality_signals import (
    _attach_dreaming_quality_signals,
    _attach_dreaming_relation_evidence,
    _audit_memory_blocked,
    _audit_memory_executed,
    _context_upsert_policy_error,
    _context_upsert_policy_metadata,
    _memory_traceability,
    _proposal_assurance_blocks_approval,
    _proposal_assurance_digest,
    _proposal_quality_signal,
    _proposal_rejected_retention_expired,
    _proposal_with_validator_assurance,
    _relation_metadata_with_defaults,
    _staging_retention_expired,
)
from services.memory.stores.validation import (
    _execute_validator_job,
    _validator_async_enabled,
    _validator_execution_backend_name,
    _validator_subject_from_payload,
    _validator_subject_from_request,
)

class InMemoryMemoryServiceStore(MemoryServiceStore):
    """Small in-memory backing store for liara-memory endpoints."""

    def __init__(self):
        self.history_by_session: Dict[str, List[MemoryMessageRecord]] = {}
        self.facts: Dict[Tuple[str, str], MemoryFactRecord] = {}
        self.retrieval_docs: Dict[str, RetrievalDocument] = {}
        self._context_docs: Dict[str, Any] = {}
        self._relations: List[RelationEdge] = []
        self._staging_records_by_session: Dict[str, List[MemoryStagingRecord]] = {}
        self._dreaming_proposals: List[MemoryDreamingProposalRecord] = []
        self._dreaming_last_run_id: str | None = None
        self._dreaming_last_run_at: str | None = None
        self._dreaming_last_run_state: Literal["idle", "running", "completed", "failed"] = "idle"
        self._validator_jobs: Dict[str, Dict[str, Any]] = {}
        self._validator_tasks: Dict[str, asyncio.Task[Any]] = {}

    def get_health(self) -> Dict[str, Any]:
        """Return status dictionary for in-memory memory store."""
        from services.config import Settings
        mode = getattr(self, "_effective_store_mode", "in_memory")
        fallback_code = getattr(self, "_fallback_reason_code", None)
        deg_codes = getattr(self, "_degradation_codes", [])
        pol = str(getattr(Settings, "PERSISTENCE_POLICY", "strict")).lower()
        return {
            "effective_store_mode": mode,
            "persistence_policy": pol,
            "degraded": mode == "degraded_in_memory" or bool(fallback_code),
            "fallback_reason_code": fallback_code,
            "degradation_codes": deg_codes,
            "available_capabilities": ["session_store", "fact_store", "in_memory"],
            "unavailable_capabilities": ["postgres", "qdrant", "chroma", "neo4j"],
            "metadata": {
                "backend_health": {
                    "postgres": "unavailable",
                    "redis": "unavailable",
                    "qdrant": "unavailable",
                    "chroma": "unavailable",
                    "neo4j": "unavailable",
                }
            },
        }

    async def _run_validator_job_in_memory(self, job_id: str, traceability: dict[str, Any]) -> None:
        payload = self._validator_jobs.get(job_id)
        if payload is None:
            return
        
        token = payload.get("fencing_token", 1)
        payload["state"] = "running"
        payload["lease_owner"] = f"worker_{os.getpid()}"
        payload["lease_expires_at"] = time.time() + 30.0
        payload["updated_at"] = datetime.now(UTC).isoformat()

        exec_fn = _get_store_symbol("_execute_validator_job", _execute_validator_job)
        try:
            execution = await asyncio.to_thread(
                exec_fn,
                job_id=job_id,
                workspace=str(payload.get("workspace") or ""),
                scope=str(payload.get("scope") or "quick"),
                checks=list(payload.get("checks") or []),
                strict_mode=bool(payload.get("strict_mode", False)),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                run_id=payload.get("run_id"),
                source=payload.get("source"),
            )
        except asyncio.CancelledError:
            # Per Rule 4: Upon cancel/shutdown, leave job recoverable via expired lease
            payload["state"] = "queued"
            payload["lease_owner"] = None
            payload["lease_expires_at"] = time.time() - 1
            raise
        except Exception as exc:
            execution = {
                "state": "failed",
                "summary": {"error": f"validator_execution_exception: {exc}"},
                "findings": [{"severity": "error", "message": str(exc)}],
                "artifacts": [],
            }

        # Check fencing token before persisting result
        if payload.get("fencing_token", 1) != token:
            # Token changed (zombie execution aborted)
            return

        payload["state"] = str(execution.get("state") or "completed")
        payload["summary"] = dict(execution.get("summary") or {})
        payload["findings"] = list(execution.get("findings") or [])
        payload["artifacts"] = list(execution.get("artifacts") or [])
        payload["lease_owner"] = None
        payload["lease_expires_at"] = None
        payload["updated_at"] = datetime.now(UTC).isoformat()
        exit_code = 0 if execution.get("state") == "completed" else 1
        _audit_memory_executed(
            operation="validator_execute",
            exit_code=exit_code,
            traceability=traceability,
            args=[f"job_id={job_id}", f"state={execution.get('state')}"],
        )

    async def shutdown_validator_jobs(self) -> None:
        """Cancel running validator tasks gracefully and leave leases expired for recovery."""
        for job_id, task in list(self._validator_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            payload = self._validator_jobs.get(job_id)
            if payload and payload.get("state") == "running":
                payload["state"] = "queued"
                payload["lease_owner"] = None
                payload["lease_expires_at"] = time.time() - 1
        self._validator_tasks.clear()

    async def append_history(self, request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
        item = MemoryMessageRecord(
            message_id=str(uuid4()),
            session_id=request.session_id,
            run_id=request.run_id,
            user_id=request.user_id,
            role=request.role,
            content=request.content,
            created_at=datetime.now(UTC).isoformat(),
            metadata=request.metadata,
        )
        self.history_by_session.setdefault(request.session_id, []).append(item)
        return MemoryHistoryResponse(
            items=[item],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def query_history(self, request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
        items = []
        for item in self.history_by_session.get(request.session_id, []):
            if request.run_id and item.run_id != request.run_id:
                continue
            if not request.include_tool_messages and item.role == "tool":
                continue
            items.append(item)
        return MemoryHistoryResponse(
            items=items[-request.limit :],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def upsert_fact(self, request: MemoryFactUpsertRequest) -> MemoryFactResponse:
        now = datetime.now(UTC).isoformat()
        existing = self.facts.get((request.namespace, request.key))
        request_metadata = dict(request.metadata or {})

        fact_id = str(uuid4())
        created_at = now
        updated_at = None
        metadata = request_metadata

        if existing is not None:
            changed = any(
                [
                    existing.value != request.value,
                    existing.source != request.source,
                    existing.confidence != request.confidence,
                    existing.status != request.status,
                    existing.promotion_reason != request.promotion_reason,
                    existing.evidence != request.evidence,
                    list(existing.tags or []) != list(request.tags or []),
                ]
            )
            previous_version = existing.metadata.get("version") if isinstance(existing.metadata, dict) else None
            try:
                previous_version_int = int(previous_version) if previous_version is not None else 1
            except Exception:
                previous_version_int = 1

            if changed:
                metadata = {**request_metadata, "version": previous_version_int + 1, "previous_fact_id": existing.fact_id}
            else:
                fact_id = existing.fact_id
                created_at = existing.created_at
                updated_at = now
                metadata = {**existing.metadata, **request_metadata}
                metadata.setdefault("version", previous_version_int)
        else:
            metadata = {**request_metadata}
            metadata.setdefault("version", 1)

        item = MemoryFactRecord(
            fact_id=fact_id,
            namespace=request.namespace,
            key=request.key,
            value=request.value,
            source=request.source,
            confidence=request.confidence,
            status=request.status,
            promotion_reason=request.promotion_reason,
            evidence=request.evidence,
            tags=request.tags,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
        )
        self.facts[(request.namespace, request.key)] = item
        return MemoryFactResponse(
            items=[item],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def query_facts(self, request: MemoryFactQueryRequest) -> MemoryFactResponse:
        items = []
        for (namespace, key), item in self.facts.items():
            if namespace != request.namespace:
                continue
            if request.key and key != request.key:
                continue
            if request.tags and not set(request.tags).issubset(set(item.tags)):
                continue
            items.append(item)
        return MemoryFactResponse(
            items=items[: request.limit],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def staging_stage(self, request: MemoryStagingStageRequest) -> MemoryStagingResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=request.run_id,
            source=request.source,
            context="memory.staging.stage",
        )
        now = datetime.now(UTC).isoformat()
        item = MemoryStagingRecord(
            staging_id=str(uuid4()),
            session_id=request.session_id,
            run_id=request.run_id,
            user_id=request.user_id,
            content=request.content,
            source=request.source,
            status=MemoryLifecycleStatus.staged,
            created_at=now,
            importance=request.importance,
            access_count=request.access_count,
            ttl_seconds=request.ttl_seconds,
            source_ids=request.source_ids,
            metadata=request.metadata,
        )
        self._staging_records_by_session.setdefault(request.session_id, []).append(item)
        _audit_memory_executed(operation="staging_stage", exit_code=0, traceability=traceability)
        return MemoryStagingResponse(
            items=[item],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def staging_list(self, request: MemoryStagingListRequest) -> MemoryStagingResponse:
        if request.session_id:
            records = list(self._staging_records_by_session.get(request.session_id, []))
        else:
            records = [item for items in self._staging_records_by_session.values() for item in items]

        if request.status is not None:
            records = [item for item in records if item.status == request.status]

        return MemoryStagingResponse(
            items=records[: request.limit],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def staging_touch(self, request: MemoryStagingTouchRequest) -> MemoryStagingResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=None,
            source=None,
            context="memory.staging.touch",
        )
        existing = list(self._staging_records_by_session.get(request.session_id, []))
        touch_ids = set(request.staging_ids) if request.staging_ids else {item.staging_id for item in existing}

        touched: List[MemoryStagingRecord] = []
        updated_records: List[MemoryStagingRecord] = []
        for item in existing:
            if item.staging_id not in touch_ids:
                updated_records.append(item)
                continue
            updated = item.model_copy(
                update={
                    "access_count": item.access_count + request.access_increment,
                    "metadata": {
                        **item.metadata,
                        "last_touch_reason": request.touch_reason,
                        **request.metadata,
                    },
                }
            )
            touched.append(updated)
            updated_records.append(updated)

        self._staging_records_by_session[request.session_id] = updated_records
        _audit_memory_executed(
            operation="staging_touch",
            exit_code=0,
            traceability=traceability,
            args=[f"touched={len(touched)}", f"access_increment={request.access_increment}"],
        )
        return MemoryStagingResponse(
            items=touched,
            status=MemoryServiceStatus(
                status="success",
                backend="memory-service",
                metadata={"touch_reason": request.touch_reason},
            ),
        )

    async def staging_discard(self, request: MemoryStagingDiscardRequest) -> MemoryStagingResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=None,
            source=None,
            context="memory.staging.discard",
        )
        existing = list(self._staging_records_by_session.get(request.session_id, []))
        if request.staging_ids:
            remove_set = set(request.staging_ids)
            discarded = [item for item in existing if item.staging_id in remove_set]
            kept = [item for item in existing if item.staging_id not in remove_set]
        else:
            discarded = existing
            kept = []

        self._staging_records_by_session[request.session_id] = kept
        _audit_memory_executed(
            operation="staging_discard",
            exit_code=0,
            traceability=traceability,
            args=[f"discarded={len(discarded)}"],
        )
        return MemoryStagingResponse(
            items=discarded,
            status=MemoryServiceStatus(
                status="success",
                backend="memory-service",
                metadata={"discard_reason": request.discard_reason},
            ),
        )

    async def dreaming_run(self, request: MemoryDreamingRunRequest) -> MemoryDreamingRunResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=None,
            source=None,
            context="memory.dreaming.run",
        )
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        self._dreaming_last_run_id = run_id
        self._dreaming_last_run_at = now
        self._dreaming_last_run_state = "running"

        if request.session_id:
            staged_records = list(self._staging_records_by_session.get(request.session_id, []))
        else:
            staged_records = [item for items in self._staging_records_by_session.values() for item in items]
        staged_records = [item for item in staged_records if item.status == MemoryLifecycleStatus.staged]
        selected = staged_records[: request.max_items]

        proposals: List[MemoryDreamingProposalRecord] = []
        for item in selected:
            proposal = MemoryDreamingProposalRecord(
                proposal_id=str(uuid4()),
                session_id=item.session_id,
                staging_id=item.staging_id,
                target_namespace="dreaming",
                target_key=f"candidate:{item.staging_id[:12]}",
                proposed_value=item.content,
                proposed_status=MemoryLifecycleStatus.candidate,
                promotion_reason="manual dreaming consolidation proposal",
                evidence=[
                    MemoryEvidence(
                        source="staging_signal",
                        confidence=item.importance,
                        reference=item.staging_id,
                        metadata={
                            "access_count": item.access_count,
                            "ttl_seconds": item.ttl_seconds,
                            "source_ids": item.source_ids,
                            "source": item.source,
                        },
                    )
                ],
                decision="pending",
                created_at=now,
                metadata={
                    "source": item.source,
                    "run_id": item.run_id,
                    "dry_run": request.dry_run,
                    "importance": item.importance,
                    "access_count": item.access_count,
                    "ttl_seconds": item.ttl_seconds,
                    "source_ids": item.source_ids,
                    **request.metadata,
                    "assurance_required": request.require_assurance_for_approval,
                },
            )
            proposals.append(proposal)

        if request.include_session_summary and request.session_id:
            history = await self.query_history(
                MemoryHistoryQueryRequest(
                    session_id=request.session_id,
                    limit=request.summary_max_messages,
                    include_tool_messages=False,
                )
            )
            if history.items:
                summary_text, source_ids = _build_session_summary_candidate(
                    session_id=request.session_id,
                    messages=history.items,
                    max_chars=request.summary_max_chars,
                )
                proposals.append(
                    MemoryDreamingProposalRecord(
                        proposal_id=str(uuid4()),
                        session_id=request.session_id,
                        staging_id=None,
                        target_namespace="dreaming",
                        target_key=f"session_summary:{request.session_id}:{run_id[:12]}",
                        proposed_value=summary_text,
                        proposed_status=MemoryLifecycleStatus.candidate,
                        promotion_reason="manual dreaming session summary proposal",
                        evidence=[
                            MemoryEvidence(
                                source="session_summary",
                                confidence=0.7,
                                reference=request.session_id,
                                metadata={
                                    "message_count": len(history.items),
                                    "source_ids": source_ids,
                                },
                            )
                        ],
                        decision="pending",
                        created_at=now,
                        metadata={
                            "source": "session_history",
                            "run_id": run_id,
                            "dry_run": request.dry_run,
                            "summary_source_ids": source_ids,
                            "summary_message_count": len(history.items),
                            **request.metadata,
                            "assurance_required": request.require_assurance_for_approval,
                        },
                    )
                )

        relation_evidence_summary = await _attach_dreaming_relation_evidence(self, proposals, request)
        quality_signals_summary = _attach_dreaming_quality_signals(proposals, request)

        if not request.dry_run:
            self._dreaming_proposals.extend(proposals)

        self._dreaming_last_run_state = "completed"
        _audit_memory_executed(
            operation="dreaming_run",
            exit_code=0,
            traceability={**traceability, "run_id": run_id},
            args=[f"proposals={len(proposals)}", f"dry_run={request.dry_run}"],
        )
        return MemoryDreamingRunResponse(
            run_id=run_id,
            trigger=request.trigger,
            proposals=proposals,
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary={
                "selected_staged_items": len(selected),
                "created_proposals": len(proposals),
                "dry_run": request.dry_run,
                "relation_evidence": relation_evidence_summary,
                "quality_signals": quality_signals_summary,
            },
        )

    async def dreaming_status(self) -> MemoryDreamingStatusResponse:
        pending_staged = sum(
            1
            for items in self._staging_records_by_session.values()
            for item in items
            if item.status == MemoryLifecycleStatus.staged
        )
        pending_proposals = sum(1 for item in self._dreaming_proposals if item.decision == "pending")
        return MemoryDreamingStatusResponse(
            scheduler_enabled=False,
            mode="manual_only",
            last_run_id=self._dreaming_last_run_id,
            last_run_at=self._dreaming_last_run_at,
            last_run_state=self._dreaming_last_run_state,
            pending_staged_items=pending_staged,
            pending_proposals=pending_proposals,
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def dreaming_proposals(self, request: MemoryDreamingProposalListRequest) -> MemoryDreamingProposalListResponse:
        items = list(self._dreaming_proposals)
        if request.session_id:
            items = [item for item in items if item.session_id == request.session_id]
        if request.decision != "all":
            items = [item for item in items if item.decision == request.decision]
        return MemoryDreamingProposalListResponse(
            items=items[: request.limit],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def dreaming_decide_proposal(self, request: MemoryDreamingProposalDecisionRequest) -> MemoryDreamingProposalDecisionResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=None,
            run_id=None,
            source=None,
            context="memory.dreaming.proposal.decision",
        )
        for index, proposal in enumerate(self._dreaming_proposals):
            if proposal.proposal_id != request.proposal_id:
                continue
            traceability["session_id"] = proposal.session_id

            if proposal.decision != "pending":
                _audit_memory_blocked(
                    operation="dreaming_decide_proposal",
                    reason="proposal_decision_immutable",
                    traceability=traceability,
                    args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
                )
                return MemoryDreamingProposalDecisionResponse(
                    item=proposal,
                    status=MemoryServiceStatus(
                        status="failed",
                        backend="memory-service",
                        degraded=True,
                        error="proposal_decision_immutable",
                    ),
                )

            if request.decision == "approved" and _proposal_assurance_blocks_approval(proposal):
                _audit_memory_blocked(
                    operation="dreaming_decide_proposal",
                    reason="proposal_assurance_not_passed",
                    traceability=traceability,
                    args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
                )
                return MemoryDreamingProposalDecisionResponse(
                    item=proposal,
                    status=MemoryServiceStatus(
                        status="failed",
                        backend="memory-service",
                        degraded=True,
                        error="proposal_assurance_not_passed",
                    ),
                )

            if request.decision == "approved" and proposal.proposed_status == MemoryLifecycleStatus.verified:
                if not can_set_verified(actor=request.decided_by, policy_exception=request.policy_exception):
                    _audit_memory_blocked(
                        operation="dreaming_decide_proposal",
                        reason="verified_requires_human_gate",
                        traceability=traceability,
                        args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
                    )
                    return MemoryDreamingProposalDecisionResponse(
                        item=proposal,
                        status=MemoryServiceStatus(
                            status="failed",
                            backend="memory-service",
                            degraded=True,
                            error="verified_requires_human_gate",
                        ),
                    )

            updated = proposal.model_copy(
                update={
                    "decision": request.decision,
                    "metadata": {
                        **proposal.metadata,
                        "decision_reason": request.decision_reason,
                        "decided_by": request.decided_by,
                        "policy_exception": request.policy_exception,
                        **request.metadata,
                        "decision_at": datetime.now(UTC).isoformat(),
                    },
                }
            )
            self._dreaming_proposals[index] = updated
            _audit_memory_executed(
                operation="dreaming_decide_proposal",
                exit_code=0,
                traceability=traceability,
                args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
            )
            return MemoryDreamingProposalDecisionResponse(
                item=updated,
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )

        _audit_memory_executed(
            operation="dreaming_decide_proposal",
            exit_code=1,
            traceability=traceability,
            args=[f"proposal_id={request.proposal_id}", "error=proposal_not_found"],
        )
        return MemoryDreamingProposalDecisionResponse(
            item=None,
            status=MemoryServiceStatus(
                status="failed",
                backend="memory-service",
                degraded=True,
                error="proposal_not_found",
            ),
        )

    async def dreaming_attach_assurance(self, request: MemoryDreamingProposalAssuranceRequest) -> MemoryDreamingProposalAssuranceResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=None,
            run_id=None,
            source="validator_report",
            context="memory.dreaming.proposal.assurance",
        )
        for index, proposal in enumerate(self._dreaming_proposals):
            if proposal.proposal_id != request.proposal_id:
                continue
            traceability["session_id"] = proposal.session_id
            if proposal.decision != "pending":
                error = "proposal_decision_immutable"
                break

            result = await self.validator_result(ValidatorResultRequest(job_id=request.validator_job_id))
            if result.status.status == "failed":
                error = result.status.error or "validator_job_unavailable"
                break
            updated, verdict, error = _proposal_with_validator_assurance(proposal, result, request)
            if error or updated is None:
                break

            self._dreaming_proposals[index] = updated
            _audit_memory_executed(
                operation="dreaming_attach_assurance",
                exit_code=0,
                traceability=traceability,
                args=[f"proposal_id={request.proposal_id}", f"job_id={request.validator_job_id}", f"verdict={verdict}"],
            )
            return MemoryDreamingProposalAssuranceResponse(
                item=updated,
                validator_job_id=request.validator_job_id,
                verdict=verdict,
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        else:
            error = "proposal_not_found"

        _audit_memory_blocked(
            operation="dreaming_attach_assurance",
            reason=error,
            traceability=traceability,
            args=[f"proposal_id={request.proposal_id}", f"job_id={request.validator_job_id}"],
        )
        return MemoryDreamingProposalAssuranceResponse(
            item=None,
            validator_job_id=request.validator_job_id,
            verdict="failed",
            status=MemoryServiceStatus(
                status="failed",
                backend="memory-service",
                degraded=True,
                error=error,
            ),
        )

    async def dreaming_cleanup(self, request: MemoryDreamingCleanupRequest) -> MemoryDreamingCleanupResponse:
        now = datetime.fromtimestamp(request.now_ts, UTC) if request.now_ts is not None else datetime.now(UTC)
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=None,
            source="retention_policy",
            context="memory.dreaming.cleanup",
        )
        scoped_proposals = [
            proposal
            for proposal in self._dreaming_proposals
            if request.session_id is None or proposal.session_id == request.session_id
        ]
        protected_staging_ids = {
            proposal.staging_id
            for proposal in scoped_proposals
            if proposal.staging_id and proposal.decision in {"pending", "approved"}
        }
        proposal_candidates = [
            proposal
            for proposal in scoped_proposals
            if _proposal_rejected_retention_expired(
                proposal,
                now=now,
                retention_seconds=request.rejected_retention_seconds,
            )
        ][: request.proposal_limit]

        staging_candidates: List[MemoryStagingRecord] = []
        for session_id, items in self._staging_records_by_session.items():
            if request.session_id is not None and session_id != request.session_id:
                continue
            for item in items:
                if item.staging_id in protected_staging_ids:
                    continue
                if _staging_retention_expired(item, now=now):
                    staging_candidates.append(item)
                    if len(staging_candidates) >= request.staging_limit:
                        break
            if len(staging_candidates) >= request.staging_limit:
                break

        staging_ids = [item.staging_id for item in staging_candidates]
        proposal_ids = [item.proposal_id for item in proposal_candidates]
        if not request.dry_run:
            staging_remove = set(staging_ids)
            for session_id, items in list(self._staging_records_by_session.items()):
                self._staging_records_by_session[session_id] = [
                    item for item in items if item.staging_id not in staging_remove
                ]
            proposal_remove = set(proposal_ids)
            self._dreaming_proposals = [
                proposal for proposal in self._dreaming_proposals if proposal.proposal_id not in proposal_remove
            ]

        _audit_memory_executed(
            operation="dreaming_cleanup",
            exit_code=0,
            traceability=traceability,
            args=[
                f"dry_run={request.dry_run}",
                f"staging_candidates={len(staging_ids)}",
                f"proposal_candidates={len(proposal_ids)}",
                f"reason={request.cleanup_reason}",
            ],
        )
        return MemoryDreamingCleanupResponse(
            dry_run=request.dry_run,
            staging_candidates=len(staging_ids),
            proposal_candidates=len(proposal_ids),
            staging_removed=0 if request.dry_run else len(staging_ids),
            proposals_removed=0 if request.dry_run else len(proposal_ids),
            staging_ids=staging_ids,
            proposal_ids=proposal_ids,
            policy={
                "staging": "expired_ttl_and_not_referenced_by_pending_or_approved_proposal",
                "proposals": "rejected_with_decision_at_older_than_retention",
                "rejected_retention_seconds": request.rejected_retention_seconds,
                "now": now.isoformat(),
            },
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def validator_submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        now = datetime.now(UTC).isoformat()
        job_id = str(uuid4())
        bound_proposal = next(
            (item for item in self._dreaming_proposals if item.proposal_id == request.proposal_id),
            None,
        )
        proposal_digest = _proposal_assurance_digest(bound_proposal) if bound_proposal is not None else None
        subject = _validator_subject_from_request(request, proposal_digest=proposal_digest)
        traceability = _memory_traceability(
            metadata={**request.metadata, "request_id": request.request_id, "context": request.context},
            session_id=request.session_id,
            run_id=request.run_id,
            source=request.source,
            context="memory.validator.submit",
        )
        self._validator_jobs[job_id] = {
            "job_id": job_id,
            "state": "queued",
            "workspace": request.workspace,
            "scope": request.scope,
            "checks": list(request.checks),
            "strict_mode": request.strict_mode,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
            "source": request.source,
            "context": request.context,
            "proposal_id": request.proposal_id,
            "proposal_digest": subject.proposal_digest,
            "subject": subject.model_dump(mode="json"),
            "created_at": now,
            "updated_at": now,
            "summary": {
                "execution_mode": _validator_execution_backend_name(),
                "execution_backend": _validator_execution_backend_name(),
                "state": "queued",
                "note": "validator job queued",
            },
            "findings": [],
            "artifacts": [],
        }
        async_mode = _validator_async_enabled()
        if async_mode:
            task = asyncio.create_task(self._run_validator_job_in_memory(job_id, traceability))
            self._validator_tasks[job_id] = task
        else:
            await self._run_validator_job_in_memory(job_id, traceability)

        _audit_memory_executed(
            operation="validator_submit",
            exit_code=0,
            traceability=traceability,
            args=[f"job_id={job_id}", f"scope={request.scope}", f"state=queued", f"async_mode={async_mode}"],
        )
        payload = self._validator_jobs[job_id]
        return ValidatorSubmitResponse(
            job_id=job_id,
            state=str(payload.get("state") or "queued"),
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary=dict(payload.get("summary") or {}),
            subject=_validator_subject_from_payload(payload),
        )

    async def validator_status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        payload = self._validator_jobs.get(request.job_id)
        if payload is None:
            return ValidatorStatusResponse(
                job_id=request.job_id,
                state="failed",
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="validator_job_not_found",
                ),
                summary={},
            )
        return ValidatorStatusResponse(
            job_id=request.job_id,
            state=payload["state"],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary=dict(payload.get("summary") or {}),
            subject=_validator_subject_from_payload(payload),
        )

    async def validator_result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        payload = self._validator_jobs.get(request.job_id)
        if payload is None:
            return ValidatorResultResponse(
                job_id=request.job_id,
                state="failed",
                findings=[],
                artifacts=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="validator_job_not_found",
                ),
                summary={},
            )

        findings = [ValidatorFinding(**item) for item in payload.get("findings", [])]
        return ValidatorResultResponse(
            job_id=request.job_id,
            state=payload["state"],
            findings=findings,
            artifacts=list(payload.get("artifacts") or []),
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary=dict(payload.get("summary") or {}),
            subject=_validator_subject_from_payload(payload),
        )

    async def upsert_retrieval(self, request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
        item = RetrievalDocument(
            document_id=request.document_id,
            content=request.content,
            score=1.0,
            source=request.source,
            chunk_index=request.metadata.get("chunk_index"),
            metadata=request.metadata,
        )
        self.retrieval_docs[request.document_id] = item
        return MemoryRetrievalResponse(
            items=[item],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="retrieval_fallback_in_memory",
                metadata={"deferred_backends": ["qdrant", "embedding"]},
            ),
        )

    async def query_retrieval(self, request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
        query_terms = self._tokenize(request.query)
        items = []
        for item in self.retrieval_docs.values():
            if request.filters and any(item.metadata.get(k) != v for k, v in request.filters.items()):
                continue
            score = self._score_content(item.content, query_terms)
            if request.min_score is not None and score < request.min_score:
                continue
            items.append(
                RetrievalDocument(
                    document_id=item.document_id,
                    content=item.content,
                    score=score,
                    source=item.source,
                    chunk_index=item.chunk_index,
                    metadata=item.metadata,
                )
            )
        items.sort(key=lambda entry: entry.score, reverse=True)
        return MemoryRetrievalResponse(
            items=items[: request.top_k],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="retrieval_fallback_in_memory",
                metadata={"deferred_backends": ["qdrant", "embedding"]},
            ),
        )

    async def generate_embedding(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        vector = self._embed_text(request.input_text, normalize=request.normalize)
        return MemoryEmbeddingResponse(
            item=EmbeddingVector(
                model=request.model or "memory-fallback-v1",
                dimensions=len(vector),
                vector=vector,
                metadata=request.metadata,
            ),
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="embedding_fallback_in_memory",
                metadata={"deferred_backends": ["embedding"]},
            ),
        )

    async def context_search(self, request: ContextSearchRequest) -> ContextSearchResponse:
        """In-memory fallback: keyword match within stored context docs."""
        query_terms = self._tokenize(request.query)
        hits: List[ContextDocument] = []
        for doc_id, item in self._context_docs.items():
            score = self._score_content(item["content"], query_terms)
            if request.min_score is not None and score < request.min_score:
                continue
            hits.append(
                ContextDocument(
                    document_id=doc_id,
                    content=item["content"],
                    score=score,
                    scope=item.get("scope", {}),
                    metadata=item.get("metadata", {}),
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return ContextSearchResponse(
            items=hits[: request.top_k],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="context_fallback_in_memory",
                metadata={"deferred_backends": ["chroma"]},
            ),
        )

    async def context_upsert(self, request: ContextUpsertRequest) -> ContextSearchResponse:
        """In-memory fallback: store context document."""
        policy_error = _context_upsert_policy_error(request)
        if policy_error:
            return ContextSearchResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=policy_error,
                    metadata=_context_upsert_policy_metadata(
                        request,
                        decision="blocked",
                        reason=policy_error,
                    ),
                ),
            )

        decision_meta = _context_upsert_policy_metadata(request, decision="allowed")
        enriched_doc_metadata = {**request.metadata, **decision_meta}
        self._context_docs[request.document_id] = {
            "content": request.content,
            "scope": request.scope.model_dump(exclude_none=True),
            "metadata": enriched_doc_metadata,
        }
        return ContextSearchResponse(
            items=[
                ContextDocument(
                    document_id=request.document_id,
                    content=request.content,
                    score=1.0,
                    scope=request.scope.model_dump(exclude_none=True),
                    metadata=enriched_doc_metadata,
                )
            ],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="context_fallback_in_memory",
                metadata={
                    "deferred_backends": ["chroma"],
                    **decision_meta,
                },
            ),
        )

    async def relation_upsert(self, request: RelationUpsertRequest) -> RelationExpandResponse:
        if not request.validated and not request.explicit_acceptance:
            return RelationExpandResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="relation_policy_violation: relation must be validated or explicitly accepted",
                ),
            )

        edge = RelationEdge(
            source=request.source,
            relation=request.relation,
            target=request.target,
            weight=request.weight,
            metadata=_relation_metadata_with_defaults(
                request.metadata,
                validated=request.validated,
                explicit_acceptance=request.explicit_acceptance,
                session_id=request.session_id,
                run_id=request.run_id,
            ),
        )
        self._relations.append(edge)
        self._relations = self._relations[-200:]
        return RelationExpandResponse(
            items=[edge],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="relation_fallback_in_memory",
                metadata={"deferred_backends": ["neo4j"]},
            ),
        )

    async def relation_expand(self, request: RelationExpandRequest) -> RelationExpandResponse:
        items = self._relations
        if request.session_id:
            items = [e for e in items if str(e.metadata.get("session_id") or "") == request.session_id]
        if request.run_id:
            items = [e for e in items if str(e.metadata.get("run_id") or "") == request.run_id]
        if request.query:
            q = request.query.lower().strip()
            items = [
                e for e in items
                if q in e.source.lower() or q in e.relation.lower() or q in e.target.lower()
            ]
        return RelationExpandResponse(
            items=items[: max(1, min(request.limit, 50))],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="relation_fallback_in_memory",
                metadata={"deferred_backends": ["neo4j"]},
            ),
        )

    async def relation_cleanup_expired(self, request: RelationCleanupExpiredRequest) -> RelationCleanupExpiredResponse:
        allowed, reason = MemoryLifecycleGovernance().cleanup_allowed(
            judge_decision=request.judge_decision,
            judge_confidence=request.judge_confidence,
        )
        if not allowed:
            return RelationCleanupExpiredResponse(
                removed=0,
                status=MemoryServiceStatus(
                    status="partial",
                    backend="memory-service",
                    degraded=True,
                    error="relation_cleanup_disabled_by_policy",
                    metadata={
                        "governance": "memory_lifecycle",
                        "governance_phase": "cleanup",
                        "governance_reason": reason,
                    },
                ),
            )
        now_ts = request.now_ts if request.now_ts is not None else datetime.now(UTC).timestamp()
        kept: list[RelationEdge] = []
        removed = 0
        for edge in self._relations:
            edge_session = str(edge.metadata.get("session_id") or "")
            edge_run = str(edge.metadata.get("run_id") or "")
            if request.session_id and edge_session != request.session_id:
                kept.append(edge)
                continue
            if request.run_id and edge_run != request.run_id:
                kept.append(edge)
                continue

            valid_until = edge.metadata.get("valid_until_ts")
            try:
                valid_until_ts = float(valid_until) if valid_until is not None else None
            except (TypeError, ValueError):
                valid_until_ts = None
            is_ephemeral = bool(edge.metadata.get("ephemeral", False))
            is_expired = valid_until_ts is not None and valid_until_ts <= float(now_ts)
            if is_ephemeral and is_expired:
                removed += 1
                continue
            kept.append(edge)
        self._relations = kept
        return RelationCleanupExpiredResponse(
            removed=removed,
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="relation_fallback_in_memory",
                metadata={
                    "deferred_backends": ["neo4j"],
                    "scope_session_id": request.session_id,
                    "scope_run_id": request.run_id,
                    "now_ts": now_ts,
                },
            ),
        )

    async def health(self) -> MemoryHealthResponse:
        backend_health: Dict[str, Literal["healthy", "degraded", "unavailable"]] = {
            "redis": "unavailable",
            "postgres": "unavailable",
            "qdrant": "unavailable",
            "neo4j": "unavailable",
            "chroma": "unavailable",
            "embedding": "unavailable",
        }
        return MemoryHealthResponse(
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="fallback_in_memory_store",
                metadata={
                    "fallback_mode": "in-memory",
                    "deferred_backends": ["qdrant", "neo4j", "chroma", "embedding"],
                },
            ),
            backend_health=backend_health,
        )

    async def health_backends(self) -> MemoryHealthResponse:
        return await self.health()

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {part for part in text.lower().split() if part}

    @classmethod
    def _score_content(cls, content: str, query_terms: set[str]) -> float:
        if not query_terms:
            return 0.0
        content_terms = cls._tokenize(content)
        if not content_terms:
            return 0.0
        overlap = len(content_terms.intersection(query_terms))
        return overlap / len(query_terms)

    @classmethod
    def _embed_text(cls, text: str, *, normalize: bool) -> list[float]:
        terms = sorted(cls._tokenize(text))
        if not terms:
            return [0.0, 0.0, 0.0, 0.0]
        buckets = [0.0, 0.0, 0.0, 0.0]
        for index, term in enumerate(terms):
            buckets[index % 4] += sum(ord(char) for char in term) / 1000.0
        if normalize:
            magnitude = sum(value * value for value in buckets) ** 0.5
            if magnitude:
                buckets = [value / magnitude for value in buckets]
        return [round(value, 6) for value in buckets]


