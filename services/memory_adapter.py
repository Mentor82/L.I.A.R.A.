"""Service-facing memory adapters.

These adapters let the orchestrator depend on a memory boundary rather than
concrete store implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx

from services.config import Settings
from services.contracts import (
    EmbeddingVector,
    ContextDocument,
    ContextUpsertRequest,
    MemoryEmbeddingRequest,
    MemoryEmbeddingResponse,
    MemoryFactQueryRequest,
    MemoryFactResponse,
    MemoryFactUpsertRequest,
    MemoryFactRecord,
    MemoryHistoryAppendRequest,
    MemoryHistoryQueryRequest,
    MemoryHistoryResponse,
    MemoryMessageRecord,
    MemoryRetrievalQueryRequest,
    MemoryRetrievalResponse,
    MemoryRetrievalUpsertRequest,
    ContextSearchRequest,
    ContextSearchResponse,
    RelationEdge,
    RelationUpsertRequest,
    RelationExpandRequest,
    RelationExpandResponse,
    MemoryServiceStatus,
    RetrievalDocument,
    GraphAgentUpsertRequest,
    GraphTaskUpsertRequest,
    GraphContextUpsertRequest,
    GraphFactUpsertRequest,
    GraphFactLinkRequest,
    GraphEmbeddingUpsertRequest,
    GraphSemanticLinkRequest,
    GraphToolUpsertRequest,
    GraphContextGraphRequest,
    GraphNodeResponse,
    GraphContextGraphResponse,
    ValidatorSubmitRequest,
    ValidatorSubmitResponse,
    ValidatorStatusRequest,
    ValidatorStatusResponse,
    ValidatorResultRequest,
    ValidatorResultResponse,
)
from services.shared.exceptions import MemoryError
from services.shared.types import MemoryTier


def _get_in_memory_context_store():
    """Lazy import to avoid circular dependencies."""
    from services.memory.store import InMemoryMemoryServiceStore
    return InMemoryMemoryServiceStore()


class MemoryServiceAdapter(ABC):
    """Boundary used by orchestrator-facing code for memory access."""

    @abstractmethod
    async def get(self, tier: MemoryTier, key: str, default: Any = None) -> Any:
        pass

    @abstractmethod
    async def set(self, tier: MemoryTier, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        pass

    @abstractmethod
    async def delete(self, tier: MemoryTier, key: str) -> None:
        pass

    @abstractmethod
    async def exists(self, tier: MemoryTier, key: str) -> bool:
        pass

    @abstractmethod
    async def append_history(self, request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
        pass

    @abstractmethod
    async def query_history(self, request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
        pass

    @abstractmethod
    async def upsert_fact(self, request: MemoryFactUpsertRequest) -> MemoryFactResponse:
        pass

    @abstractmethod
    async def query_facts(self, request: MemoryFactQueryRequest) -> MemoryFactResponse:
        pass

    @abstractmethod
    async def upsert_retrieval(self, request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
        pass

    @abstractmethod
    async def query_retrieval(self, request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
        pass

    @abstractmethod
    async def generate_embedding(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        pass

    @abstractmethod
    async def context_search(self, request: ContextSearchRequest) -> ContextSearchResponse:
        pass

    @abstractmethod
    async def context_upsert(self, request: ContextUpsertRequest) -> ContextSearchResponse:
        pass

    @abstractmethod
    async def relation_upsert(self, request: RelationUpsertRequest) -> RelationExpandResponse:
        pass

    @abstractmethod
    async def relation_expand(self, request: RelationExpandRequest) -> RelationExpandResponse:
        pass

    @abstractmethod
    async def graph_agent_upsert(self, *, agent_id: str, role: str | None = None, version: str | None = None) -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None) -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session") -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_fact_upsert(
        self,
        *,
        fact_id: str,
        text: str,
        source: str,
        context_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        embedding_id: str | None = None,
    ) -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED") -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None) -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float) -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None) -> GraphNodeResponse:
        pass

    @abstractmethod
    async def graph_context_graph(self, *, context_id: str, limit: int = 20) -> GraphContextGraphResponse:
        pass

    async def validator_submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        raise NotImplementedError("validator submit is not available on this memory adapter")

    async def validator_status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        raise NotImplementedError("validator status is not available on this memory adapter")

    async def validator_result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        raise NotImplementedError("validator result is not available on this memory adapter")


class InProcessMemoryAdapter(MemoryServiceAdapter):
    """Adapter that maps memory service contracts to in-process MemoryLayer."""

    HISTORY_INDEX_PREFIX = "history_index"
    HISTORY_RECORD_PREFIX = "history_record"
    FACT_RECORD_PREFIX = "fact_record"
    FACT_NAMESPACE_PREFIX = "fact_namespace"
    RETRIEVAL_RECORD_PREFIX = "retrieval_record"
    RETRIEVAL_INDEX_KEY = "retrieval_index"

    def __init__(self, memory_layer):
        self.memory_layer = memory_layer
        # Fallback in-memory context store when no Chroma/external context backend is configured.
        self._fallback_context_store = _get_in_memory_context_store()

    async def validator_submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        return await self.memory_layer.validator_submit(request)

    async def validator_status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        return await self.memory_layer.validator_status(request)

    async def validator_result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        return await self.memory_layer.validator_result(request)

    async def get(self, tier: MemoryTier, key: str, default: Any = None) -> Any:
        return await self.memory_layer.get(tier, key, default)

    async def set(self, tier: MemoryTier, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        await self.memory_layer.set(tier, key, value, ttl_seconds)

    async def delete(self, tier: MemoryTier, key: str) -> None:
        await self.memory_layer.delete(tier, key)

    async def exists(self, tier: MemoryTier, key: str) -> bool:
        return await self.memory_layer.exists(tier, key)

    async def append_history(self, request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
        try:
            message = MemoryMessageRecord(
                message_id=str(uuid4()),
                session_id=request.session_id,
                run_id=request.run_id,
                user_id=request.user_id,
                role=request.role,
                content=request.content,
                created_at=datetime.now(UTC).isoformat(),
                metadata=request.metadata,
            )
            message_key = self._history_record_key(message.message_id)
            index_key = self._history_index_key(request.session_id)

            await self.memory_layer.set(MemoryTier.PERSISTENT, message_key, message.model_dump())
            index = await self.memory_layer.get(MemoryTier.PERSISTENT, index_key, default=[])
            index = [*index, message.message_id]
            await self.memory_layer.set(MemoryTier.PERSISTENT, index_key, index)

            return MemoryHistoryResponse(
                items=[message],
                status=MemoryServiceStatus(status="success", backend="postgres"),
            )
        except Exception:
            return MemoryHistoryResponse(
                items=[],
                status=self._failed_primary_status(
                    backend="postgres",
                    error="history_backend_unavailable",
                    operation="append_history",
                ),
            )

    async def query_history(self, request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
        try:
            index = await self.memory_layer.get(
                MemoryTier.PERSISTENT,
                self._history_index_key(request.session_id),
                default=[],
            )
            selected_ids = index[-request.limit :]
            items = []
            for message_id in selected_ids:
                raw = await self.memory_layer.get(
                    MemoryTier.PERSISTENT,
                    self._history_record_key(message_id),
                    default=None,
                )
                if raw is None:
                    continue
                item = MemoryMessageRecord(**raw)
                if not request.include_tool_messages and item.role == "tool":
                    continue
                if request.run_id and item.run_id != request.run_id:
                    continue
                items.append(item)

            return MemoryHistoryResponse(
                items=items,
                status=MemoryServiceStatus(status="success", backend="postgres"),
            )
        except Exception:
            return MemoryHistoryResponse(
                items=[],
                status=self._failed_primary_status(
                    backend="postgres",
                    error="history_backend_unavailable",
                    operation="query_history",
                ),
            )

    async def upsert_fact(self, request: MemoryFactUpsertRequest) -> MemoryFactResponse:
        try:
            now = datetime.now(UTC).isoformat()
            record_key = self._fact_record_key(request.namespace, request.key)
            namespace_key = self._fact_namespace_key(request.namespace)

            existing = await self.memory_layer.get(MemoryTier.PERSISTENT, record_key, default=None)
            record = MemoryFactRecord(
                fact_id=existing.get("fact_id") if existing else str(uuid4()),
                namespace=request.namespace,
                key=request.key,
                value=request.value,
                source=request.source,
                confidence=request.confidence,
                tags=request.tags,
                created_at=existing.get("created_at") if existing else now,
                updated_at=now if existing else None,
                metadata=request.metadata,
            )

            await self.memory_layer.set(MemoryTier.PERSISTENT, record_key, record.model_dump())
            known_keys = await self.memory_layer.get(MemoryTier.PERSISTENT, namespace_key, default=[])
            if request.key not in known_keys:
                known_keys = [*known_keys, request.key]
                await self.memory_layer.set(MemoryTier.PERSISTENT, namespace_key, known_keys)

            return MemoryFactResponse(
                items=[record],
                status=MemoryServiceStatus(status="success", backend="postgres"),
            )
        except Exception:
            return MemoryFactResponse(
                items=[],
                status=self._failed_primary_status(
                    backend="postgres",
                    error="fact_backend_unavailable",
                    operation="upsert_fact",
                ),
            )

    async def query_facts(self, request: MemoryFactQueryRequest) -> MemoryFactResponse:
        try:
            keys = await self.memory_layer.get(
                MemoryTier.PERSISTENT,
                self._fact_namespace_key(request.namespace),
                default=[],
            )
            if request.key:
                keys = [key for key in keys if key == request.key]

            items = []
            for key in keys[: request.limit]:
                raw = await self.memory_layer.get(
                    MemoryTier.PERSISTENT,
                    self._fact_record_key(request.namespace, key),
                    default=None,
                )
                if raw is None:
                    continue
                item = MemoryFactRecord(**raw)
                if request.tags and not set(request.tags).issubset(set(item.tags)):
                    continue
                items.append(item)

            return MemoryFactResponse(
                items=items,
                status=MemoryServiceStatus(status="success", backend="postgres"),
            )
        except Exception:
            return MemoryFactResponse(
                items=[],
                status=self._failed_primary_status(
                    backend="postgres",
                    error="fact_backend_unavailable",
                    operation="query_facts",
                ),
            )

    async def upsert_retrieval(self, request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
        record = {
            "document_id": request.document_id,
            "content": request.content,
            "source": request.source,
            "chunk_index": request.metadata.get("chunk_index"),
            "metadata": request.metadata,
            "embedding": request.embedding,
        }
        await self.memory_layer.set(MemoryTier.RETRIEVAL, self._retrieval_record_key(request.document_id), record)
        known_ids = await self.memory_layer.get(MemoryTier.RETRIEVAL, self.RETRIEVAL_INDEX_KEY, default=[])
        if request.document_id not in known_ids:
            known_ids = [*known_ids, request.document_id]
            await self.memory_layer.set(MemoryTier.RETRIEVAL, self.RETRIEVAL_INDEX_KEY, known_ids)

        return MemoryRetrievalResponse(
            items=[
                RetrievalDocument(
                    document_id=request.document_id,
                    content=request.content,
                    score=1.0,
                    source=request.source,
                    chunk_index=request.metadata.get("chunk_index"),
                    metadata=request.metadata,
                )
            ],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="retrieval_fallback_in_process",
                metadata={"backend": "retrieval-tier", "mode": "in-process"},
            ),
        )

    async def query_retrieval(self, request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
        known_ids = await self.memory_layer.get(MemoryTier.RETRIEVAL, self.RETRIEVAL_INDEX_KEY, default=[])
        hits = []
        query_terms = self._tokenize(request.query)
        for document_id in known_ids:
            raw = await self.memory_layer.get(
                MemoryTier.RETRIEVAL,
                self._retrieval_record_key(document_id),
                default=None,
            )
            if raw is None:
                continue
            if request.filters and any(raw.get("metadata", {}).get(k) != v for k, v in request.filters.items()):
                continue
            score = self._score_content(raw.get("content", ""), query_terms)
            if request.min_score is not None and score < request.min_score:
                continue
            hits.append(
                RetrievalDocument(
                    document_id=document_id,
                    content=raw.get("content", ""),
                    score=score,
                    source=raw.get("source"),
                    chunk_index=raw.get("chunk_index"),
                    metadata=raw.get("metadata", {}),
                )
            )

        hits.sort(key=lambda item: item.score, reverse=True)
        return MemoryRetrievalResponse(
            items=hits[: request.top_k],
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="retrieval_fallback_in_process",
                metadata={"backend": "retrieval-tier", "mode": "in-process"},
            ),
        )

    async def generate_embedding(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        vector = self._embed_text(request.input_text, normalize=request.normalize)
        model_name = request.model or "memory-fallback-v1"
        return MemoryEmbeddingResponse(
            item=EmbeddingVector(
                model=model_name,
                dimensions=len(vector),
                vector=vector,
                metadata=request.metadata,
            ),
            status=MemoryServiceStatus(
                status="partial",
                backend="memory-service",
                degraded=True,
                error="embedding_fallback_in_process",
                metadata={"backend": "embedding-tier", "mode": "in-process"},
            ),
        )

    async def context_search(self, request: ContextSearchRequest) -> ContextSearchResponse:
        """Delegate to memory layer context store, falling back to in-memory store."""
        context_store = getattr(self.memory_layer, "context_store", None)
        if context_store is None:
            return await self._fallback_context_store.context_search(request)
        try:
            hits = await context_store.context_search(
                query=request.query,
                scope=request.scope,
                limit=request.top_k,
            )
            return ContextSearchResponse(
                items=hits,
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as e:
            return ContextSearchResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="partial",
                    backend="memory-service",
                    degraded=True,
                    error="context_backend_degraded",
                    metadata={"mode": "in-process", "operation": "context_search"},
                ),
            )

    async def context_upsert(self, request: ContextUpsertRequest) -> ContextSearchResponse:
        """Delegate context writes, falling back to in-memory store when no Chroma."""
        context_store = getattr(self.memory_layer, "context_store", None)
        if context_store is None:
            return await self._fallback_context_store.context_upsert(request)
        try:
            effective_metadata = request.effective_metadata()
            await context_store.context_upsert(
                document_id=request.document_id,
                content=request.content,
                scope=request.scope,
                embedding=request.embedding,
                metadata=effective_metadata,
            )
            return ContextSearchResponse(
                items=[
                    ContextDocument(
                        document_id=request.document_id,
                        content=request.content,
                        score=1.0,
                        scope=request.scope.model_dump(exclude_none=True),
                        metadata=effective_metadata,
                    )
                ],
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as e:
            return ContextSearchResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="partial",
                    backend="memory-service",
                    degraded=True,
                    error="context_backend_degraded",
                    metadata={"mode": "in-process", "operation": "context_upsert"},
                ),
            )

    async def relation_upsert(self, request: RelationUpsertRequest) -> RelationExpandResponse:
        """Persist relation edge into graph store tier (Neo4j-backed when configured)."""
        try:
            edge = {
                "source": request.source,
                "relation": request.relation,
                "target": request.target,
                "weight": request.weight,
                "metadata": {
                    **request.metadata,
                    "session_id": request.session_id,
                    "run_id": request.run_id,
                    "validated": request.validated,
                    "explicit_acceptance": request.explicit_acceptance,
                },
            }
            key = f"rel:{request.source}:{request.relation}:{request.target}"
            await self.memory_layer.set(MemoryTier.PATTERN, key, edge)
            return RelationExpandResponse(
                items=[RelationEdge(**edge)],
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as e:
            return RelationExpandResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="relation_backend_unavailable",
                    metadata={"mode": "in-process", "operation": "relation_upsert"},
                ),
            )

    async def relation_expand(self, request: RelationExpandRequest) -> RelationExpandResponse:
        try:
            graph_store = getattr(self.memory_layer, "graph_store", None)
            if graph_store is None or not hasattr(graph_store, "relation_expand"):
                return RelationExpandResponse(
                    items=[],
                    status=MemoryServiceStatus(
                        status="failed",
                        backend="memory-service",
                        degraded=True,
                        error="graph_store_unavailable",
                    ),
                )
            rows = await graph_store.relation_expand(
                session_id=request.session_id,
                run_id=request.run_id,
                query=request.query,
                limit=request.limit,
            )
            return RelationExpandResponse(
                items=[RelationEdge(**row) for row in rows],
                status=MemoryServiceStatus(status="success", backend="memory-service"),
            )
        except Exception as e:
            return RelationExpandResponse(
                items=[],
                status=MemoryServiceStatus(
                    status="failed",
                    backend="memory-service",
                    degraded=True,
                    error="relation_backend_unavailable",
                    metadata={"mode": "in-process", "operation": "relation_expand"},
                ),
            )

    async def graph_agent_upsert(self, *, agent_id: str, role: str | None = None, version: str | None = None) -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "agent_upsert"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.agent_upsert(agent_id=agent_id, role=role, version=version)

    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None) -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "task_upsert"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.task_upsert(task_id=task_id, status=status, agent_id=agent_id)

    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session") -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "context_upsert"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.context_upsert(context_id=context_id, context_type=context_type)

    async def graph_fact_upsert(
        self,
        *,
        fact_id: str,
        text: str,
        source: str,
        context_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        embedding_id: str | None = None,
    ) -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "fact_upsert"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.fact_upsert(
            fact_id=fact_id,
            text=text,
            source=source,
            context_id=context_id,
            agent_id=agent_id,
            task_id=task_id,
            embedding_id=embedding_id,
        )

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED") -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "fact_link"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.fact_link(fact_a_id=fact_a_id, fact_b_id=fact_b_id, relation_type=relation_type)

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None) -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "embedding_upsert"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.embedding_upsert(embedding_id=embedding_id, vector_ref=vector_ref, dim=dim)

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float) -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "semantic_link"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.semantic_link(emb_a_id=emb_a_id, emb_b_id=emb_b_id, score=score)

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None) -> GraphNodeResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "tool_upsert"):
            return GraphNodeResponse(ok=False, data={}, status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.tool_upsert(name=name, version=version, category=category)

    async def graph_context_graph(self, *, context_id: str, limit: int = 20) -> GraphContextGraphResponse:
        graph_store = getattr(self.memory_layer, "graph_store", None)
        if graph_store is None or not hasattr(graph_store, "context_graph"):
            return GraphContextGraphResponse(items=[], status=MemoryServiceStatus(status="failed", backend="memory-service", degraded=True, error="graph_store_unavailable"))
        return await graph_store.context_graph(context_id=context_id, limit=limit)

    @classmethod
    def _history_index_key(cls, session_id: str) -> str:
        return f"{cls.HISTORY_INDEX_PREFIX}:{session_id}"

    @classmethod
    def _history_record_key(cls, message_id: str) -> str:
        return f"{cls.HISTORY_RECORD_PREFIX}:{message_id}"

    @classmethod
    def _fact_record_key(cls, namespace: str, key: str) -> str:
        return f"{cls.FACT_RECORD_PREFIX}:{namespace}:{key}"

    @classmethod
    def _fact_namespace_key(cls, namespace: str) -> str:
        return f"{cls.FACT_NAMESPACE_PREFIX}:{namespace}"

    @staticmethod
    def _failed_primary_status(*, backend: str, error: str, operation: str) -> MemoryServiceStatus:
        return MemoryServiceStatus(
            status="failed",
            backend=backend,
            degraded=True,
            error=error,
            metadata={"mode": "in-process", "operation": operation},
        )

    @classmethod
    def _retrieval_record_key(cls, document_id: str) -> str:
        return f"{cls.RETRIEVAL_RECORD_PREFIX}:{document_id}"

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


class RemoteMemoryAdapter(MemoryServiceAdapter):
    """HTTP-backed adapter for future liara-memory service endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        client: Any | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    async def get(self, tier: MemoryTier, key: str, default: Any = None) -> Any:
        del tier, key, default
        raise NotImplementedError("RemoteMemoryAdapter does not expose generic tier get(); use typed memory contracts")

    async def set(self, tier: MemoryTier, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        del tier, key, value, ttl_seconds
        raise NotImplementedError("RemoteMemoryAdapter does not expose generic tier set(); use typed memory contracts")

    async def delete(self, tier: MemoryTier, key: str) -> None:
        del tier, key
        raise NotImplementedError("RemoteMemoryAdapter does not expose generic tier delete(); use typed memory contracts")

    async def exists(self, tier: MemoryTier, key: str) -> bool:
        del tier, key
        raise NotImplementedError("RemoteMemoryAdapter does not expose generic tier exists(); use typed memory contracts")

    async def append_history(self, request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
        payload = await self._post_json("/history/append", request.model_dump())
        return MemoryHistoryResponse(**payload)

    async def query_history(self, request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
        payload = await self._post_json("/history/query", request.model_dump())
        return MemoryHistoryResponse(**payload)

    async def upsert_fact(self, request: MemoryFactUpsertRequest) -> MemoryFactResponse:
        payload = await self._post_json("/facts/upsert", request.model_dump())
        return MemoryFactResponse(**payload)

    async def query_facts(self, request: MemoryFactQueryRequest) -> MemoryFactResponse:
        payload = await self._post_json("/facts/query", request.model_dump())
        return MemoryFactResponse(**payload)

    async def upsert_retrieval(self, request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
        payload = await self._post_json("/retrieval/upsert", request.model_dump())
        return MemoryRetrievalResponse(**payload)

    async def query_retrieval(self, request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
        payload = await self._post_json("/retrieval/query", request.model_dump())
        return MemoryRetrievalResponse(**payload)

    async def validator_submit(self, request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
        payload = await self._post_json("/validator/submit", request.model_dump())
        return ValidatorSubmitResponse(**payload)

    async def validator_status(self, request: ValidatorStatusRequest) -> ValidatorStatusResponse:
        payload = await self._post_json("/validator/status", request.model_dump())
        return ValidatorStatusResponse(**payload)

    async def validator_result(self, request: ValidatorResultRequest) -> ValidatorResultResponse:
        payload = await self._post_json("/validator/result", request.model_dump())
        return ValidatorResultResponse(**payload)

    async def generate_embedding(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        payload = await self._post_json("/embedding/generate", request.model_dump())
        return MemoryEmbeddingResponse(**payload)

    async def context_search(self, request: ContextSearchRequest) -> ContextSearchResponse:
        payload = await self._post_json("/context/search", request.model_dump())
        return ContextSearchResponse(**payload)

    async def context_upsert(self, request: ContextUpsertRequest) -> ContextSearchResponse:
        payload = await self._post_json("/context/upsert", request.model_dump())
        return ContextSearchResponse(**payload)

    async def relation_upsert(self, request: RelationUpsertRequest) -> RelationExpandResponse:
        payload = await self._post_json("/relations/upsert", request.model_dump())
        return RelationExpandResponse(**payload)

    async def relation_expand(self, request: RelationExpandRequest) -> RelationExpandResponse:
        payload = await self._post_json("/relations/expand", request.model_dump())
        return RelationExpandResponse(**payload)

    async def graph_agent_upsert(self, *, agent_id: str, role: str | None = None, version: str | None = None) -> GraphNodeResponse:
        request = GraphAgentUpsertRequest(agent_id=agent_id, role=role, version=version)
        payload = await self._post_json("/graph/agent/upsert", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None) -> GraphNodeResponse:
        request = GraphTaskUpsertRequest(task_id=task_id, status=status, agent_id=agent_id)
        payload = await self._post_json("/graph/task/upsert", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session") -> GraphNodeResponse:
        request = GraphContextUpsertRequest(context_id=context_id, context_type=context_type)
        payload = await self._post_json("/graph/context/upsert", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_fact_upsert(
        self,
        *,
        fact_id: str,
        text: str,
        source: str,
        context_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        embedding_id: str | None = None,
    ) -> GraphNodeResponse:
        request = GraphFactUpsertRequest(
            fact_id=fact_id,
            text=text,
            source=source,
            context_id=context_id,
            agent_id=agent_id,
            task_id=task_id,
            embedding_id=embedding_id,
        )
        payload = await self._post_json("/graph/fact/upsert", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED") -> GraphNodeResponse:
        request = GraphFactLinkRequest(fact_a_id=fact_a_id, fact_b_id=fact_b_id, relation_type=relation_type)
        payload = await self._post_json("/graph/fact/link", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None) -> GraphNodeResponse:
        request = GraphEmbeddingUpsertRequest(embedding_id=embedding_id, vector_ref=vector_ref, dim=dim)
        payload = await self._post_json("/graph/embedding/upsert", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float) -> GraphNodeResponse:
        request = GraphSemanticLinkRequest(emb_a_id=emb_a_id, emb_b_id=emb_b_id, score=score)
        payload = await self._post_json("/graph/embedding/semantic-link", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None) -> GraphNodeResponse:
        request = GraphToolUpsertRequest(name=name, version=version, category=category)
        payload = await self._post_json("/graph/tool/upsert", request.model_dump())
        return GraphNodeResponse(**payload)

    async def graph_context_graph(self, *, context_id: str, limit: int = 20) -> GraphContextGraphResponse:
        request = GraphContextGraphRequest(context_id=context_id, limit=limit)
        payload = await self._post_json("/graph/context/graph", request.model_dump())
        return GraphContextGraphResponse(**payload)

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        await self._client.aclose()
        self._client = None

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._ensure_client()
        response = await client.post(f"{self.base_url}{path}", json=payload)
        response.raise_for_status()
        return response.json()

    async def _get_json(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        client = await self._ensure_client()
        response = await client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client


def ensure_memory_service_adapter(memory_dependency) -> MemoryServiceAdapter:
    """Wrap legacy in-process memory dependency in a service-facing adapter."""
    if isinstance(memory_dependency, MemoryServiceAdapter):
        return memory_dependency

    selected_mode = (getattr(Settings, "MEMORY_MODE", "in_process") or "in_process").strip().lower()
    if selected_mode == "service":
        base_url = getattr(Settings, "MEMORY_SERVICE_BASE_URL", None)
        if not base_url:
            raise MemoryError("MEMORY_SERVICE_BASE_URL is required when MEMORY_MODE=service")
        return RemoteMemoryAdapter(
            base_url,
            timeout_seconds=getattr(Settings, "MEMORY_SERVICE_TIMEOUT_SECONDS", 10.0),
        )

    if getattr(Settings, "MEMORY_ADAPTER_ONLY", True):
        raise MemoryError(
            "MEMORY_ADAPTER_ONLY is enabled; pass a MemoryServiceAdapter instance "
            "(for example InProcessMemoryAdapter or RemoteMemoryAdapter)"
        )

    return InProcessMemoryAdapter(memory_dependency)
