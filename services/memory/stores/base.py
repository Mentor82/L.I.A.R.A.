"""Base interface and lightweight implementations for liara-memory stores."""

from __future__ import annotations

import asyncio
from abc import ABC
from datetime import UTC, datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal
from uuid import uuid4


def _get_store_symbol(name: str, fallback: Any) -> Any:
    mod = sys.modules.get("services.memory.store")
    if not mod:
        return fallback
    return getattr(mod, name, fallback)

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
    ValidatorResultRequest,
    ValidatorResultResponse,
    ValidatorStatusRequest,
    ValidatorStatusResponse,
    ValidatorSubmitRequest,
    ValidatorSubmitResponse,
)


_SENSITIVE_CONTEXT_PATTERNS = [
    r"(?i)api[_-]?key\s*[:=]",
    r"(?i)token\s*[:=]",
    r"(?i)authorization\s*:\s*bearer",
    r"(?i)password\s*[:=]",
    r"(?i)secret\s*[:=]",
    r"AKIA[0-9A-Z]{16}",
]

_EMBEDDING_AUDIT_LOG = Path("logs") / "services" / "embedding_requests.jsonl"


def _estimate_token_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _embedding_text_fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def _append_embedding_request_audit(event: str, request: MemoryEmbeddingRequest, **fields: Any) -> None:
    metadata = dict(request.metadata or {})
    raw_text = getattr(request, "input_text", None) or getattr(request, "text", "") or ""
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": str(event),
        "text_length": len(raw_text),
        "text_tokens": _estimate_token_count(raw_text),
        "text_fingerprint": _embedding_text_fingerprint(raw_text),
        "session_id": metadata.get("session_id"),
        "user_id": metadata.get("user_id"),
        "source": metadata.get("source"),
        "model": metadata.get("model"),
        **fields,
    }

    try:
        _EMBEDDING_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_EMBEDDING_AUDIT_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _context_contains_sensitive_data(content: str) -> bool:
    return any(re.search(pattern, content) for pattern in _SENSITIVE_CONTEXT_PATTERNS)


def _context_scope_present(scope: ContextScope) -> bool:
    return bool(scope.session_id or scope.run_id)


def _compact_summary_text(text: str, *, max_chars: int = 220) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _build_session_summary_candidate(
    *,
    session_id: str,
    messages: List[MemoryMessageRecord],
    max_chars: int = 400,
) -> Tuple[str, List[str]]:
    summary_parts: List[str] = []
    source_ids: List[str] = []
    current_length = 0

    for msg in reversed(messages):
        role = "User" if msg.role == "user" else "Assistant" if msg.role == "assistant" else msg.role
        clean_content = _compact_summary_text(msg.content, max_chars=120)
        part = f"{role}: {clean_content}"
        part_len = len(part) + 4
        if current_length + part_len > max_chars and summary_parts:
            break
        summary_parts.insert(0, part)
        current_length += part_len
        if msg.message_id:
            source_ids.insert(0, msg.message_id)

    summary_text = " || ".join(summary_parts) if summary_parts else f"Session {session_id} summary candidate"
    return summary_text, source_ids


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


