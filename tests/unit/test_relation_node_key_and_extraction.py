"""Tests for relation node-key normalization and LLM-based relation extraction."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.contracts import (
    InferenceResult,
    MemoryServiceStatus,
    RelationType,
    RelationUpsertRequest,
)
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

class _MinimalMemory(MemoryServiceAdapter):
    def __init__(self):
        self.relation_calls: list[RelationUpsertRequest] = []

    async def get(self, tier, key, default=None):
        return default

    async def set(self, tier, key, value, ttl_seconds=None):
        return None

    async def delete(self, tier, key):
        return None

    async def exists(self, tier, key) -> bool:
        return False

    async def append_history(self, request):
        return SimpleNamespace(items=[])

    async def query_history(self, request):
        return SimpleNamespace(items=[])

    async def upsert_fact(self, request):
        return SimpleNamespace(items=[])

    async def query_facts(self, request):
        return SimpleNamespace(items=[])

    async def embed(self, request):
        return SimpleNamespace(vector=[0.0])

    async def query_retrieval(self, request):
        return SimpleNamespace(items=[])

    async def upsert_retrieval(self, request):
        return SimpleNamespace(items=[])

    async def health(self):
        return MemoryServiceStatus(status="ok")

    async def relation_upsert(self, request: RelationUpsertRequest):
        self.relation_calls.append(request)
        return SimpleNamespace(items=[])

    async def relation_expand(self, request):
        return SimpleNamespace(items=[])

    async def generate_embedding(self, request):
        return SimpleNamespace(vector=[0.0])

    async def context_search(self, request):
        return SimpleNamespace(items=[])

    async def context_upsert(self, request):
        return SimpleNamespace(items=[])

    async def graph_agent_upsert(self, *, agent_id: str, role: str | None = None, version: str | None = None):
        return SimpleNamespace()

    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None):
        return SimpleNamespace()

    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session"):
        return SimpleNamespace()

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
        return SimpleNamespace()

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED"):
        return SimpleNamespace()

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None):
        return SimpleNamespace()

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float):
        return SimpleNamespace()

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None):
        return SimpleNamespace()

    async def graph_context_graph(self, *, context_id: str, limit: int = 20):
        return SimpleNamespace(nodes=[], edges=[])


def _make_orchestrator(memory: _MinimalMemory, inference_invoker=None):
    inference_gw = SimpleNamespace(invocation_mode=None)
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_service = memory
    orch.inference_gateway = inference_gw
    orch.inference_invoker = inference_invoker or SimpleNamespace(infer=AsyncMock())
    orch.default_inference_provider = "test"
    return orch


# ---------------------------------------------------------------------------
# _relation_node_key tests
# ---------------------------------------------------------------------------

class TestRelationNodeKey:
    def test_stable_same_input(self):
        key1 = Orchestrator._relation_node_key("query", "What is Berlin?")
        key2 = Orchestrator._relation_node_key("query", "What is Berlin?")
        assert key1 == key2

    def test_stable_across_whitespace_variants(self):
        key1 = Orchestrator._relation_node_key("query", "  What  is Berlin? ")
        key2 = Orchestrator._relation_node_key("query", "What is Berlin?")
        assert key1 == key2

    def test_stable_case_insensitive(self):
        key1 = Orchestrator._relation_node_key("query", "BERLIN ist toll")
        key2 = Orchestrator._relation_node_key("query", "berlin ist toll")
        assert key1 == key2

    def test_different_texts_give_different_keys(self):
        key1 = Orchestrator._relation_node_key("query", "What is Paris?")
        key2 = Orchestrator._relation_node_key("query", "What is Berlin?")
        assert key1 != key2

    def test_prefix_is_preserved(self):
        key = Orchestrator._relation_node_key("response", "Some answer text")
        assert key.startswith("response:")

    def test_key_contains_sha1_suffix(self):
        import hashlib, re
        text = "hello world"
        key = Orchestrator._relation_node_key("q", text)
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        digest = hashlib.sha1(normalized.encode()).hexdigest()[:8]
        assert key.endswith(f":{digest}")

    def test_long_text_truncated_in_slug(self):
        long_text = "a" * 200
        key = Orchestrator._relation_node_key("query", long_text)
        # slug portion should not exceed 48 chars
        parts = key.split(":")
        # format: prefix:slug:hash  (slug may have underscores replacing spaces)
        slug_part = parts[1]
        assert len(slug_part) <= 48


# ---------------------------------------------------------------------------
# _extract_content_relations tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExtractContentRelations:
    async def test_returns_empty_when_disabled(self):
        mem = _MinimalMemory()
        orch = _make_orchestrator(mem)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", False):
            result = await orch._extract_content_relations(
                query="What is Berlin?",
                response="Berlin is the capital of Germany.",
                session_id="s1",
                run_id="r1",
            )
        assert result == []

    async def test_parses_valid_llm_json(self):
        mem = _MinimalMemory()
        llm_output = json.dumps([
            {"source": "Berlin", "relation": "REFERENCES", "target": "Germany"},
        ])
        invoker = SimpleNamespace(
            infer=AsyncMock(return_value=InferenceResult(
                content=llm_output, provider="test", model="test", status="success"
            ))
        )
        orch = _make_orchestrator(mem, invoker)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", True):
            with patch("services.config.settings.Settings.RELATION_EXTRACTION_MAX_TRIPLES", 5):
                result = await orch._extract_content_relations(
                    query="What is Berlin?",
                    response="Berlin is the capital of Germany.",
                    session_id="s1",
                    run_id="r1",
                )
        assert len(result) == 1
        assert result[0].relation == RelationType.REFERENCES

    async def test_rejects_unknown_relation_types(self):
        mem = _MinimalMemory()
        llm_output = json.dumps([
            {"source": "A", "relation": "MADE_UP_TYPE", "target": "B"},
            {"source": "C", "relation": "DEPENDS_ON", "target": "D"},
        ])
        invoker = SimpleNamespace(
            infer=AsyncMock(return_value=InferenceResult(
                content=llm_output, provider="test", model="test", status="success"
            ))
        )
        orch = _make_orchestrator(mem, invoker)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", True):
            with patch("services.config.settings.Settings.RELATION_EXTRACTION_MAX_TRIPLES", 5):
                result = await orch._extract_content_relations(
                    query="q", response="r", session_id="s", run_id="r",
                )
        # MADE_UP_TYPE must be skipped, DEPENDS_ON must be accepted
        assert len(result) == 1
        assert result[0].relation == RelationType.DEPENDS_ON

    async def test_accepts_new_semantic_relation_types(self):
        mem = _MinimalMemory()
        llm_output = json.dumps([
            {"source": "A", "relation": "DESCRIBES", "target": "B"},
            {"source": "B", "relation": "SUPPORTS", "target": "C"},
            {"source": "C", "relation": "CONTRADICTS", "target": "D"},
            {"source": "D", "relation": "RESOLVES", "target": "E"},
        ])
        invoker = SimpleNamespace(
            infer=AsyncMock(return_value=InferenceResult(
                content=llm_output, provider="test", model="test", status="success"
            ))
        )
        orch = _make_orchestrator(mem, invoker)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", True):
            with patch("services.config.settings.Settings.RELATION_EXTRACTION_MAX_TRIPLES", 10):
                result = await orch._extract_content_relations(
                    query="q", response="r", session_id="s", run_id="r",
                )

        assert [edge.relation for edge in result] == [
            RelationType.DESCRIBES,
            RelationType.SUPPORTS,
            RelationType.CONTRADICTS,
            RelationType.RESOLVES,
        ]

    async def test_graceful_on_malformed_json(self):
        mem = _MinimalMemory()
        invoker = SimpleNamespace(
            infer=AsyncMock(return_value=InferenceResult(
                content="This is not JSON at all.", provider="test", model="test", status="success"
            ))
        )
        orch = _make_orchestrator(mem, invoker)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", True):
            with patch("services.config.settings.Settings.RELATION_EXTRACTION_MAX_TRIPLES", 5):
                result = await orch._extract_content_relations(
                    query="q", response="r", session_id="s", run_id="r",
                )
        assert result == []

    async def test_respects_max_triples_limit(self):
        mem = _MinimalMemory()
        llm_output = json.dumps([
            {"source": f"A{i}", "relation": "REFERENCES", "target": f"B{i}"}
            for i in range(10)
        ])
        invoker = SimpleNamespace(
            infer=AsyncMock(return_value=InferenceResult(
                content=llm_output, provider="test", model="test", status="success"
            ))
        )
        orch = _make_orchestrator(mem, invoker)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", True):
            with patch("services.config.settings.Settings.RELATION_EXTRACTION_MAX_TRIPLES", 3):
                result = await orch._extract_content_relations(
                    query="q", response="r", session_id="s", run_id="r",
                )
        assert len(result) == 3

    async def test_node_keys_are_normalized_in_output(self):
        mem = _MinimalMemory()
        llm_output = json.dumps([
            {"source": "  Berlin  ", "relation": "REFERENCES", "target": "Germany"},
        ])
        invoker = SimpleNamespace(
            infer=AsyncMock(return_value=InferenceResult(
                content=llm_output, provider="test", model="test", status="success"
            ))
        )
        orch = _make_orchestrator(mem, invoker)
        with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", True):
            with patch("services.config.settings.Settings.RELATION_EXTRACTION_MAX_TRIPLES", 5):
                result = await orch._extract_content_relations(
                    query="q", response="r", session_id="s", run_id="r",
                )
        assert result[0].source.startswith("entity:")
        assert result[0].target.startswith("entity:")
        # Normalized "berlin" and "  berlin  " must yield the same node key
        key_trimmed = Orchestrator._relation_node_key("entity", "Berlin")
        key_padded = Orchestrator._relation_node_key("entity", "  Berlin  ")
        assert key_trimmed == key_padded


# ---------------------------------------------------------------------------
# Integration: structural relations also use normalized keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_validated_relations_uses_normalized_keys():
    mem = _MinimalMemory()
    orch = _make_orchestrator(mem)
    # Identical query text should always produce the same node key regardless of
    # surrounding whitespace
    query = "  What is Berlin?  "
    with patch("services.config.settings.Settings.RELATION_EXTRACTION_ENABLED", False):
        await orch._upsert_validated_relations(
            session_id="s1",
            run_id="r1",
            query=query,
            tools_used=[],
            final_response="Berlin is a city.",
        )
    assert len(mem.relation_calls) >= 1
    req = next(
        call for call in mem.relation_calls
        if call.relation == RelationType.DIRECT_RESPONSE
    )
    expected_source = Orchestrator._relation_node_key("query", query)
    assert req.source == expected_source
    assert "query:" in req.source
    # No raw query text should appear verbatim in the key
    assert "What is Berlin" not in req.source
