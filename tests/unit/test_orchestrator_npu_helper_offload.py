"""Unit tests for orchestrator NPU helper offload routing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.contracts import (
    InferenceResult,
    InputSituationProfile,
    OrchestratorRequest,
    ValidationResult,
)
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


class _FakeInferenceGateway:
    def __init__(self):
        self.calls = []

    async def infer(self, request):
        self.calls.append(request)
        if request.provider == "openvino_npu_helper":
            return InferenceResult(
                content="",
                provider="openvino_npu_helper",
                model="ov-npu",
                status="failed",
                error="helper unavailable",
                stop_reason="error",
                metadata={"helper_schema_ok": False},
            )
        return InferenceResult(
            content="main-path-ok",
            provider=request.provider,
            model="main-model",
            status="success",
            stop_reason="stop",
            metadata={},
        )


class _FakeMemoryAdapter(MemoryServiceAdapter):
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
        return SimpleNamespace(items=[])

    async def graph_context_graph(self, *, context_id: str, limit: int = 20):
        return SimpleNamespace(items=[])

    async def graph_context_upsert(self, *, context_id: str, context_type: str = "session"):
        return SimpleNamespace(items=[])

    async def graph_embedding_upsert(self, *, embedding_id: str, vector_ref: str | None = None, dim: int | None = None):
        return SimpleNamespace(items=[])

    async def graph_fact_link(self, *, fact_a_id: str, fact_b_id: str, relation_type: str = "RELATED"):
        return SimpleNamespace(items=[])

    async def graph_fact_upsert(
        self, *, fact_id: str, text: str, source: str, context_id: str | None = None,
        agent_id: str | None = None, task_id: str | None = None, embedding_id: str | None = None,
    ):
        return SimpleNamespace(items=[])

    async def graph_semantic_link(self, *, emb_a_id: str, emb_b_id: str, score: float):
        return SimpleNamespace(items=[])

    async def graph_task_upsert(self, *, task_id: str, status: str | None = None, agent_id: str | None = None):
        return SimpleNamespace(items=[])

    async def graph_tool_upsert(self, *, name: str, version: str | None = None, category: str | None = None):
        return SimpleNamespace(items=[])


class _AcceptValidator:
    def validate(self, _context):
        return ValidationResult(
            passed=True,
            decision="accept",
            checks={"fast_check": "pass"},
            issues=[],
            confidence_score=0.99,
        )


def _enable_compact_context(orchestrator: Orchestrator) -> None:
    async def _fake_initialize():
        pass

    async def _fake_profile(*args, **kwargs):
        return InputSituationProfile(domain="general", intent="chat")

    orchestrator.input_profiler = SimpleNamespace(
        initialize=_fake_initialize,
        profile=_fake_profile,
    )
    orchestrator.context_compressor = SimpleNamespace(
        compress=lambda **kwargs: SimpleNamespace(
            final_context="",
            summary="",
            facts=[],
            relations=[],
            dropped_items=[],
            token_estimate=0,
            metadata={},
            no_new_information=False,
            meaningful_reduction=True,
        )
    )


def test_workspace_planning_uses_scheduler_main_path_not_npu_helper():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )
    orchestrator._active_request_source = "assistant"

    provider, routing = orchestrator._select_inference_provider_for_step(
        preferred_provider=None,
        query="Lege einen Translator-Worker im Workspace an und implementiere ihn.",
        tools_used=["sys"],
        tool_outputs={},
        force_context=True,
        retry_attempt=0,
    )

    assert provider == orchestrator.default_inference_provider
    assert provider != orchestrator.npu_helper_provider
    assert routing["helper_offload_used"] is False
    assert routing["helper_offload_reason"] == "default_main_path"


@pytest.mark.asyncio
async def test_orchestrator_helper_offload_falls_back_to_main_provider():
    gateway = _FakeInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=gateway,
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _AcceptValidator()
    _enable_compact_context(orchestrator)

    result = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-helper-fallback",
            run_id="run-helper-fallback",
            user_id="user-1",
            query="Extrahiere 3 Kernpunkte als JSON.",
            request_source="assistant",
            max_tokens=220,
        )
    )

    assert len(gateway.calls) >= 2
    assert gateway.calls[0].provider == "openvino_npu_helper"
    assert gateway.calls[0].task_type == "quick_extract"
    assert gateway.calls[0].expected_fields == ["task_id", "key_points", "confidence"]
    assert gateway.calls[1].provider == orchestrator.default_inference_provider
    routing = result.llm_generation["context_debug"]["routing"]
    assert routing["helper_offload_used"] is True
    assert routing["helper_fallback_triggered"] is True
    assert routing["helper_schema_ok"] is False
    assert routing["helper_task_type"] == "quick_extract"
    assert routing["routing_class"] == "npu_helper_offload"
    assert routing["fallback_depth"] >= 1
    assert "breaker_state" in routing


@pytest.mark.asyncio
async def test_orchestrator_co_worker_skips_helper_offload():
    gateway = _FakeInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=gateway,
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _AcceptValidator()
    _enable_compact_context(orchestrator)

    result = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-coworker-main",
            run_id="run-coworker-main",
            user_id="user-1",
            query="Extrahiere 3 Kernpunkte als JSON.",
            request_source="co_worker",
            max_tokens=220,
        )
    )

    assert gateway.calls
    assert gateway.calls[0].provider == orchestrator.co_worker_main_provider
    routing = result.llm_generation["context_debug"]["routing"]
    assert routing["helper_offload_used"] is False
    assert routing["helper_fallback_triggered"] is False
    assert routing["helper_offload_reason"] == "co_worker_locked_main_path"
    assert routing["co_worker_provider_locked"] is True
    assert routing["co_worker_locked_provider"] == orchestrator.co_worker_main_provider
    assert routing["routing_class"] == "co_worker"
    assert routing["fallback_depth"] == 0
    assert "breaker_state" in routing


@pytest.mark.asyncio
async def test_orchestrator_co_worker_lock_ignores_explicit_preferred_provider():
    gateway = _FakeInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=gateway,
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _AcceptValidator()
    _enable_compact_context(orchestrator)

    result = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-coworker-lock",
            run_id="run-coworker-lock",
            user_id="user-1",
            query="Extrahiere 3 Kernpunkte als JSON.",
            request_source="co_worker",
            preferred_provider="openvino",
            max_tokens=220,
        )
    )

    assert gateway.calls
    assert gateway.calls[0].provider == orchestrator.co_worker_main_provider
    routing = result.llm_generation["context_debug"]["routing"]
    assert routing["co_worker_provider_locked"] is True
    assert routing["co_worker_preferred_provider_ignored"] is True
    assert routing["routing_class"] == "co_worker"
    assert "breaker_state" in routing


@pytest.mark.asyncio
async def test_orchestrator_helper_offload_classifies_intent_task_type():
    gateway = _FakeInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=gateway,
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _AcceptValidator()
    _enable_compact_context(orchestrator)

    result = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-helper-intent",
            run_id="run-helper-intent",
            user_id="user-1",
            query="Klassifiziere bitte den Intent dieses Textes als Label.",
            request_source="assistant",
            max_tokens=220,
        )
    )

    assert gateway.calls
    assert gateway.calls[0].provider == "openvino_npu_helper"
    assert gateway.calls[0].task_type == "intent_classification"
    assert gateway.calls[0].expected_fields == ["task_id", "intent", "confidence"]
    routing = result.llm_generation["context_debug"]["routing"]
    assert routing["helper_task_type"] == "intent_classification"
    assert routing["helper_offload_reason"] == "short_parallelizable_intent_classification"


@pytest.mark.asyncio
async def test_orchestrator_helper_offload_classifies_rewrite_fragments_task_type():
    gateway = _FakeInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=gateway,
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _AcceptValidator()
    _enable_compact_context(orchestrator)

    result = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-helper-rewrite",
            run_id="run-helper-rewrite",
            user_id="user-1",
            query="Bitte rewrite den folgenden fragment als klaren Satz.",
            request_source="assistant",
            max_tokens=220,
        )
    )

    assert gateway.calls
    assert gateway.calls[0].provider == "openvino_npu_helper"
    assert gateway.calls[0].task_type == "rewrite_fragments"
    assert gateway.calls[0].expected_fields == ["task_id", "rewrite_fragments", "confidence"]
    routing = result.llm_generation["context_debug"]["routing"]
    assert routing["helper_task_type"] == "rewrite_fragments"
    assert routing["helper_offload_reason"] == "short_parallelizable_rewrite_fragments"