class MemoryServiceStore(ABC):
    """Typed backing store API exposed by the liara-memory service."""

    async def health(self) -> MemoryHealthResponse:
        raise NotImplementedError

    async def append_history(self, request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
        raise NotImplementedError

    async def query_history(self, request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
        raise NotImplementedError

    async def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def upsert_session(
        self,
        session_id: str,
        user_id: str,
        *,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def upsert_fact(self, request: MemoryFactUpsertRequest) -> MemoryFactResponse:
        raise NotImplementedError

    async def query_facts(self, request: MemoryFactQueryRequest) -> MemoryFactResponse:
        raise NotImplementedError

    async def delete_fact(self, namespace: str, key: str) -> MemoryFactResponse:
        raise NotImplementedError

    async def upsert_retrieval(self, request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
        raise NotImplementedError

    async def query_retrieval(self, request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
        raise NotImplementedError

    async def generate_embeddings(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        raise NotImplementedError

    async def upsert_context(self, request: ContextUpsertRequest) -> ContextSearchResponse:
        raise NotImplementedError

    async def search_context(self, request: ContextSearchRequest) -> ContextSearchResponse:
        raise NotImplementedError

    async def delete_context(self, document_id: str) -> ContextSearchResponse:
        raise NotImplementedError

    async def upsert_relation(self, request: RelationUpsertRequest) -> RelationExpandResponse:
        raise NotImplementedError

    async def expand_relations(self, request: RelationExpandRequest) -> RelationExpandResponse:
        raise NotImplementedError

    async def cleanup_expired_relations(self, request: RelationCleanupExpiredRequest) -> RelationCleanupExpiredResponse:
        raise NotImplementedError

    async def upsert_graph_agent(self, request: GraphAgentUpsertRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def upsert_graph_task(self, request: GraphTaskUpsertRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def upsert_graph_context(self, request: GraphContextUpsertRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def upsert_graph_fact(self, request: GraphFactUpsertRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def link_graph_fact(self, request: GraphFactLinkRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def upsert_graph_embedding(self, request: GraphEmbeddingUpsertRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def link_graph_semantic(self, request: GraphSemanticLinkRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def upsert_graph_tool(self, request: GraphToolUpsertRequest) -> GraphNodeResponse:
        raise NotImplementedError

    async def get_graph_context(self, request: GraphContextGraphRequest) -> GraphContextGraphResponse:
        raise NotImplementedError

    async def get_graph_subgraph(self, request: GraphSubgraphRequest) -> GraphSubgraphResponse:
        raise NotImplementedError

    async def stage_memory(self, request: MemoryStagingStageRequest) -> MemoryStagingResponse:
        raise NotImplementedError

    async def list_staging(self, request: MemoryStagingListRequest) -> MemoryStagingResponse:
        raise NotImplementedError

    async def touch_staging(self, request: MemoryStagingTouchRequest) -> MemoryStagingResponse:
        raise NotImplementedError

    async def discard_staging(self, request: MemoryStagingDiscardRequest) -> MemoryStagingResponse:
        raise NotImplementedError

    async def run_dreaming(self, request: MemoryDreamingRunRequest) -> MemoryDreamingRunResponse:
        raise NotImplementedError

    async def dreaming_status(self) -> MemoryDreamingStatusResponse:
        raise NotImplementedError

    async def list_dreaming_proposals(self, request: MemoryDreamingProposalListRequest) -> MemoryDreamingProposalListResponse:
        raise NotImplementedError

    async def decide_dreaming_proposal(self, request: MemoryDreamingProposalDecisionRequest) -> MemoryDreamingProposalDecisionResponse:
        raise NotImplementedError

    async def set_proposal_assurance(self, request: MemoryDreamingProposalAssuranceRequest) -> MemoryDreamingProposalAssuranceResponse:
        raise NotImplementedError

    async def submit_validator_job(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        raise NotImplementedError

    async def get_validator_status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        raise NotImplementedError

    async def get_validator_result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        raise NotImplementedError

    async def cleanup_dreaming(self, request: MemoryDreamingCleanupRequest) -> MemoryDreamingCleanupResponse:
        raise NotImplementedError

    async def get(self, tier: str, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    async def set(self, tier: str, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        raise NotImplementedError

    async def delete(self, tier: str, key: str) -> None:
        raise NotImplementedError


class NullMemoryStore:
    """No-op store for tiers not yet served by liara-memory."""

    async def get(self, key: str, default: Any = None) -> Any:
        return default

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass


class EphemeralMemoryStore:
    """Small async key/value store for deferred tiers in backed service mode."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[Any, float | None]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return default
            value, expires_at = item
            if expires_at is not None and datetime.now(UTC).timestamp() >= expires_at:
                self._items.pop(key, None)
                return default
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        async with self._lock:
            expires_at = (
                datetime.now(UTC).timestamp() + float(ttl_seconds)
                if ttl_seconds is not None and ttl_seconds > 0
                else None
            )
            self._items[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._items.pop(key, None)
