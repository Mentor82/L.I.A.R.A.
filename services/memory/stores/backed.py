"""Production backing store for liara-memory wrapping real storage layers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import inspect
import json
import os
import re
import time
from typing import Any, Dict, List, Literal, Tuple
from uuid import uuid4

import httpx

from services.config import Settings
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
    MemoryEvidence,
    MemoryEmbeddingRequest,
    MemoryEmbeddingResponse,
    MemoryFactQueryRequest,
    MemoryFactRecord,
    MemoryFactResponse,
    MemoryFactUpsertRequest,
    MemoryHealthResponse,
    MemoryHistoryAppendRequest,
    MemoryHistoryQueryRequest,
    MemoryHistoryResponse,
    MemoryMessageRecord,
    MemoryRetrievalQueryRequest,
    MemoryRetrievalResponse,
    MemoryRetrievalUpsertRequest,
    MemoryLifecycleStatus,
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
from services.memory.chunking import chunk_text_by_tokens, load_retrieval_chunking_config
from services.memory.governance import MemoryLifecycleGovernance
from services.memory.tier_store import ContextStore, FactStore, MemoryLayer, RetrievalIndex, SessionStore
from services.memory.stores.base import (
    EphemeralMemoryStore,
    MemoryServiceStore,
    NullMemoryStore,
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
from services.memory.stores.in_memory import InMemoryMemoryServiceStore
from services.memory_adapter import InProcessMemoryAdapter
from services.workspace import persist_memory_consolidation


class BackedMemoryServiceStore(MemoryServiceStore):
    """Service store backed by the real SessionStore and FactStore implementations."""

    def __init__(
        self,
        *,
        session_store: SessionStore | None = None,
        fact_store: FactStore | None = None,
        retrieval_index: RetrievalIndex | None = None,
        context_store: ContextStore | None = None,
        graph_store: Any | None = None,
        embedding_service_base_url: str | None = None,
        embedding_service_timeout_seconds: float | None = None,
        embedding_client: Any = None,
    ):
        self.session_store = session_store or SessionStore()
        self.fact_store = fact_store or FactStore()
        self.retrieval_index = retrieval_index
        self._uses_qdrant = retrieval_index is not None or bool(Settings.QDRANT_URL)
        if self._uses_qdrant and self.retrieval_index is None:
            self.retrieval_index = RetrievalIndex()
        self.retrieval_store = self.retrieval_index or EphemeralMemoryStore()

        # Chroma context store — optional; only active when CHROMA_HOST is set.
        self.context_store = context_store
        if self.context_store is None and Settings.CHROMA_HOST:
            try:
                self.context_store = ContextStore()
            except Exception:
                self.context_store = None
        self.graph_store = graph_store
        if self.graph_store is None and Settings.NEO4J_URL:
            try:
                from services.memory.tier_store import GraphStore

                self.graph_store = GraphStore()
            except Exception:
                self.graph_store = None
        configured_embedding_base_url = os.getenv("EMBEDDING_SERVICE_BASE_URL")
        self.embedding_service_base_url = (
            embedding_service_base_url
            or configured_embedding_base_url
        )
        self.embedding_service_timeout_seconds = (
            embedding_service_timeout_seconds
            if embedding_service_timeout_seconds is not None
            else float(os.getenv("EMBEDDING_SERVICE_TIMEOUT_SECONDS", "10"))
        )
        self.embedding_client = embedding_client
        self._context_fallback_store = InMemoryMemoryServiceStore()
        self._relation_fallback_store = InMemoryMemoryServiceStore()
        self.memory_layer = MemoryLayer(
            session_store=self.session_store,
            fact_store=self.fact_store,
            retrieval_index=self.retrieval_store,
            graph_store=self.graph_store or NullMemoryStore(),
        )
        self.adapter = InProcessMemoryAdapter(self.memory_layer)
        self.lifecycle_governance = MemoryLifecycleGovernance()

    async def _maybe_link_context_scope(self, *, request: ContextUpsertRequest, metadata: Dict[str, Any]) -> None:
        if not self.lifecycle_governance.phase_enabled("scope_link"):
            return
        if self.graph_store is None or not hasattr(self.graph_store, "relation_upsert"):
            return

        scope_expiry = self.lifecycle_governance.scope_relation_expiry(metadata)
        relation_metadata = {
            "kind": "context_scope",
            "scope": "session",
            "persistable": False,
            "ephemeral": True,
            "ttl_seconds": metadata.get("ttl_seconds"),
            "governance": "memory_lifecycle",
            "governance_edge_type": "scope_link",
        }
        if scope_expiry is not None:
            relation_metadata["valid_until_ts"] = scope_expiry

        context_node = f"context:{request.document_id}"
        if request.scope.session_id:
            await self.graph_store.relation_upsert(
                source=f"session:{request.scope.session_id}",
                relation="PART_OF",
                target=context_node,
                session_id=request.scope.session_id,
                run_id=request.scope.run_id,
                weight=1.0,
                metadata=relation_metadata,
            )
        if request.scope.run_id:
            await self.graph_store.relation_upsert(
                source=f"run:{request.scope.run_id}",
                relation="PART_OF",
                target=context_node,
                session_id=request.scope.session_id,
                run_id=request.scope.run_id,
                weight=1.0,
                metadata=relation_metadata,
            )

    async def _maybe_promote_context(self, *, request: ContextUpsertRequest, metadata: Dict[str, Any]) -> Dict[str, Any]:
        decision = self.lifecycle_governance.decide_promotion(request, metadata)
        result: Dict[str, Any] = decision.as_metadata()

        if not decision.should_promote:
            return result
        if self.retrieval_index is None:
            result["promotion_result"] = "skipped_no_qdrant"
            return result

        retrieval_id = f"context:{request.document_id}"
        retrieval_response = await self.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id=retrieval_id,
                content=request.content,
                source="context_promotion",
                embedding=request.embedding,
                metadata={
                    **metadata,
                    "governance": "memory_lifecycle",
                    "governance_promoted_from": "context",
                    "retrieval_doc_root_id": retrieval_id,
                },
            )
        )
        result["promotion_result"] = retrieval_response.status.status
        result["promotion_backend"] = retrieval_response.status.backend
        result["promoted_retrieval_document_id"] = retrieval_id

        if (
            retrieval_response.status.status != "failed"
            and self.graph_store is not None
            and hasattr(self.graph_store, "relation_upsert")
        ):
            stable_edge_metadata = _relation_metadata_with_defaults(
                {
                    "kind": "context_promotion",
                    "scope": "long_term",
                    "persistable": True,
                    "ephemeral": False,
                    "governance": "memory_lifecycle",
                    "governance_edge_type": "promotion_link",
                },
                validated=True,
                explicit_acceptance=True,
                session_id=request.scope.session_id,
                run_id=request.scope.run_id,
            )
            await self.graph_store.relation_upsert(
                source=f"context:{request.document_id}",
                relation="REFERENCES",
                target=f"retrieval:{retrieval_id}",
                session_id=request.scope.session_id,
                run_id=request.scope.run_id,
                weight=1.0,
                metadata=stable_edge_metadata,
            )

        return result

    @staticmethod
    def _extract_pattern_terms(content: str) -> List[str]:
        stop_words = {
            "this", "that", "with", "from", "into", "over", "under", "they", "them", "were",
            "have", "has", "will", "would", "shall", "could", "there", "their", "about", "which",
            "while", "where", "what", "when", "how", "fuer", "eine", "einer", "einem", "einen",
            "dies", "diese", "dieser", "dieses", "oder", "aber", "weil", "dann", "noch", "auch",
        }
        tokens = [tok.lower() for tok in re.findall(r"[a-zA-Z0-9_]{4,}", content or "")]
        seen: set[str] = set()
        terms: List[str] = []
        for token in tokens:
            if token in stop_words or token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= 8:
                break
        return terms

    async def _learn_cross_session_pattern(self, *, request: ContextUpsertRequest, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if not self.lifecycle_governance.phase_enabled("pattern_learning"):
            return {
                "pattern_learning_enabled": False,
                "pattern_learning_status": "disabled",
            }
        if not request.scope.session_id:
            return {
                "pattern_learning_enabled": True,
                "pattern_learning_status": "skipped_no_session_scope",
            }

        terms = self._extract_pattern_terms(request.content)
        if len(terms) < 3:
            return {
                "pattern_learning_enabled": True,
                "pattern_learning_status": "skipped_insufficient_terms",
            }

        abstraction_terms = terms[:5]
        abstraction = "Pattern: " + ", ".join(abstraction_terms)
        pattern_basis = "|".join(abstraction_terms)
        pattern_id = hashlib.sha1(pattern_basis.encode("utf-8")).hexdigest()[:12]
        pattern_key = f"context_pattern:{pattern_id}"

        existing = await self.fact_store.get(
            pattern_key,
            default={
                "pattern_id": pattern_id,
                "pattern_basis": pattern_basis,
                "abstraction": abstraction,
                "occurrences": 0,
                "sessions": [],
                "sample_document_ids": [],
                "last_seen": None,
            },
        )
        record = dict(existing or {})
        sessions = list(record.get("sessions") or [])
        session_id = str(request.scope.session_id)
        if session_id not in sessions:
            sessions.append(session_id)
        sample_docs = list(record.get("sample_document_ids") or [])
        if request.document_id not in sample_docs:
            sample_docs.append(request.document_id)

        record.update(
            {
                "pattern_id": pattern_id,
                "pattern_basis": pattern_basis,
                "abstraction": abstraction,
                "occurrences": int(record.get("occurrences") or 0) + 1,
                "sessions": sessions[-20:],
                "sample_document_ids": sample_docs[-20:],
                "last_seen": datetime.now(UTC).isoformat(),
                "source": str(metadata.get("source") or "context_upsert"),
            }
        )
        await self.fact_store.set(pattern_key, record)

        cross_session_count = len(record.get("sessions") or [])
        return {
            "pattern_learning_enabled": True,
            "pattern_learning_status": "updated",
            "pattern_id": pattern_id,
            "pattern_abstraction": abstraction,
            "pattern_occurrences": int(record.get("occurrences") or 0),
            "pattern_cross_session_count": cross_session_count,
        }

    async def append_history(self, request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
        primary = await self.adapter.append_history(request)
        if primary.status.status != "failed":
            return primary

        fallback = await self._context_fallback_store.append_history(request)
        return MemoryHistoryResponse(
            items=fallback.items,
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error=primary.status.error or "history_backend_unavailable",
                metadata={
                    "fallback_backend": "in-memory",
                    "fallback_operation": "append_history",
                    "primary_backend": primary.status.backend,
                    "primary_status": primary.status.status,
                },
            ),
        )

    async def query_history(self, request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
        primary = await self.adapter.query_history(request)
        if primary.status.status != "failed":
            return primary

        fallback = await self._context_fallback_store.query_history(request)
        return MemoryHistoryResponse(
            items=fallback.items,
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error=primary.status.error or "history_backend_unavailable",
                metadata={
                    "fallback_backend": "in-memory",
                    "fallback_operation": "query_history",
                    "primary_backend": primary.status.backend,
                    "primary_status": primary.status.status,
                },
            ),
        )

    async def upsert_fact(self, request: MemoryFactUpsertRequest) -> MemoryFactResponse:
        return await self.adapter.upsert_fact(request)

    async def query_facts(self, request: MemoryFactQueryRequest) -> MemoryFactResponse:
        return await self.adapter.query_facts(request)

    async def staging_stage(self, request: MemoryStagingStageRequest) -> MemoryStagingResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=request.run_id,
            source=request.source,
            context="memory.staging.stage",
        )
        now = datetime.now(UTC).isoformat()
        record = MemoryStagingRecord(
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
        await self.fact_store.set(self._staging_record_key(record.staging_id), record.model_dump(mode="json"))
        await self._append_unique_index_id(self._staging_session_index_key(request.session_id), record.staging_id)
        await self._append_unique_index_id(self._staging_all_index_key(), record.staging_id)
        _audit_memory_executed(operation="staging_stage", exit_code=0, traceability=traceability)
        return MemoryStagingResponse(
            items=[record],
            status=MemoryServiceStatus(status="success", backend="postgres"),
        )

    async def staging_list(self, request: MemoryStagingListRequest) -> MemoryStagingResponse:
        index_key = (
            self._staging_session_index_key(request.session_id)
            if request.session_id
            else self._staging_all_index_key()
        )
        record_ids = await self._get_index_ids(index_key)
        items: List[MemoryStagingRecord] = []
        for staging_id in record_ids:
            payload = await self.fact_store.get(self._staging_record_key(staging_id), default=None)
            if payload is None:
                continue
            try:
                record = MemoryStagingRecord(**payload)
            except Exception:
                continue
            if request.status is not None and record.status != request.status:
                continue
            items.append(record)
            if len(items) >= request.limit:
                break

        return MemoryStagingResponse(
            items=items,
            status=MemoryServiceStatus(status="success", backend="postgres"),
        )

    async def staging_touch(self, request: MemoryStagingTouchRequest) -> MemoryStagingResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=request.session_id,
            run_id=None,
            source=None,
            context="memory.staging.touch",
        )
        session_index_key = self._staging_session_index_key(request.session_id)
        session_ids = await self._get_index_ids(session_index_key)
        touch_ids = set(request.staging_ids) if request.staging_ids else set(session_ids)
        touch_ids = {staging_id for staging_id in session_ids if staging_id in touch_ids}

        touched: List[MemoryStagingRecord] = []
        for staging_id in session_ids:
            if staging_id not in touch_ids:
                continue
            payload = await self.fact_store.get(self._staging_record_key(staging_id), default=None)
            if payload is None:
                continue
            try:
                record = MemoryStagingRecord(**payload)
            except Exception:
                continue
            updated = record.model_copy(
                update={
                    "access_count": record.access_count + request.access_increment,
                    "metadata": {
                        **record.metadata,
                        "last_touch_reason": request.touch_reason,
                        **request.metadata,
                    },
                }
            )
            await self.fact_store.set(self._staging_record_key(staging_id), updated.model_dump(mode="json"))
            touched.append(updated)

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
                backend="postgres",
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
        session_index_key = self._staging_session_index_key(request.session_id)
        session_ids = await self._get_index_ids(session_index_key)
        remove_ids = set(request.staging_ids) if request.staging_ids else set(session_ids)
        remove_ids = {staging_id for staging_id in session_ids if staging_id in remove_ids}

        discarded: List[MemoryStagingRecord] = []
        for staging_id in session_ids:
            if staging_id not in remove_ids:
                continue
            payload = await self.fact_store.get(self._staging_record_key(staging_id), default=None)
            if payload is not None:
                try:
                    discarded.append(MemoryStagingRecord(**payload))
                except Exception:
                    pass
            await self.fact_store.delete(self._staging_record_key(staging_id))

        await self._remove_index_ids(session_index_key, remove_ids)
        await self._remove_index_ids(self._staging_all_index_key(), remove_ids)
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
                backend="postgres",
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
        await self._set_dreaming_status(
            {
                "scheduler_enabled": False,
                "mode": "manual_only",
                "last_run_id": run_id,
                "last_run_at": now,
                "last_run_state": "running",
            }
        )

        staged = await self.staging_list(
            MemoryStagingListRequest(
                session_id=request.session_id,
                status=MemoryLifecycleStatus.staged,
                limit=request.max_items,
            )
        )
        selected = staged.items[: request.max_items]

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
            for proposal in proposals:
                await self.fact_store.set(
                    self._dreaming_proposal_key(proposal.proposal_id),
                    proposal.model_dump(mode="json"),
                )
                await self._append_unique_index_id(
                    self._dreaming_proposals_session_index_key(proposal.session_id),
                    proposal.proposal_id,
                )
                await self._append_unique_index_id(self._dreaming_proposals_all_index_key(), proposal.proposal_id)

        await self._set_dreaming_status(
            {
                "scheduler_enabled": False,
                "mode": "manual_only",
                "last_run_id": run_id,
                "last_run_at": now,
                "last_run_state": "completed",
            }
        )
        _audit_memory_executed(
            operation="dreaming_run",
            exit_code=0,
            traceability={**traceability, "run_id": run_id},
            args=[f"proposals={len(proposals)}", f"dry_run={request.dry_run}"],
        )
        
        # Persist memory consolidation to workspace
        artifact_persistence: dict[str, Any] | None = None
        if not request.dry_run and proposals:
            try:
                artifact_path = await asyncio.to_thread(
                    persist_memory_consolidation,
                    dreaming_run_id=run_id,
                    proposals=[p.model_dump(mode="json") for p in proposals],
                    verified_facts=[],  # Populated later when decisions are made
                    session_id=request.session_id,
                    request_id=traceability.get("request_id"),
                    run_id=run_id,
                    source=traceability.get("source") or "memory.dreaming",
                )
                artifact_persistence = {"status": "verified", "path": str(artifact_path)}
            except Exception as exc:
                artifact_persistence = {"status": "failed", "error": str(exc)}
        
        return MemoryDreamingRunResponse(
            run_id=run_id,
            trigger=request.trigger,
            proposals=proposals,
            status=MemoryServiceStatus(status="success", backend="postgres"),
            summary={
                "selected_staged_items": len(selected),
                "created_proposals": len(proposals),
                "dry_run": request.dry_run,
                "relation_evidence": relation_evidence_summary,
                "quality_signals": quality_signals_summary,
                "artifact_persistence": artifact_persistence,
            },
        )

    async def dreaming_status(self) -> MemoryDreamingStatusResponse:
        status_payload = await self.fact_store.get(self._dreaming_status_key(), default={})
        staged_ids = await self._get_index_ids(self._staging_all_index_key())
        proposal_ids = await self._get_index_ids(self._dreaming_proposals_all_index_key())

        pending_staged = 0
        for staging_id in staged_ids:
            payload = await self.fact_store.get(self._staging_record_key(staging_id), default=None)
            if payload is None:
                continue
            try:
                record = MemoryStagingRecord(**payload)
            except Exception:
                continue
            if record.status == MemoryLifecycleStatus.staged:
                pending_staged += 1

        pending_proposals = 0
        for proposal_id in proposal_ids:
            payload = await self.fact_store.get(self._dreaming_proposal_key(proposal_id), default=None)
            if payload is None:
                continue
            try:
                proposal = MemoryDreamingProposalRecord(**payload)
            except Exception:
                continue
            if proposal.decision == "pending":
                pending_proposals += 1

        return MemoryDreamingStatusResponse(
            scheduler_enabled=False,
            mode="manual_only",
            last_run_id=status_payload.get("last_run_id"),
            last_run_at=status_payload.get("last_run_at"),
            last_run_state=status_payload.get("last_run_state", "idle"),
            pending_staged_items=pending_staged,
            pending_proposals=pending_proposals,
            status=MemoryServiceStatus(status="success", backend="postgres"),
        )

    async def dreaming_proposals(self, request: MemoryDreamingProposalListRequest) -> MemoryDreamingProposalListResponse:
        index_key = (
            self._dreaming_proposals_session_index_key(request.session_id)
            if request.session_id
            else self._dreaming_proposals_all_index_key()
        )
        proposal_ids = await self._get_index_ids(index_key)
        items: List[MemoryDreamingProposalRecord] = []
        for proposal_id in proposal_ids:
            payload = await self.fact_store.get(self._dreaming_proposal_key(proposal_id), default=None)
            if payload is None:
                continue
            try:
                proposal = MemoryDreamingProposalRecord(**payload)
            except Exception:
                continue
            if request.decision != "all" and proposal.decision != request.decision:
                continue
            items.append(proposal)
            if len(items) >= request.limit:
                break
        return MemoryDreamingProposalListResponse(
            items=items,
            status=MemoryServiceStatus(status="success", backend="postgres"),
        )

    async def dreaming_decide_proposal(self, request: MemoryDreamingProposalDecisionRequest) -> MemoryDreamingProposalDecisionResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=None,
            run_id=None,
            source=None,
            context="memory.dreaming.proposal.decision",
        )
        payload = await self.fact_store.get(self._dreaming_proposal_key(request.proposal_id), default=None)
        if payload is None:
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
                    backend="postgres",
                    degraded=True,
                    error="proposal_not_found",
                ),
            )

        try:
            proposal = MemoryDreamingProposalRecord(**payload)
        except Exception:
            return MemoryDreamingProposalDecisionResponse(
                item=None,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="postgres",
                    degraded=True,
                    error="proposal_payload_invalid",
                ),
            )

        if proposal.decision != "pending":
            _audit_memory_blocked(
                operation="dreaming_decide_proposal",
                reason="proposal_decision_immutable",
                traceability={**traceability, "session_id": proposal.session_id},
                args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
            )
            return MemoryDreamingProposalDecisionResponse(
                item=proposal,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="postgres",
                    degraded=True,
                    error="proposal_decision_immutable",
                ),
            )

        if request.decision == "approved" and _proposal_assurance_blocks_approval(proposal):
            _audit_memory_blocked(
                operation="dreaming_decide_proposal",
                reason="proposal_assurance_not_passed",
                traceability={**traceability, "session_id": proposal.session_id},
                args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
            )
            return MemoryDreamingProposalDecisionResponse(
                item=proposal,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="postgres",
                    degraded=True,
                    error="proposal_assurance_not_passed",
                ),
            )

        if request.decision == "approved" and proposal.proposed_status == MemoryLifecycleStatus.verified:
            if not can_set_verified(actor=request.decided_by, policy_exception=request.policy_exception):
                _audit_memory_blocked(
                    operation="dreaming_decide_proposal",
                    reason="verified_requires_human_gate",
                    traceability={**traceability, "session_id": proposal.session_id},
                    args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
                )
                return MemoryDreamingProposalDecisionResponse(
                    item=proposal,
                    status=MemoryServiceStatus(
                        status="failed",
                        backend="postgres",
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
        await self.fact_store.set(self._dreaming_proposal_key(request.proposal_id), updated.model_dump(mode="json"))
        _audit_memory_executed(
            operation="dreaming_decide_proposal",
            exit_code=0,
            traceability={**traceability, "session_id": proposal.session_id},
            args=[f"proposal_id={request.proposal_id}", f"decision={request.decision}"],
        )
        return MemoryDreamingProposalDecisionResponse(
            item=updated,
            status=MemoryServiceStatus(status="success", backend="postgres"),
        )

    async def dreaming_attach_assurance(self, request: MemoryDreamingProposalAssuranceRequest) -> MemoryDreamingProposalAssuranceResponse:
        traceability = _memory_traceability(
            metadata=request.metadata,
            session_id=None,
            run_id=None,
            source="validator_report",
            context="memory.dreaming.proposal.assurance",
        )
        payload = await self.fact_store.get(self._dreaming_proposal_key(request.proposal_id), default=None)
        if payload is None:
            error = "proposal_not_found"
            proposal = None
        else:
            try:
                proposal = MemoryDreamingProposalRecord(**payload)
            except Exception:
                proposal = None
                error = "proposal_payload_invalid"

        if proposal is not None:
            traceability["session_id"] = proposal.session_id
            if proposal.decision != "pending":
                error = "proposal_decision_immutable"
            else:
                result = await self.validator_result(ValidatorResultRequest(job_id=request.validator_job_id))
                if result.status.status == "failed":
                    error = result.status.error or "validator_job_unavailable"
                else:
                    updated, verdict, error = _proposal_with_validator_assurance(proposal, result, request)
                    if not error and updated is not None:
                        await self.fact_store.set(
                            self._dreaming_proposal_key(request.proposal_id),
                            updated.model_dump(mode="json"),
                        )
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
                            status=MemoryServiceStatus(status="success", backend="postgres"),
                        )

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
                backend="postgres",
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

        proposal_index_key = (
            self._dreaming_proposals_session_index_key(request.session_id)
            if request.session_id
            else self._dreaming_proposals_all_index_key()
        )
        proposal_index_ids = await self._get_index_ids(proposal_index_key)
        scoped_proposals: List[MemoryDreamingProposalRecord] = []
        for proposal_id in proposal_index_ids:
            payload = await self.fact_store.get(self._dreaming_proposal_key(proposal_id), default=None)
            if not isinstance(payload, dict):
                continue
            try:
                scoped_proposals.append(MemoryDreamingProposalRecord(**payload))
            except Exception:
                continue

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

        staging_index_key = (
            self._staging_session_index_key(request.session_id)
            if request.session_id
            else self._staging_all_index_key()
        )
        staging_index_ids = await self._get_index_ids(staging_index_key)
        staging_candidates: List[MemoryStagingRecord] = []
        for staging_id in staging_index_ids:
            if staging_id in protected_staging_ids:
                continue
            payload = await self.fact_store.get(self._staging_record_key(staging_id), default=None)
            if not isinstance(payload, dict):
                continue
            try:
                record = MemoryStagingRecord(**payload)
            except Exception:
                continue
            if _staging_retention_expired(record, now=now):
                staging_candidates.append(record)
                if len(staging_candidates) >= request.staging_limit:
                    break

        staging_ids = [item.staging_id for item in staging_candidates]
        proposal_ids = [item.proposal_id for item in proposal_candidates]
        if not request.dry_run:
            staging_remove = set(staging_ids)
            for item in staging_candidates:
                await self.fact_store.delete(self._staging_record_key(item.staging_id))
                await self._remove_index_ids(self._staging_session_index_key(item.session_id), staging_remove)
            await self._remove_index_ids(self._staging_all_index_key(), staging_remove)

            proposal_remove = set(proposal_ids)
            for item in proposal_candidates:
                await self.fact_store.delete(self._dreaming_proposal_key(item.proposal_id))
                await self._remove_index_ids(
                    self._dreaming_proposals_session_index_key(item.session_id),
                    proposal_remove,
                )
            await self._remove_index_ids(self._dreaming_proposals_all_index_key(), proposal_remove)

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
            status=MemoryServiceStatus(status="success", backend="postgres"),
        )

    async def validator_submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        now = datetime.now(UTC).isoformat()
        job_id = str(uuid4())
        bound_payload = (
            await self.fact_store.get(self._dreaming_proposal_key(request.proposal_id), default=None)
            if request.proposal_id
            else None
        )
        try:
            bound_proposal = MemoryDreamingProposalRecord(**bound_payload) if isinstance(bound_payload, dict) else None
        except Exception:
            bound_proposal = None
        proposal_digest = _proposal_assurance_digest(bound_proposal) if bound_proposal is not None else None
        subject = _validator_subject_from_request(request, proposal_digest=proposal_digest)
        traceability = _memory_traceability(
            metadata={**request.metadata, "request_id": request.request_id, "context": request.context},
            session_id=request.session_id,
            run_id=request.run_id,
            source=request.source,
            context="memory.validator.submit",
        )
        payload = {
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
        await self.fact_store.set(self._validator_job_key(job_id), payload)
        await self._append_unique_index_id(self._validator_jobs_index_key(), job_id)
        async_mode = _validator_async_enabled()
        if async_mode:
            asyncio.create_task(self._run_validator_job_backed(job_id, traceability))
        else:
            await self._run_validator_job_backed(job_id, traceability)

        _audit_memory_executed(
            operation="validator_submit",
            exit_code=0,
            traceability=traceability,
            args=[f"job_id={job_id}", f"scope={request.scope}", "state=queued", f"async_mode={async_mode}"],
        )
        current_payload = await self.fact_store.get(self._validator_job_key(job_id), default=payload)
        return ValidatorSubmitResponse(
            job_id=job_id,
            state=str((current_payload or {}).get("state") or "queued"),
            status=MemoryServiceStatus(status="success", backend="postgres"),
            summary=dict((current_payload or {}).get("summary") or {}),
            subject=_validator_subject_from_payload(current_payload or payload),
        )

    async def _run_validator_job_backed(self, job_id: str, traceability: dict[str, Any]) -> None:
        payload = await self.fact_store.get(self._validator_job_key(job_id), default=None)
        if not isinstance(payload, dict):
            return

        payload["state"] = "running"
        payload["updated_at"] = datetime.now(UTC).isoformat()
        await self.fact_store.set(self._validator_job_key(job_id), payload)

        exec_fn = _get_store_symbol("_execute_validator_job", _execute_validator_job)
        try:
            execution = await asyncio.to_thread(
                exec_fn,
                job_id=job_id,
                workspace=str(payload.get("workspace") or ""),
                scope=str(payload.get("scope") or "quick"),
                checks=list(payload.get("checks") or []),
                strict_mode=bool(payload.get("strict_mode", False)),
                session_id=traceability.get("session_id"),
                request_id=traceability.get("request_id"),
                run_id=traceability.get("run_id"),
                source=traceability.get("source") or "memory.validator",
            )
        except Exception as exc:
            execution = {
                "state": "failed",
                "summary": {
                    "execution_mode": _validator_execution_backend_name(),
                    "execution_backend": _validator_execution_backend_name(),
                    "error": f"validator_execution_exception: {exc}",
                },
                "findings": [{"severity": "error", "message": str(exc)}],
                "artifacts": [],
            }

        payload["state"] = execution["state"]
        payload["summary"] = execution["summary"]
        payload["findings"] = execution["findings"]
        payload["artifacts"] = execution["artifacts"]
        payload["updated_at"] = datetime.now(UTC).isoformat()
        await self.fact_store.set(self._validator_job_key(job_id), payload)

        exit_code = 0 if execution["state"] == "completed" else 1
        _audit_memory_executed(
            operation="validator_execute",
            exit_code=exit_code,
            traceability=traceability,
            args=[f"job_id={job_id}", f"state={execution['state']}"],
        )

    async def validator_status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        payload = await self.fact_store.get(self._validator_job_key(request.job_id), default=None)
        if payload is None:
            return ValidatorStatusResponse(
                job_id=request.job_id,
                state="failed",
                status=MemoryServiceStatus(
                    status="failed",
                    backend="postgres",
                    degraded=True,
                    error="validator_job_not_found",
                ),
                summary={},
            )
        return ValidatorStatusResponse(
            job_id=request.job_id,
            state=str(payload.get("state") or "failed"),
            status=MemoryServiceStatus(status="success", backend="postgres"),
            summary=dict(payload.get("summary") or {}),
            subject=_validator_subject_from_payload(payload),
        )

    async def validator_result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        payload = await self.fact_store.get(self._validator_job_key(request.job_id), default=None)
        if payload is None:
            return ValidatorResultResponse(
                job_id=request.job_id,
                state="failed",
                findings=[],
                artifacts=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="postgres",
                    degraded=True,
                    error="validator_job_not_found",
                ),
                summary={},
            )

        findings = [ValidatorFinding(**item) for item in list(payload.get("findings") or [])]
        return ValidatorResultResponse(
            job_id=request.job_id,
            state=str(payload.get("state") or "failed"),
            findings=findings,
            artifacts=list(payload.get("artifacts") or []),
            status=MemoryServiceStatus(status="success", backend="postgres"),
            summary=dict(payload.get("summary") or {}),
            subject=_validator_subject_from_payload(payload),
        )

    async def _get_index_ids(self, key: str) -> list[str]:
        payload = await self.fact_store.get(key, default=[])
        if not isinstance(payload, list):
            return []
        return [str(item) for item in payload if str(item).strip()]

    async def _set_index_ids(self, key: str, ids: list[str]) -> None:
        deduped_ids = list(dict.fromkeys(str(item) for item in ids if str(item).strip()))
        await self.fact_store.set(key, deduped_ids)

    async def _append_unique_index_id(self, key: str, item_id: str) -> None:
        ids = await self._get_index_ids(key)
        if item_id not in ids:
            ids.append(item_id)
            await self._set_index_ids(key, ids)

    async def _remove_index_ids(self, key: str, remove_ids: set[str]) -> None:
        if not remove_ids:
            return
        ids = await self._get_index_ids(key)
        kept = [item_id for item_id in ids if item_id not in remove_ids]
        await self._set_index_ids(key, kept)

    async def _set_dreaming_status(self, payload: dict[str, Any]) -> None:
        await self.fact_store.set(self._dreaming_status_key(), payload)

    async def upsert_retrieval(self, request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
        if self.retrieval_index is None:
            return await self.adapter.upsert_retrieval(request)

        chunking_config = load_retrieval_chunking_config()
        chunks = chunk_text_by_tokens(request.content, chunking_config)
        if not chunks:
            return MemoryRetrievalResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="empty_retrieval_content",
                ),
            )

        total_chunks = len(chunks)
        doc_root_id = request.document_id
        items: List[RetrievalDocument] = []
        for chunk in chunks:
            chunk_id = request.document_id if total_chunks == 1 else f"{request.document_id}#chunk-{chunk.chunk_index}"
            chunk_metadata = {
                **request.metadata,
            "retrieval_doc_root_id": doc_root_id,
            "retrieval_level": "chunk",
                "chunk_index": chunk.chunk_index,
                "chunk_total": chunk.total_chunks,
                "chunk_token_start": chunk.token_start,
                "chunk_token_end": chunk.token_end,
                "chunk_token_count": chunk.token_count,
                "retrieval_chunk_max_tokens": chunking_config.max_tokens,
                "retrieval_chunk_overlap_tokens": chunking_config.overlap_tokens,
                "effective_max_length": chunking_config.effective_max_tokens,
            }

            record = {
                "document_id": chunk_id,
                "content": chunk.content,
                "source": request.source,
                "chunk_index": chunk.chunk_index,
                "metadata": chunk_metadata,
            }
            await self.fact_store.set(self._retrieval_record_key(chunk_id), record)

            chunk_embedding = request.embedding if (total_chunks == 1 and request.embedding is not None) else None
            if chunk_embedding is None:
                embedding_response = await self.generate_embedding(
                    MemoryEmbeddingRequest(
                        input_text=chunk.content,
                        model=request.metadata.get("embedding_model"),
                        metadata={"source": "retrieval_upsert", **chunk_metadata},
                    )
                )
                if embedding_response.item is None:
                    return MemoryRetrievalResponse(
                        items=[],
                        status=MemoryServiceStatus(
                            status="failed",
                            backend="memory-service",
                            degraded=True,
                            error="embedding_unavailable",
                        ),
                    )
                chunk_embedding = embedding_response.item.vector

            await self.retrieval_index.set(
                chunk_id,
                {
                    "content": chunk.content,
                    "source": request.source,
                    "chunk_index": chunk.chunk_index,
                    "metadata": chunk_metadata,
                    "embedding": chunk_embedding,
                },
            )

            items.append(
                RetrievalDocument(
                    document_id=chunk_id,
                    content=chunk.content,
                    score=1.0,
                    source=request.source,
                    chunk_index=chunk.chunk_index,
                    metadata=chunk_metadata,
                )
            )

        # Optional level-1 retrieval signal: a compact document summary/fingerprint vector.
        summary_max_tokens = int(os.getenv("RETRIEVAL_LEVEL1_SUMMARY_MAX_TOKENS", "128"))
        summary_max_tokens = max(16, summary_max_tokens)
        summary_text = " ".join(chunks[0].content.split()[:summary_max_tokens]).strip()
        if summary_text:
            summary_metadata = {
                **request.metadata,
                "retrieval_doc_root_id": doc_root_id,
                "retrieval_level": "doc_summary",
                "chunk_index": -1,
                "chunk_total": total_chunks,
                "summary_token_count": len(summary_text.split()),
            }
            summary_embedding_response = await self.generate_embedding(
                MemoryEmbeddingRequest(
                    input_text=summary_text,
                    model=request.metadata.get("embedding_model"),
                    metadata={"source": "retrieval_upsert_summary", **summary_metadata},
                )
            )
            if summary_embedding_response.item is not None:
                summary_id = f"{doc_root_id}#summary"
                await self.retrieval_index.set(
                    summary_id,
                    {
                        "content": summary_text,
                        "source": request.source,
                        "chunk_index": -1,
                        "metadata": summary_metadata,
                        "embedding": summary_embedding_response.item.vector,
                    },
                )

        return MemoryRetrievalResponse(
            items=items,
            status=MemoryServiceStatus(status="success", backend="qdrant"),
        )

    async def query_retrieval(self, request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
        if self.retrieval_index is None:
            return await self.adapter.query_retrieval(request)

        embedding_response = await self.generate_embedding(
            MemoryEmbeddingRequest(
                input_text=request.query,
                metadata={
                    "source": "retrieval_query",
                    "session_id": request.session_id,
                    **request.filters,
                },
            )
        )
        if embedding_response.item is None:
            return MemoryRetrievalResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="embedding_unavailable",
                ),
            )

        two_level_enabled = os.getenv("RETRIEVAL_TWO_LEVEL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        if two_level_enabled:
            level1_scan_k = max(request.top_k, int(os.getenv("RETRIEVAL_LEVEL1_SCAN_K", "24")))
            level1_top_docs = max(1, int(os.getenv("RETRIEVAL_LEVEL1_TOP_DOCS", "3")))
            level2_scan_k = max(request.top_k, int(os.getenv("RETRIEVAL_LEVEL2_SCAN_K", "64")))

            level1_hits = await self.retrieval_index.search_semantic(
                embedding_response.item.vector,
                top_k=level1_scan_k,
            )

            best_doc_scores: Dict[str, float] = {}
            for hit in level1_hits:
                metadata = hit.get("metadata", {}) or {}
                level = str(metadata.get("retrieval_level", "")).strip().lower()
                root_id = str(metadata.get("retrieval_doc_root_id") or self._retrieval_doc_root_key(str(hit.get("key") or ""))).strip()
                if not root_id:
                    continue
                # Prefer explicit doc_summary entries for level-1 selection.
                if level and level != "doc_summary":
                    continue
                score = float(hit.get("score", 0.0) or 0.0)
                prev = best_doc_scores.get(root_id)
                if prev is None or score > prev:
                    best_doc_scores[root_id] = score

            selected_doc_roots = {
                doc_id
                for doc_id, _ in sorted(best_doc_scores.items(), key=lambda item: item[1], reverse=True)[:level1_top_docs]
            }

            hits = await self.retrieval_index.search_semantic(
                embedding_response.item.vector,
                top_k=level2_scan_k,
            )
        else:
            selected_doc_roots = set()
            hits = await self.retrieval_index.search_semantic(
                embedding_response.item.vector,
                top_k=request.top_k,
            )

        items: List[RetrievalDocument] = []
        seen_ids: set[str] = set()
        for hit in hits:
            metadata = hit.get("metadata", {}) or {}
            retrieval_level = str(metadata.get("retrieval_level", "")).strip().lower()
            if retrieval_level == "doc_summary":
                continue

            root_id = str(metadata.get("retrieval_doc_root_id") or self._retrieval_doc_root_key(str(hit.get("key") or ""))).strip()
            if two_level_enabled and selected_doc_roots and root_id not in selected_doc_roots:
                continue

            if request.filters and any(metadata.get(k) != v for k, v in request.filters.items()):
                continue
            if request.min_score is not None and hit.get("score", 0.0) < request.min_score:
                continue
            raw = await self.fact_store.get(
                self._retrieval_record_key(hit["key"]),
                default=hit.get("record"),
            )
            record = raw or {}
            resolved_doc_id = record.get("document_id", hit["key"])
            if resolved_doc_id in seen_ids:
                continue
            seen_ids.add(str(resolved_doc_id))
            items.append(
                RetrievalDocument(
                    document_id=resolved_doc_id,
                    content=record.get("content", hit.get("content", "")),
                    score=hit.get("score", 0.0),
                    source=record.get("source", hit.get("source")),
                    chunk_index=record.get("chunk_index", hit.get("chunk_index")),
                    metadata={
                        **record.get("metadata", hit.get("metadata", {})),
                        "retrieval_strategy": "two_level" if two_level_enabled else "single_stage",
                        "level1_selected_doc_roots": sorted(selected_doc_roots) if two_level_enabled else [],
                    },
                )
            )
            if len(items) >= request.top_k:
                break

        return MemoryRetrievalResponse(
            items=items[: request.top_k],
            status=MemoryServiceStatus(status="success", backend="qdrant"),
        )

    async def context_search(self, request: ContextSearchRequest) -> ContextSearchResponse:
        if self.context_store is None:
            # Fallback: delegate to in-memory keyword search
            return await self._context_fallback_store.context_search(request)
        try:
            hits = await self.context_store.context_search(
                query=request.query,
                scope=request.scope,
                top_k=request.top_k,
                min_score=request.min_score,
            )
            return ContextSearchResponse(
                items=[
                    ContextDocument(
                        document_id=h["document_id"],
                        content=h["content"],
                        score=h["score"],
                        scope=h.get("scope", {}),
                        metadata=h.get("metadata", {}),
                    )
                    for h in hits
                ],
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as exc:
            response = await self._context_fallback_store.context_search(request)
            response.status.error = f"context_search_error: {exc}"
            return response

    async def context_upsert(self, request: ContextUpsertRequest) -> ContextSearchResponse:
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

        if self.context_store is None:
            return await self._context_fallback_store.context_upsert(request)
        try:
            decision_meta = _context_upsert_policy_metadata(request, decision="allowed")
            request_metadata = request.effective_metadata()
            pattern_meta: Dict[str, Any] = {}
            try:
                pattern_meta = await self._learn_cross_session_pattern(request=request, metadata=request_metadata)
            except Exception as pattern_exc:
                pattern_meta = {
                    "pattern_learning_enabled": self.lifecycle_governance.phase_enabled("pattern_learning"),
                    "pattern_learning_status": "error",
                    "pattern_learning_error": str(pattern_exc),
                }

            decision_input_meta = {**request_metadata, **pattern_meta}
            promotion_decision = self.lifecycle_governance.decide_promotion(request, decision_input_meta)
            governance_meta = {**promotion_decision.as_metadata(), **pattern_meta}
            governance_meta["governance_scope_link_enabled"] = self.lifecycle_governance.phase_enabled("scope_link")
            governance_meta["governance_cleanup_enabled"] = self.lifecycle_governance.phase_enabled("cleanup")
            governance_meta["governance_pattern_learning_enabled"] = self.lifecycle_governance.phase_enabled("pattern_learning")
            enriched_doc_metadata = {**request_metadata, **decision_meta, **governance_meta}
            await self.context_store.context_upsert(
                document_id=request.document_id,
                content=request.content,
                scope=request.scope,
                embedding=request.embedding,
                metadata=enriched_doc_metadata,
            )

            governance_result: Dict[str, Any] = {}
            try:
                await self._maybe_link_context_scope(request=request, metadata=enriched_doc_metadata)
                governance_result = await self._maybe_promote_context(request=request, metadata=enriched_doc_metadata)
            except Exception as governance_exc:
                governance_result = {"governance_error": str(governance_exc)}

            response_meta = {**decision_meta, **governance_result}
            return ContextSearchResponse(
                items=[
                    ContextDocument(
                        document_id=request.document_id,
                        content=request.content,
                        score=1.0,
                        scope=request.scope.model_dump(exclude_none=True),
                        metadata={**enriched_doc_metadata, **governance_result},
                    )
                ],
                status=MemoryServiceStatus(
                    status="success",
                    backend="memory-service",
                    metadata=response_meta,
                ),
            )
        except Exception as exc:
            response = await self._context_fallback_store.context_upsert(request)
            response.status.error = f"context_upsert_error: {exc}"
            return response

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
        if self.graph_store is None or not hasattr(self.graph_store, "relation_upsert"):
            return await self._relation_fallback_store.relation_upsert(request)

        try:
            row = await self.graph_store.relation_upsert(
                source=request.source,
                relation=request.relation,
                target=request.target,
                session_id=request.session_id,
                run_id=request.run_id,
                weight=request.weight,
                metadata=_relation_metadata_with_defaults(
                    request.metadata,
                    validated=request.validated,
                    explicit_acceptance=request.explicit_acceptance,
                    session_id=request.session_id,
                    run_id=request.run_id,
                ),
            )
            return RelationExpandResponse(
                items=[RelationEdge(**row)],
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as exc:
            return RelationExpandResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=f"relation_upsert_error: {exc}",
                ),
            )

    async def relation_expand(self, request: RelationExpandRequest) -> RelationExpandResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "relation_expand"):
            return await self._relation_fallback_store.relation_expand(request)
        try:
            rows = await self.graph_store.relation_expand(
                session_id=request.session_id,
                run_id=request.run_id,
                query=request.query,
                limit=max(1, min(request.limit, 50)),
            )
            return RelationExpandResponse(
                items=[RelationEdge(**row) for row in rows],
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as exc:
            return RelationExpandResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=f"relation_expand_error: {exc}",
                ),
            )

    async def relation_cleanup_expired(self, request: RelationCleanupExpiredRequest) -> RelationCleanupExpiredResponse:
        allowed, reason = self.lifecycle_governance.cleanup_allowed(
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
        if self.graph_store is None or not hasattr(self.graph_store, "relation_cleanup_expired"):
            return await self._relation_fallback_store.relation_cleanup_expired(request)
        now_ts = request.now_ts if request.now_ts is not None else datetime.now(UTC).timestamp()
        try:
            removed = await self.graph_store.relation_cleanup_expired(
                now_ts=float(now_ts),
                session_id=request.session_id,
                run_id=request.run_id,
                limit=max(1, min(request.limit, 20000)),
            )
            return RelationCleanupExpiredResponse(
                removed=int(removed),
                status=MemoryServiceStatus(
                    status="success",
                    backend="memory-service",
                    metadata={
                        "scope_session_id": request.session_id,
                        "scope_run_id": request.run_id,
                        "now_ts": float(now_ts),
                    },
                ),
            )
        except Exception as exc:
            return RelationCleanupExpiredResponse(
                removed=0,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=f"relation_cleanup_expired_error: {exc}",
                ),
            )

    def _graph_v2_unavailable(self, op: str) -> GraphNodeResponse:
        return GraphNodeResponse(
            ok=False,
            status=MemoryServiceStatus(
                status="failed", backend="memory-service", degraded=True,
                error=f"graph_v2_{op}: neo4j not configured or method missing",
            ),
        )

    async def graph_agent_upsert(self, request: GraphAgentUpsertRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "agent_upsert"):
            return self._graph_v2_unavailable("agent_upsert")
        try:
            data = await self.graph_store.agent_upsert(
                agent_id=request.agent_id, role=request.role, version=request.version
            )
            return GraphNodeResponse(data=data)
        except Exception as exc:
            return self._graph_v2_unavailable(f"agent_upsert_error: {exc}")

    async def graph_task_upsert(self, request: GraphTaskUpsertRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "task_upsert"):
            return self._graph_v2_unavailable("task_upsert")
        try:
            data = await self.graph_store.task_upsert(
                task_id=request.task_id, status=request.status, agent_id=request.agent_id
            )
            return GraphNodeResponse(data=data)
        except Exception as exc:
            return self._graph_v2_unavailable(f"task_upsert_error: {exc}")

    async def graph_context_upsert(self, request: GraphContextUpsertRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "context_upsert"):
            return self._graph_v2_unavailable("context_upsert")
        try:
            data = await self.graph_store.context_upsert(
                context_id=request.context_id, context_type=request.context_type
            )
            return GraphNodeResponse(data=data)
        except Exception as exc:
            return self._graph_v2_unavailable(f"context_upsert_error: {exc}")

    async def graph_fact_upsert(self, request: GraphFactUpsertRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "fact_upsert"):
            return self._graph_v2_unavailable("fact_upsert")
        try:
            data = await self.graph_store.fact_upsert(
                fact_id=request.fact_id,
                text=request.text,
                source=request.source,
                context_id=request.context_id,
                agent_id=request.agent_id,
                task_id=request.task_id,
                embedding_id=request.embedding_id,
            )
            return GraphNodeResponse(data=data)
        except Exception as exc:
            return self._graph_v2_unavailable(f"fact_upsert_error: {exc}")

    async def graph_fact_link(self, request: GraphFactLinkRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "fact_link"):
            return self._graph_v2_unavailable("fact_link")
        try:
            await self.graph_store.fact_link(
                fact_a_id=request.fact_a_id,
                fact_b_id=request.fact_b_id,
                relation_type=request.relation_type,
            )
            return GraphNodeResponse(data={"fact_a_id": request.fact_a_id, "fact_b_id": request.fact_b_id})
        except Exception as exc:
            return self._graph_v2_unavailable(f"fact_link_error: {exc}")

    async def graph_embedding_upsert(self, request: GraphEmbeddingUpsertRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "embedding_upsert"):
            return self._graph_v2_unavailable("embedding_upsert")
        try:
            data = await self.graph_store.embedding_upsert(
                embedding_id=request.embedding_id, vector_ref=request.vector_ref, dim=request.dim
            )
            return GraphNodeResponse(data=data)
        except Exception as exc:
            return self._graph_v2_unavailable(f"embedding_upsert_error: {exc}")

    async def graph_semantic_link(self, request: GraphSemanticLinkRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "semantic_link"):
            return self._graph_v2_unavailable("semantic_link")
        try:
            await self.graph_store.semantic_link(
                emb_a_id=request.emb_a_id, emb_b_id=request.emb_b_id, score=request.score
            )
            return GraphNodeResponse(data={"emb_a_id": request.emb_a_id, "emb_b_id": request.emb_b_id})
        except Exception as exc:
            return self._graph_v2_unavailable(f"semantic_link_error: {exc}")

    async def graph_tool_upsert(self, request: GraphToolUpsertRequest) -> GraphNodeResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "tool_upsert"):
            return self._graph_v2_unavailable("tool_upsert")
        try:
            data = await self.graph_store.tool_upsert(
                name=request.name, version=request.version, category=request.category
            )
            return GraphNodeResponse(data=data)
        except Exception as exc:
            return self._graph_v2_unavailable(f"tool_upsert_error: {exc}")

    async def graph_context_graph(self, request: GraphContextGraphRequest) -> GraphContextGraphResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "context_graph"):
            return GraphContextGraphResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed", backend="memory-service", degraded=True,
                    error="graph_v2_context_graph: neo4j not configured",
                ),
            )
        try:
            items = await self.graph_store.context_graph(
                context_id=request.context_id, limit=request.limit
            )
            return GraphContextGraphResponse(items=items)
        except Exception as exc:
            return GraphContextGraphResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed", backend="memory-service", degraded=True,
                    error=f"graph_context_graph_error: {exc}",
                ),
            )

    async def architecture_subgraph(self, request: GraphSubgraphRequest) -> GraphSubgraphResponse:
        if self.graph_store is None or not hasattr(self.graph_store, "architecture_subgraph"):
            return await super().architecture_subgraph(request)
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.graph_store.architecture_subgraph(
                    component=request.component,
                    limit=request.limit,
                ),
                timeout=Settings.MEMORY_ARCHITECTURE_SUBGRAPH_TIMEOUT_SECONDS,
            )
            return GraphSubgraphResponse(
                component=request.component,
                nodes=result.get("nodes", []),
                edges=result.get("edges", []),
                truncated=bool(result.get("truncated", False)),
                query_ms=int(result.get("query_ms", 0)),
            )
        except TimeoutError:
            return GraphSubgraphResponse(
                component=request.component,
                query_ms=int((time.perf_counter() - started) * 1000),
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="architecture_subgraph_timeout",
                ),
            )
        except Exception as exc:
            return GraphSubgraphResponse(
                component=request.component,
                query_ms=int((time.perf_counter() - started) * 1000),
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error=f"architecture_subgraph_error: {exc}",
                ),
            )

    async def generate_embedding(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        if not self.embedding_service_base_url:
            return MemoryEmbeddingResponse(
                item=None,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="embedding",
                    degraded=True,
                    error="embedding_service_not_configured",
                ),
            )
        endpoint = f"{self.embedding_service_base_url}/embedding/generate"
        payload = request.model_dump()
        started = time.perf_counter()
        _append_embedding_request_audit(
            "before_post",
            request,
            endpoint=endpoint,
            timeout_seconds=self.embedding_service_timeout_seconds,
        )
        try:
            if self.embedding_client is not None:
                response = await self.embedding_client.post(endpoint, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self.embedding_service_timeout_seconds) as client:
                    response = await client.post(endpoint, json=payload)

            response.raise_for_status()
            result = MemoryEmbeddingResponse(**response.json())
            http_status = getattr(response, "status_code", None)
            _append_embedding_request_audit(
                "after_post",
                request,
                endpoint=endpoint,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                http_status=http_status,
                result_status=result.status.status,
                degraded=result.status.degraded,
                dimensions=result.item.dimensions if result.item is not None else None,
            )
            return result
        except Exception as exc:
            _append_embedding_request_audit(
                "post_error",
                request,
                endpoint=endpoint,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                error=type(exc).__name__,
                error_message=str(exc)[:500],
            )
            return MemoryEmbeddingResponse(
                item=None,
                status=MemoryServiceStatus(
                    status="failed",
                    backend="embedding",
                    degraded=True,
                    error=f"embedding_service_error: {exc}",
                ),
            )

    async def _probe_redis(self) -> Literal["healthy", "degraded", "unavailable"]:
        try:
            client = await self.session_store._ensure_client()
            ping = getattr(client, "ping", None)
            if callable(ping):
                result = ping()
                if inspect.isawaitable(result):
                    await result
            else:
                await client.exists("__health__:redis")
            return "healthy"
        except Exception:
            return "unavailable"

    async def _probe_neo4j(self) -> Literal["healthy", "degraded", "unavailable"]:
        if self.graph_store is None:
            return "unavailable"
        try:
            healthcheck = getattr(self.graph_store, "healthcheck", None)
            if callable(healthcheck):
                result = healthcheck()
                if inspect.isawaitable(result):
                    return "healthy" if await result else "unavailable"
                return "healthy" if bool(result) else "unavailable"
            return "degraded"
        except Exception:
            return "unavailable"

    async def _probe_postgres(self) -> Literal["healthy", "degraded", "unavailable"]:
        try:
            await self.fact_store.initialize()
            await self.fact_store.exists("__health__:postgres")
            return "healthy"
        except Exception:
            return "unavailable"

    async def _probe_chroma(self) -> Literal["healthy", "degraded", "unavailable"]:
        if self.context_store is None:
            return "unavailable"
        try:
            if not self.context_store._initialized:
                await self.context_store.initialize()
            return "healthy"
        except Exception:
            # Distinguish a true backend outage from a client/API mismatch.
            # If heartbeat is reachable but ContextStore init fails, report degraded.
            chroma_host = getattr(self.context_store, "chroma_host", None) or Settings.CHROMA_HOST
            chroma_port = getattr(self.context_store, "chroma_port", None) or Settings.CHROMA_PORT
            if not chroma_host:
                return "unavailable"

            base_url = f"http://{chroma_host}:{int(chroma_port)}"
            heartbeat_paths = ("/api/v2/heartbeat", "/api/v1/heartbeat", "/heartbeat")
            try:
                async with httpx.AsyncClient(timeout=2.5) as client:
                    for path in heartbeat_paths:
                        try:
                            response = await client.get(f"{base_url}{path}")
                        except Exception:
                            continue
                        if response.status_code in (200, 404, 410):
                            return "degraded"
            except Exception:
                pass

            return "unavailable"

    async def _build_health_response(self) -> MemoryHealthResponse:
        embedding_health_payload = await self._fetch_embedding_health_payload()
        backend_health: Dict[str, Literal["healthy", "degraded", "unavailable"]] = {
            "redis": await self._probe_redis(),
            "postgres": await self._probe_postgres(),
            "qdrant": await self._probe_qdrant(),
            "chroma": await self._probe_chroma(),
            "neo4j": await self._probe_neo4j(),
            "embedding": self._embedding_health_state_from_payload(embedding_health_payload),
        }
        
        deg_codes: List[str] = []
        avail_caps: List[str] = []
        unavail_caps: List[str] = []

        if backend_health.get("postgres") == "healthy":
            avail_caps.extend(["postgres", "session_store", "fact_store"])
        else:
            deg_codes.append("POSTGRES_UNAVAILABLE")
            unavail_caps.append("postgres")

        if backend_health.get("redis") == "healthy":
            avail_caps.append("redis")
        else:
            deg_codes.append("REDIS_UNAVAILABLE")
            unavail_caps.append("redis")

        if backend_health.get("qdrant") == "healthy" and self.retrieval_index is not None:
            avail_caps.append("qdrant_retrieval")
        else:
            deg_codes.append("QDRANT_UNAVAILABLE")
            unavail_caps.append("qdrant_retrieval")

        if backend_health.get("chroma") == "healthy" and self.context_store is not None:
            avail_caps.append("chroma_context")
        else:
            deg_codes.append("CHROMA_UNAVAILABLE")
            unavail_caps.append("chroma_context")

        if backend_health.get("neo4j") == "healthy" and self.graph_store is not None:
            avail_caps.append("neo4j_graph")
        else:
            deg_codes.append("NEO4J_UNAVAILABLE")
            unavail_caps.append("neo4j_graph")

        if backend_health.get("embedding") == "healthy":
            avail_caps.append("embedding_worker")
        else:
            deg_codes.append("EMBEDDING_WORKER_UNAVAILABLE")
            unavail_caps.append("embedding_worker")

        pol = str(getattr(Settings, "PERSISTENCE_POLICY", "strict")).lower()
        if backend_health["postgres"] != "healthy":
            eff_mode = "degraded_in_memory" if pol == "degraded" else "failed"
            status = MemoryServiceStatus(
                status="failed" if pol == "strict" else "partial",
                backend="memory-service",
                degraded=True,
                error="POSTGRES_UNAVAILABLE",
                metadata={
                    "backend_health": backend_health,
                    "effective_store_mode": eff_mode,
                    "persistence_policy": pol,
                    "degradation_codes": deg_codes,
                    "available_capabilities": avail_caps,
                    "unavailable_capabilities": unavail_caps,
                },
            )
        elif deg_codes:
            eff_mode = "backed_degraded"
            status = MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error=deg_codes[0],
                metadata={
                    "backend_health": backend_health,
                    "effective_store_mode": eff_mode,
                    "persistence_policy": pol,
                    "degradation_codes": deg_codes,
                    "available_capabilities": avail_caps,
                    "unavailable_capabilities": unavail_caps,
                },
            )
        else:
            eff_mode = "backed"
            status = MemoryServiceStatus(
                status="success",
                backend="memory-service",
                metadata={
                    "backend_health": backend_health,
                    "effective_store_mode": eff_mode,
                    "persistence_policy": pol,
                    "degradation_codes": [],
                    "available_capabilities": avail_caps,
                    "unavailable_capabilities": [],
                },
            )

        return MemoryHealthResponse(
            status=status,
            backend_health=backend_health,
            device=self._embedding_health_detail(embedding_health_payload, "device"),
            execution_devices=self._embedding_health_execution_devices(embedding_health_payload),
            model=self._embedding_health_detail(embedding_health_payload, "model"),
            dimensions=self._embedding_health_int_detail(embedding_health_payload, "dimensions"),
            runtime_backend=self._embedding_health_detail(embedding_health_payload, "runtime_backend"),
            effective_max_length=self._embedding_health_int_detail(embedding_health_payload, "effective_max_length"),
            configured_model_id=self._embedding_health_detail(embedding_health_payload, "configured_model_id"),
            configured_model_dir=self._embedding_health_detail(embedding_health_payload, "configured_model_dir"),
        )

    def get_health(self) -> Dict[str, Any]:
        """Synchronous health snapshot for factory probes."""
        pol = str(getattr(Settings, "PERSISTENCE_POLICY", "strict")).lower()
        unavail = []
        avail = ["session_store", "fact_store", "postgres"]

        if self.retrieval_index is None:
            unavail.append("QDRANT_UNAVAILABLE")
        else:
            avail.append("qdrant_retrieval")

        if self.context_store is None:
            unavail.append("CHROMA_UNAVAILABLE")
        else:
            avail.append("chroma_context")

        if self.graph_store is None:
            unavail.append("NEO4J_UNAVAILABLE")
        else:
            avail.append("neo4j_graph")

        mode = "backed_degraded" if unavail else "backed"
        return {
            "effective_store_mode": mode,
            "persistence_policy": pol,
            "degraded": bool(unavail),
            "fallback_reason_code": None,
            "degradation_codes": unavail,
            "available_capabilities": avail,
            "unavailable_capabilities": unavail,
            "metadata": {
                "backend_health": {
                    "postgres": "healthy",
                    "redis": "healthy",
                    "qdrant": "healthy" if self.retrieval_index is not None else "unavailable",
                    "chroma": "healthy" if self.context_store is not None else "unavailable",
                    "neo4j": "healthy" if self.graph_store is not None else "unavailable",
                }
            }
        }

    async def health(self) -> MemoryHealthResponse:
        return await self._build_health_response()

    async def health_backends(self) -> MemoryHealthResponse:
        return await self._build_health_response()

    async def close(self) -> None:
        await self.session_store.close()
        await self.fact_store.close()
        if self.retrieval_index is not None:
            await self.retrieval_index.close()
        if self.context_store is not None:
            await self.context_store.close()
        if self.graph_store is not None and hasattr(self.graph_store, "close"):
            result = self.graph_store.close()
            if inspect.isawaitable(result):
                await result

    async def _probe_qdrant(self) -> Literal["healthy", "degraded", "unavailable"]:
        if self.retrieval_index is None:
            return "unavailable"
        try:
            return "healthy" if await self.retrieval_index.healthcheck() else "unavailable"
        except Exception:
            return "unavailable"

    async def _fetch_embedding_health_payload(self) -> Dict[str, Any] | None:
        if not self.embedding_service_base_url:
            return None

        endpoint = f"{self.embedding_service_base_url.rstrip('/')}/health"
        try:
            if self.embedding_client is not None:
                response = await self.embedding_client.get(endpoint)
            else:
                async with httpx.AsyncClient(timeout=self.embedding_service_timeout_seconds) as client:
                    response = await client.get(endpoint)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    @staticmethod
    def _embedding_health_state_from_payload(payload: Dict[str, Any] | None) -> Literal["healthy", "degraded", "unavailable"]:
        if not isinstance(payload, dict):
            return "unavailable"
        status = str((payload.get("status") or {}).get("status") or "").strip().lower()
        if status == "success":
            return "healthy"
        if status:
            return "degraded"
        return "unavailable"

    @staticmethod
    def _embedding_health_detail(payload: Dict[str, Any] | None, key: str) -> str | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get(key)
        return str(value) if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _embedding_health_int_detail(payload: Dict[str, Any] | None, key: str) -> int | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        return None

    @staticmethod
    def _embedding_health_execution_devices(payload: Dict[str, Any] | None) -> List[str]:
        if not isinstance(payload, dict):
            return []
        raw_devices = payload.get("execution_devices")
        if not isinstance(raw_devices, list):
            return []
        devices: List[str] = []
        for item in raw_devices:
            if isinstance(item, str) and item.strip():
                devices.append(item)
        return devices

    @staticmethod
    def _retrieval_record_key(document_id: str) -> str:
        return f"retrieval_doc:{document_id}"

    @staticmethod
    def _retrieval_doc_root_key(document_id: str) -> str:
        if "#chunk-" in document_id:
            return document_id.split("#chunk-", 1)[0]
        if document_id.endswith("#summary"):
            return document_id[:-8]
        return document_id

    @staticmethod
    def _staging_record_key(staging_id: str) -> str:
        return f"staging_record:{staging_id}"

    @staticmethod
    def _staging_session_index_key(session_id: str) -> str:
        return f"staging_index:session:{session_id}"

    @staticmethod
    def _staging_all_index_key() -> str:
        return "staging_index:all"

    @staticmethod
    def _dreaming_proposal_key(proposal_id: str) -> str:
        return f"dreaming_proposal:{proposal_id}"

    @staticmethod
    def _dreaming_proposals_session_index_key(session_id: str) -> str:
        return f"dreaming_proposals_index:session:{session_id}"

    @staticmethod
    def _dreaming_proposals_all_index_key() -> str:
        return "dreaming_proposals_index:all"

    @staticmethod
    def _dreaming_status_key() -> str:
        return "dreaming_status"

    @staticmethod
    def _validator_job_key(job_id: str) -> str:
        return f"validator_job:{job_id}"

    @staticmethod
    def _validator_jobs_index_key() -> str:
        return "validator_jobs_index:all"


