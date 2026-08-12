from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.contracts import MemoryFactRecord, MemoryLifecycleStatus, MemoryServiceStatus
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.librarian_router import LibrarianDecision
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


class _FactsMemoryAdapter(MemoryServiceAdapter):
    def __init__(self, fact_items: list[MemoryFactRecord]):
        self._fact_items = fact_items

    async def get(self, tier: MemoryTier, key: str, default=None):
        return default

    async def set(self, tier: MemoryTier, key: str, value, ttl_seconds=None):
        return None

    async def delete(self, tier: MemoryTier, key: str):
        return None

    async def exists(self, tier: MemoryTier, key: str) -> bool:
        return False

    async def append_history(self, request):
        return SimpleNamespace(items=[])

    async def query_history(self, request):
        return SimpleNamespace(items=[])

    async def upsert_fact(self, request):
        return SimpleNamespace(items=[])

    async def query_facts(self, request):
        items = [
            item
            for item in self._fact_items
            if item.namespace == request.namespace and (request.key is None or item.key == request.key)
        ]
        return SimpleNamespace(items=items, status=MemoryServiceStatus(status="success", backend="postgres"))

    async def upsert_retrieval(self, request):
        return SimpleNamespace(items=[])

    async def query_retrieval(self, request):
        return SimpleNamespace(items=[])

    async def generate_embedding(self, request):
        return SimpleNamespace(item=None)

    async def context_search(self, request):
        return SimpleNamespace(items=[])

    async def context_upsert(self, request):
        return SimpleNamespace(items=[])

    async def relation_upsert(self, request):
        return SimpleNamespace(items=[])

    async def relation_expand(self, request):
        return SimpleNamespace(items=[])

    async def graph_agent_upsert(self, *, agent_id: str, role: str | None = None, version: str | None = None):
        return SimpleNamespace(item=None)

    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None):
        return SimpleNamespace(item=None)

    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session"):
        return SimpleNamespace(item=None)

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
    ):
        return SimpleNamespace(item=None)

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED"):
        return SimpleNamespace(item=None)

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None):
        return SimpleNamespace(item=None)

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float):
        return SimpleNamespace(item=None)

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None):
        return SimpleNamespace(item=None)

    async def graph_context_graph(self, *, context_id: str, limit: int = 20):
        return SimpleNamespace(items=[])


class _StubInferenceGateway:
    async def infer(self, request):
        return SimpleNamespace(
            content="ok",
            provider="stub",
            model="stub",
            ttft_ms=0.0,
            gen_ms=0.0,
            winner_provider="stub",
            status="success",
            error=None,
            stop_reason="stop",
            metadata={},
        )


def _fact(namespace: str, key: str, value: str, status: MemoryLifecycleStatus) -> MemoryFactRecord:
    return MemoryFactRecord(
        fact_id=f"fact-{namespace}-{key}-{value}",
        namespace=namespace,
        key=key,
        value=value,
        source="test",
        confidence=1.0,
        status=status,
        tags=[],
        created_at="2026-07-13T00:00:00+00:00",
        updated_at=None,
        metadata={},
    )


@pytest.mark.asyncio
async def test_load_librarian_context_prefers_verified_facts_and_excludes_staged_from_ground_truth():
    namespace = "session:s-facts:facts"
    memory = _FactsMemoryAdapter(
        [
            _fact(namespace, "name", "Nora", MemoryLifecycleStatus.verified),
            _fact(namespace, "name", "Nora-Draft", MemoryLifecycleStatus.staged),
        ]
    )
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=memory,
    )
    orchestrator._build_embedding_query = lambda **kwargs: (
        kwargs.get("current_user_input", ""),
        {
            "input_chars": len(kwargs.get("current_user_input", "")),
            "embedding_chars": len(kwargs.get("current_user_input", "")),
            "token_length": 1,
            "topic_used": False,
            "history_used": False,
            "constraints": [],
        },
    )

    channels, counts = await orchestrator._load_librarian_context(
        run_id="run-facts-1",
        session_id="s-facts",
        query="Wie heisse ich?",
        conversation_history="",
        force_context=False,
        limit=3,
        librarian=LibrarianDecision(
            route="FACT_LOOKUP",
            reason="test",
            primary_source="postgres",
            fact_key="name",
            fact_namespaces=[namespace],
            load_facts=True,
        ),
    )

    fact_context = channels.get("fact_context", "")
    assert "[fact_verified:" in fact_context
    assert "Nora" in fact_context
    assert "Nora-Draft" not in fact_context
    assert "[fact_hint:" not in fact_context
    assert counts["facts"] == 2


@pytest.mark.asyncio
async def test_load_librarian_context_uses_non_verified_as_hint_when_no_verified_fact_exists():
    namespace = "session:s-hint:facts"
    memory = _FactsMemoryAdapter(
        [
            _fact(namespace, "nickname", "Nori", MemoryLifecycleStatus.candidate),
            _fact(namespace, "nickname", "Draft-Nori", MemoryLifecycleStatus.staged),
        ]
    )
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=memory,
    )
    orchestrator._build_embedding_query = lambda **kwargs: (
        kwargs.get("current_user_input", ""),
        {
            "input_chars": len(kwargs.get("current_user_input", "")),
            "embedding_chars": len(kwargs.get("current_user_input", "")),
            "token_length": 1,
            "topic_used": False,
            "history_used": False,
            "constraints": [],
        },
    )

    channels, counts = await orchestrator._load_librarian_context(
        run_id="run-facts-2",
        session_id="s-hint",
        query="Welchen Spitznamen habe ich?",
        conversation_history="",
        force_context=False,
        limit=3,
        librarian=LibrarianDecision(
            route="FACT_LOOKUP",
            reason="test",
            primary_source="postgres",
            fact_key="nickname",
            fact_namespaces=[namespace],
            load_facts=True,
        ),
    )

    fact_context = channels.get("fact_context", "")
    assert "[fact_hint:" in fact_context
    assert "candidate" in fact_context
    assert "Nori" in fact_context
    assert "Draft-Nori" not in fact_context
    assert counts["facts"] == 2


@pytest.mark.asyncio
async def test_staged_fact_not_ground_truth():
    namespace = "session:s-ground-truth:facts"
    memory = _FactsMemoryAdapter(
        [
            _fact(namespace, "name", "Nora", MemoryLifecycleStatus.verified),
            _fact(namespace, "name", "Nora-Staged", MemoryLifecycleStatus.staged),
        ]
    )
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=memory,
    )
    orchestrator._build_embedding_query = lambda **kwargs: (
        kwargs.get("current_user_input", ""),
        {
            "input_chars": len(kwargs.get("current_user_input", "")),
            "embedding_chars": len(kwargs.get("current_user_input", "")),
            "token_length": 1,
            "topic_used": False,
            "history_used": False,
            "constraints": [],
        },
    )

    channels, _counts = await orchestrator._load_librarian_context(
        run_id="run-facts-ground-truth-1",
        session_id="s-ground-truth",
        query="Wie heiße ich?",
        conversation_history="",
        force_context=False,
        limit=3,
        librarian=LibrarianDecision(
            route="FACT_LOOKUP",
            reason="test",
            primary_source="postgres",
            fact_key="name",
            fact_namespaces=[namespace],
            load_facts=True,
        ),
    )

    fact_context = channels.get("fact_context", "")
    assert "[fact_verified:" in fact_context
    assert "Nora" in fact_context
    assert "Nora-Staged" not in fact_context
    assert "[fact_hint:" not in fact_context
