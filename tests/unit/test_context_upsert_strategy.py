"""Unit tests for orchestrator context upsert strategy defaults."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.contracts import ContextSearchResponse, MemoryServiceStatus
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


class _FakeGateway:
    invocation_mode = None


from services.memory_adapter import InProcessMemoryAdapter, MemoryServiceAdapter


class _CaptureMemoryAdapter(InProcessMemoryAdapter):
    def __init__(self):
        super().__init__(None)
        self.session_values = {}
        self.last_set = None
        self.last_context_upsert = None

    async def get(self, tier, key, default=None):
        return self.session_values.get((tier, key), default)

    async def set(self, tier, key, value, ttl_seconds=None):
        self.session_values[(tier, key)] = value
        self.last_set = {
            "tier": tier,
            "key": key,
            "value": value,
            "ttl_seconds": ttl_seconds,
        }
        return None

    async def upsert_retrieval(self, request):
        self.last_context_upsert = request
        return SimpleNamespace(items=[])

    async def graph_fact_upsert(
        self, *, fact_id: str, text: str, source: str, context_id: str | None = None,
        agent_id: str | None = None, task_id: str | None = None, embedding_id: str | None = None,
    ):
        del fact_id, text, source, context_id, agent_id, task_id, embedding_id
        return SimpleNamespace(ok=True)

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED"):
        del fact_a_id, fact_b_id, relation_type
        return SimpleNamespace(ok=True)

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None):
        del embedding_id, vector_ref, dim
        return SimpleNamespace(ok=True)

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float):
        del emb_a_id, emb_b_id, score
        return SimpleNamespace(ok=True)

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None):
        del name, version, category
        return SimpleNamespace(ok=True)

    async def graph_context_graph(self, *, context_id: str, limit: int = 20):
        del context_id, limit
        return SimpleNamespace(items=[])


@pytest.mark.asyncio
async def test_temp_context_note_uses_one_hour_ttl_and_mandatory_metadata_defaults():
    memory = _CaptureMemoryAdapter()
    orchestrator = Orchestrator(tool_coordinator=object(), inference_gateway=_FakeGateway(), memory_layer=memory)

    await orchestrator._upsert_temp_context_note(
        session_id="session-1",
        run_id="run-1",
        note_kind="assistant_draft",
        content="Bitte pruefe die Architektur fuer den Memory-Service.",
    )

    assert memory.last_set is not None
    assert memory.last_set["ttl_seconds"] == 3600
    note = memory.last_set["value"][-1]
    metadata = note["metadata"]
    assert metadata["source"] == "reasoning_loop"
    assert metadata["artifact_type"] == "assistant_draft"
    assert metadata["validation_status"] == "unvalidated"
    assert metadata["scope"] == "session"
    assert metadata["created_by"] == "liara"
    assert metadata["language"] == "de"
    assert metadata["reasoning_step"] == 1


@pytest.mark.asyncio
async def test_working_context_doc_adds_mandatory_metadata_defaults():
    memory = _CaptureMemoryAdapter()
    orchestrator = Orchestrator(tool_coordinator=object(), inference_gateway=_FakeGateway(), memory_layer=memory)

    await orchestrator._upsert_working_context_doc(
        session_id="session-2",
        run_id="run-2",
        document_id="run-2:working_context",
        content="Current architecture discussion summary for Memory-Service split.",
        turn_index=4,
        metadata={"source": "orchestrator", "reasoning_step": 2},
    )

    assert memory.last_context_upsert is not None
    assert memory.last_context_upsert.memory_tier == "working"
    assert memory.last_context_upsert.ttl_seconds >= 300
    assert memory.last_context_upsert.expires_at is not None
    assert memory.last_context_upsert.promotion_state == "none"
    assert memory.last_set is not None
    assert memory.last_set["tier"] == MemoryTier.SESSION
    assert memory.last_set["key"] == "workflow_active:session-2"
    assert memory.last_set["ttl_seconds"] == memory.last_context_upsert.ttl_seconds
    metadata = memory.last_context_upsert.metadata
    assert metadata["source"] == "orchestrator"
    assert metadata["artifact_type"] == "working_context"
    assert metadata["validation_status"] == "validated"
    assert metadata["scope"] == "working_context"
    assert metadata["created_by"] == "liara"
    assert metadata["language"] == "en"
    assert metadata["reasoning_step"] == 2
