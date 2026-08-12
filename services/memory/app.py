"""
FastAPI app for the liara-memory service.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from services.contracts import (
	MemoryEmbeddingRequest,
	MemoryEmbeddingResponse,
	MemoryDreamingProposalListRequest,
	MemoryDreamingProposalListResponse,
	MemoryDreamingProposalDecisionRequest,
	MemoryDreamingProposalDecisionResponse,
	MemoryDreamingProposalAssuranceRequest,
	MemoryDreamingProposalAssuranceResponse,
	MemoryDreamingCleanupRequest,
	MemoryDreamingCleanupResponse,
	ValidatorSubmitRequest,
	ValidatorSubmitResponse,
	ValidatorStatusRequest,
	ValidatorStatusResponse,
	ValidatorResultRequest,
	ValidatorResultResponse,
	MemoryDreamingRunRequest,
	MemoryDreamingRunResponse,
	MemoryDreamingStatusResponse,
	MemoryFactQueryRequest,
	MemoryFactResponse,
	MemoryFactUpsertRequest,
	MemoryHealthResponse,
	MemoryHistoryAppendRequest,
	MemoryHistoryQueryRequest,
	MemoryHistoryResponse,
	MemoryRetrievalQueryRequest,
	MemoryRetrievalResponse,
	MemoryRetrievalUpsertRequest,
	MemoryStagingDiscardRequest,
	MemoryStagingListRequest,
	MemoryStagingResponse,
	MemoryStagingStageRequest,
	MemoryStagingTouchRequest,
	ContextSearchRequest,
	ContextUpsertRequest,
	ContextSearchResponse,
	RelationUpsertRequest,
	RelationExpandRequest,
	RelationExpandResponse,
	RelationCleanupExpiredRequest,
	RelationCleanupExpiredResponse,
)
from services.contracts import (
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
	GraphSubgraphRequest,
	GraphSubgraphResponse,
)
from .store import MemoryServiceStore, create_default_memory_service_store


async def _maybe_await(result: Any) -> Any:
	if hasattr(result, "__await__"):
		return await result
	return result


def create_memory_service_app(store: MemoryServiceStore | None = None) -> FastAPI:
	"""Build a typed FastAPI app for liara-memory endpoints."""
	app = FastAPI(title="liara-memory")
	backing_store = store or create_default_memory_service_store()

	@app.post("/history/append", response_model=MemoryHistoryResponse)
	async def append_history(request: MemoryHistoryAppendRequest) -> MemoryHistoryResponse:
		return await _maybe_await(backing_store.append_history(request))

	@app.post("/history/query", response_model=MemoryHistoryResponse)
	async def query_history(request: MemoryHistoryQueryRequest) -> MemoryHistoryResponse:
		return await _maybe_await(backing_store.query_history(request))

	@app.post("/facts/upsert", response_model=MemoryFactResponse)
	async def upsert_fact(request: MemoryFactUpsertRequest) -> MemoryFactResponse:
		return await _maybe_await(backing_store.upsert_fact(request))

	@app.post("/facts/query", response_model=MemoryFactResponse)
	async def query_facts(request: MemoryFactQueryRequest) -> MemoryFactResponse:
		return await _maybe_await(backing_store.query_facts(request))

	@app.post("/staging/stage", response_model=MemoryStagingResponse)
	async def staging_stage(request: MemoryStagingStageRequest) -> MemoryStagingResponse:
		return await _maybe_await(backing_store.staging_stage(request))

	@app.post("/staging/list", response_model=MemoryStagingResponse)
	async def staging_list(request: MemoryStagingListRequest) -> MemoryStagingResponse:
		return await _maybe_await(backing_store.staging_list(request))

	@app.post("/staging/touch", response_model=MemoryStagingResponse)
	async def staging_touch(request: MemoryStagingTouchRequest) -> MemoryStagingResponse:
		return await _maybe_await(backing_store.staging_touch(request))

	@app.post("/staging/discard", response_model=MemoryStagingResponse)
	async def staging_discard(request: MemoryStagingDiscardRequest) -> MemoryStagingResponse:
		return await _maybe_await(backing_store.staging_discard(request))

	@app.post("/staging/consolidate", response_model=MemoryDreamingRunResponse)
	async def staging_consolidate(request: MemoryDreamingRunRequest) -> MemoryDreamingRunResponse:
		return await _maybe_await(backing_store.dreaming_run(request))

	@app.post("/dreaming/run", response_model=MemoryDreamingRunResponse)
	async def dreaming_run(request: MemoryDreamingRunRequest) -> MemoryDreamingRunResponse:
		return await _maybe_await(backing_store.dreaming_run(request))

	@app.get("/dreaming/status", response_model=MemoryDreamingStatusResponse)
	async def dreaming_status() -> MemoryDreamingStatusResponse:
		return await _maybe_await(backing_store.dreaming_status())

	@app.post("/dreaming/proposals", response_model=MemoryDreamingProposalListResponse)
	async def dreaming_proposals(request: MemoryDreamingProposalListRequest) -> MemoryDreamingProposalListResponse:
		return await _maybe_await(backing_store.dreaming_proposals(request))

	@app.post("/dreaming/proposals/decision", response_model=MemoryDreamingProposalDecisionResponse)
	async def dreaming_proposals_decision(request: MemoryDreamingProposalDecisionRequest) -> MemoryDreamingProposalDecisionResponse:
		return await _maybe_await(backing_store.dreaming_decide_proposal(request))

	@app.post("/dreaming/proposals/assurance", response_model=MemoryDreamingProposalAssuranceResponse)
	async def dreaming_proposals_assurance(request: MemoryDreamingProposalAssuranceRequest) -> MemoryDreamingProposalAssuranceResponse:
		return await _maybe_await(backing_store.dreaming_attach_assurance(request))

	@app.post("/dreaming/cleanup", response_model=MemoryDreamingCleanupResponse)
	async def dreaming_cleanup(request: MemoryDreamingCleanupRequest) -> MemoryDreamingCleanupResponse:
		return await _maybe_await(backing_store.dreaming_cleanup(request))

	@app.post("/validator/submit", response_model=ValidatorSubmitResponse)
	async def validator_submit(request: ValidatorSubmitRequest) -> ValidatorSubmitResponse:
		return await _maybe_await(backing_store.validator_submit(request))

	@app.post("/validator/status", response_model=ValidatorStatusResponse)
	async def validator_status(request: ValidatorStatusRequest) -> ValidatorStatusResponse:
		return await _maybe_await(backing_store.validator_status(request))

	@app.post("/validator/result", response_model=ValidatorResultResponse)
	async def validator_result(request: ValidatorResultRequest) -> ValidatorResultResponse:
		return await _maybe_await(backing_store.validator_result(request))

	@app.post("/retrieval/upsert", response_model=MemoryRetrievalResponse)
	async def upsert_retrieval(request: MemoryRetrievalUpsertRequest) -> MemoryRetrievalResponse:
		return await _maybe_await(backing_store.upsert_retrieval(request))

	@app.post("/retrieval/query", response_model=MemoryRetrievalResponse)
	async def query_retrieval(request: MemoryRetrievalQueryRequest) -> MemoryRetrievalResponse:
		return await _maybe_await(backing_store.query_retrieval(request))

	@app.post("/embedding/generate", response_model=MemoryEmbeddingResponse)
	async def generate_embedding(request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
		return await _maybe_await(backing_store.generate_embedding(request))

	@app.post("/context/search", response_model=ContextSearchResponse)
	async def context_search(request: ContextSearchRequest) -> ContextSearchResponse:
		return await _maybe_await(backing_store.context_search(request))

	@app.post("/context/upsert", response_model=ContextSearchResponse)
	async def context_upsert(request: ContextUpsertRequest) -> ContextSearchResponse:
		return await _maybe_await(backing_store.context_upsert(request))

	@app.post("/relations/upsert", response_model=RelationExpandResponse)
	async def relation_upsert(request: RelationUpsertRequest) -> RelationExpandResponse:
		return await _maybe_await(backing_store.relation_upsert(request))

	@app.post("/relations/expand", response_model=RelationExpandResponse)
	async def relation_expand(request: RelationExpandRequest) -> RelationExpandResponse:
		return await _maybe_await(backing_store.relation_expand(request))

	@app.post("/relations/cleanup-expired", response_model=RelationCleanupExpiredResponse)
	async def relation_cleanup_expired(request: RelationCleanupExpiredRequest) -> RelationCleanupExpiredResponse:
		return await _maybe_await(backing_store.relation_cleanup_expired(request))

	# ------------------------------------------------------------------
	# Graph v2 routes
	# ------------------------------------------------------------------

	@app.post("/graph/agent/upsert", response_model=GraphNodeResponse)
	async def graph_agent_upsert(request: GraphAgentUpsertRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_agent_upsert(request))

	@app.post("/graph/task/upsert", response_model=GraphNodeResponse)
	async def graph_task_upsert(request: GraphTaskUpsertRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_task_upsert(request))

	@app.post("/graph/context/upsert", response_model=GraphNodeResponse)
	async def graph_context_upsert(request: GraphContextUpsertRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_context_upsert(request))

	@app.post("/graph/fact/upsert", response_model=GraphNodeResponse)
	async def graph_fact_upsert(request: GraphFactUpsertRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_fact_upsert(request))

	@app.post("/graph/fact/link", response_model=GraphNodeResponse)
	async def graph_fact_link(request: GraphFactLinkRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_fact_link(request))

	@app.post("/graph/embedding/upsert", response_model=GraphNodeResponse)
	async def graph_embedding_upsert(request: GraphEmbeddingUpsertRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_embedding_upsert(request))

	@app.post("/graph/embedding/semantic-link", response_model=GraphNodeResponse)
	async def graph_semantic_link(request: GraphSemanticLinkRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_semantic_link(request))

	@app.post("/graph/tool/upsert", response_model=GraphNodeResponse)
	async def graph_tool_upsert(request: GraphToolUpsertRequest) -> GraphNodeResponse:
		return await _maybe_await(backing_store.graph_tool_upsert(request))

	@app.post("/graph/context/graph", response_model=GraphContextGraphResponse)
	async def graph_context_graph(request: GraphContextGraphRequest) -> GraphContextGraphResponse:
		return await _maybe_await(backing_store.graph_context_graph(request))

	@app.post("/graph/architecture/subgraph", response_model=GraphSubgraphResponse)
	async def graph_architecture_subgraph(request: GraphSubgraphRequest) -> GraphSubgraphResponse:
		return await _maybe_await(backing_store.architecture_subgraph(request))

	@app.get("/health", response_model=MemoryHealthResponse)
	async def health() -> MemoryHealthResponse:
		return await _maybe_await(backing_store.health())

	@app.get("/health/backends", response_model=MemoryHealthResponse)
	async def health_backends() -> MemoryHealthResponse:
		return await _maybe_await(backing_store.health_backends())

	return app


app = create_memory_service_app()

__all__ = ["app", "create_memory_service_app"]
