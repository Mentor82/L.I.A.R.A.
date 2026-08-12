from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.contracts import MemoryServiceStatus, RelationType
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.librarian_router import LibrarianDecision
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


class _GraphMemoryAdapter(MemoryServiceAdapter):
    def __init__(self, *, relation_items: list[SimpleNamespace]):
        self._relation_items = relation_items

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
        return SimpleNamespace(items=[], status=MemoryServiceStatus(status="success", backend="postgres"))

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
        return SimpleNamespace(items=list(self._relation_items))

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


def _make_orchestrator(memory: MemoryServiceAdapter) -> Orchestrator:
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
    return orchestrator


def test_extract_graph_relations_from_relation_context_for_validator_handoff():
    relation_context = (
        "[graph_guardrail] Direct graph relations are authoritative.\n"
        "[relation] service:api -[DEPENDS_ON]-> service:memory\n"
        "[relation] service:memory -[USES_TOOL]-> service:embedding"
    )

    relations = Orchestrator._extract_graph_relations_from_context(relation_context)

    assert relations == [
        {"source": "service:api", "relation": "DEPENDS_ON", "target": "service:memory"},
        {"source": "service:memory", "relation": "USES_TOOL", "target": "service:embedding"},
    ]


@pytest.mark.asyncio
async def test_relation_context_includes_graph_guardrail_when_relations_exist():
    memory = _GraphMemoryAdapter(
        relation_items=[SimpleNamespace(source="service:api", relation="DEPENDS_ON", target="service:memory")]
    )
    orchestrator = _make_orchestrator(memory)

    channels, counts = await orchestrator._load_librarian_context(
        run_id="run-graph-1",
        session_id="sess-graph-1",
        query="Welche Services haengen voneinander ab?",
        conversation_history="",
        force_context=False,
        librarian=LibrarianDecision(
            route="RELATION_LOOKUP",
            reason="test",
            primary_source="neo4j",
            load_relations=True,
        ),
    )

    relation_context = channels.get("relation_context", "")
    lines = [line.strip() for line in relation_context.splitlines() if line.strip()]
    assert lines
    assert lines[0].startswith("[graph_guardrail]")
    assert any("[relation] service:api -[DEPENDS_ON]-> service:memory" in line for line in lines)
    assert counts["neo4j"] == 1


@pytest.mark.asyncio
async def test_relation_context_has_no_guardrail_when_no_relations_exist():
    memory = _GraphMemoryAdapter(relation_items=[])
    orchestrator = _make_orchestrator(memory)

    channels, counts = await orchestrator._load_librarian_context(
        run_id="run-graph-2",
        session_id="sess-graph-2",
        query="Welche Services haengen voneinander ab?",
        conversation_history="",
        force_context=False,
        librarian=LibrarianDecision(
            route="RELATION_LOOKUP",
            reason="test",
            primary_source="neo4j",
            load_relations=True,
        ),
    )

    relation_context = channels.get("relation_context", "")
    assert relation_context == ""
    assert counts["neo4j"] == 0


@pytest.mark.asyncio
async def test_relation_context_serializes_relation_enum_value():
    memory = _GraphMemoryAdapter(
        relation_items=[
            SimpleNamespace(
                source="service:api",
                relation=RelationType.DEPENDS_ON,
                target="service:memory",
            )
        ]
    )
    orchestrator = _make_orchestrator(memory)

    channels, counts = await orchestrator._load_librarian_context(
        run_id="run-graph-3",
        session_id="sess-graph-3",
        query="Welche Services haengen voneinander ab?",
        conversation_history="",
        force_context=False,
        librarian=LibrarianDecision(
            route="RELATION_LOOKUP",
            reason="test",
            primary_source="neo4j",
            load_relations=True,
        ),
    )

    assert "[relation] service:api -[DEPENDS_ON]-> service:memory" in channels["relation_context"]
    assert "RelationType.DEPENDS_ON" not in channels["relation_context"]
    assert counts["neo4j"] == 1
