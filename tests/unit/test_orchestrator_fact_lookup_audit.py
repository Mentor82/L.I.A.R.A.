from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.memory_adapter import MemoryServiceAdapter
from services.contracts import OrchestratorRequest
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


class _MinimalMemoryAdapter(MemoryServiceAdapter):
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
        return SimpleNamespace(items=[])

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


@pytest.mark.asyncio
async def test_validate_response_logs_logic_error_for_fact_lookup_without_reference(monkeypatch):
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=_MinimalMemoryAdapter(),
    )

    calls: list[dict[str, str]] = []

    def _capture_log(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("services.orchestrator.orchestrator.log_judge_pre_action", _capture_log)

    validation = await orchestrator._validate_response(
        run_id="run-logic-1",
        query="Wie heisse ich?",
        response="Du heisst Nora.",
        tools_used=[],
        tool_outputs={},
        context_debug={
            "mode": "MEMORY",
            "sources": {"postgres": 1},
            "librarian": {"route": "FACT_LOOKUP"},
        },
    )

    assert validation.checks.get("fact_lookup_reference") == "fail"
    assert any("FACT_LOOKUP response missing [KNOWLEDGE_REFERENCE]" in issue for issue in validation.issues)
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "fact_lookup_reference"
    assert calls[0]["decision"] == "block"
    assert calls[0]["context"] == "logic_error_missing_knowledge_reference"
    assert calls[0]["request_id"] == "run-logic-1"
    assert calls[0]["run_id"] == "run-logic-1"
    assert calls[0]["session_id"] is None
    assert calls[0]["source"] == "orchestrator"


@pytest.mark.asyncio
async def test_validate_response_uses_session_fallback_when_run_id_missing(monkeypatch):
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=_MinimalMemoryAdapter(),
    )
    orchestrator._active_session_id = "session-fallback-1"

    calls: list[dict[str, str]] = []

    def _capture_log(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("services.orchestrator.orchestrator.log_judge_pre_action", _capture_log)

    validation = await orchestrator._validate_response(
        run_id="",
        query="Wie heisse ich?",
        response="Du heisst Nora.",
        tools_used=[],
        tool_outputs={},
        context_debug={
            "mode": "MEMORY",
            "sources": {"postgres": 1},
            "librarian": {"route": "FACT_LOOKUP"},
        },
    )

    assert validation.checks.get("fact_lookup_reference") == "fail"
    assert len(calls) == 1
    assert calls[0]["request_id"] == "session-fallback-1"
    assert calls[0]["run_id"] is None
    assert calls[0]["session_id"] == "session-fallback-1"
    assert calls[0]["source"] == "orchestrator"


@pytest.mark.asyncio
async def test_validate_response_passes_fact_lookup_reference_when_present(monkeypatch):
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=_MinimalMemoryAdapter(),
    )

    calls: list[dict[str, str]] = []

    def _capture_log(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("services.orchestrator.orchestrator.log_judge_pre_action", _capture_log)

    validation = await orchestrator._validate_response(
        run_id="run-logic-2",
        query="Was ist meine Lieblingsfarbe?",
        response="Deine Lieblingsfarbe ist Cyan. [KNOWLEDGE_REFERENCE]",
        tools_used=[],
        tool_outputs={},
        context_debug={
            "mode": "MEMORY",
            "sources": {"postgres": 1},
            "librarian": {"route": "FACT_LOOKUP"},
        },
    )

    assert validation.checks.get("fact_lookup_reference") == "pass"
    assert not any("FACT_LOOKUP response missing [KNOWLEDGE_REFERENCE]" in issue for issue in validation.issues)
    assert calls == []


@pytest.mark.asyncio
async def test_run_uses_raw_routing_query_when_attachments_expand_prompt(monkeypatch):
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_StubInferenceGateway(),
        memory_layer=_MinimalMemoryAdapter(),
    )

    observed: dict[str, object] = {}

    async def _fake_select_tools(query, tools_override=None):
        observed["select_query"] = query
        return []

    async def _fake_execute_tools(tool_names, query, run_id=None):
        observed["execute_query"] = query
        return {}

    async def _fake_generate_llm_response(
        run_id,
        query,
        routing_query,
        session_id,
        tools_used,
        tool_outputs,
        max_tokens,
        preferred_provider,
        preferred_model,
        force_context,
        retry_directive,
        retry_attempt,
        gap_action,
        previous_compressed_context,
    ):
        del run_id, session_id, tools_used, tool_outputs, max_tokens
        del preferred_provider, preferred_model, force_context, retry_directive
        del retry_attempt, gap_action, previous_compressed_context
        observed["llm_query"] = query
        observed["llm_routing_query"] = routing_query
        return {
            "content": "Analyse abgeschlossen.",
            "provider": "stub",
            "model": "stub",
            "ttft_ms": 0.0,
            "gen_ms": 0.0,
            "winner_provider": "stub",
            "status": "ok",
            "error": None,
            "stop_reason": "stop",
            "inference_metadata": {},
            "context_debug": {
                "mode": "CONTEXT",
                "sources": {"chroma": 0, "qdrant": 0, "postgres": 0, "neo4j": 0},
                "librarian": {"route": "SEMANTIC_MEMORY"},
            },
            "compressed_context": "",
            "compression": {
                "summary": "",
                "facts": [],
                "relations": [],
                "dropped_items": [],
                "token_estimate": 0,
                "metadata": {},
                "no_new_information": False,
                "meaningful_reduction": True,
            },
        }

    async def _fake_validate_response(**kwargs):
        return SimpleNamespace(
            passed=True,
            decision="accept",
            checks={},
            issues=[],
            confidence_score=1.0,
            suggestions=None,
        )

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(orchestrator, "_select_tools", _fake_select_tools)
    monkeypatch.setattr(orchestrator, "_execute_tools", _fake_execute_tools)
    monkeypatch.setattr(orchestrator, "_generate_llm_response", _fake_generate_llm_response)
    monkeypatch.setattr(orchestrator, "_validate_response", _fake_validate_response)
    monkeypatch.setattr(orchestrator, "_upsert_temp_context_note", _noop_async)
    monkeypatch.setattr(orchestrator, "_upsert_working_context_doc", _noop_async)
    monkeypatch.setattr(orchestrator, "_upsert_validated_relations", _noop_async)

    raw_message = "Bitte werte den aktuellen Code im Anhang aus."
    effective_query = (
        raw_message
        + "\n\nBereitgestellte Dateien/Anhänge:\n[Attachment 1: name=app.py]\n"
        + "API_VERSION = '1.0'\nPORT = 8000\n"
    )

    response = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-1",
            run_id="run-attachment-route",
            user_id="user-1",
            query=effective_query,
            routing_query=raw_message,
        )
    )

    assert observed["select_query"] == raw_message
    assert observed["execute_query"] == raw_message
    assert observed["llm_query"] == effective_query
    assert observed["llm_routing_query"] == raw_message
    assert response.final_response == "Analyse abgeschlossen."
