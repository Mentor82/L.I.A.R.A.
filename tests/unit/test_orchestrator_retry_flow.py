"""Unit tests for orchestrator retry flow triggered by validator decisions."""

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
from services.orchestrator.gap_detector import GapDecision, GapDetector
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier


class _FakeInferenceGateway:
    def __init__(self):
        self.calls = []

    async def infer(self, request):
        self.calls.append(request)
        attempt = len(self.calls)
        return InferenceResult(
            content=f"attempt-{attempt}",
            provider="mock",
            model="mock-model",
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
        # Return one context entry when forced context is active (query != "summary")
        if request.query != "summary":
            return SimpleNamespace(items=[SimpleNamespace(content="forced context")])
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


class _SequencedValidator:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def validate(self, context):
        decision = self.decisions.pop(0)
        return ValidationResult(
            passed=decision == "accept",
            decision=decision,
            checks={"fast_check": "pass"},
            issues=[] if decision == "accept" else ["Factual answer appears ungrounded: no context or tool evidence"],
            confidence_score=0.9 if decision == "accept" else 0.7,
        )


def _force_gap(action: str, *, trigger: str = "test_gap"):
    def _detect(**kwargs):
        return GapDecision(
            gap_detected=True,
            gap_type="MEMORY_GAP",
            missing=["test evidence"],
            confidence=0.8,
            action=action,
            reasoning_step=int(kwargs.get("reasoning_step", 1)),
            trigger=trigger,
        )

    return staticmethod(_detect)


def _enable_retryable_compression(orchestrator: Orchestrator) -> None:
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
            final_context="forced context",
            summary="forced summary",
            facts=[],
            relations=[],
            dropped_items=[],
            token_estimate=16,
            metadata={"source": "test"},
            no_new_information=False,
            meaningful_reduction=True,
        )
    )


@pytest.mark.asyncio
async def test_orchestrator_retries_once_on_revise_and_completes():
    gateway = _FakeInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=gateway,
        memory_layer=_FakeMemoryAdapter(),
    )

    # Avoid unrelated tool path in this focused retry test.
    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _SequencedValidator(["revise", "accept"])
    _enable_retryable_compression(orchestrator)
    original_detect = GapDetector.detect
    GapDetector.detect = _force_gap("LOAD_MEMORY")

    try:
        result = await orchestrator.run(
            OrchestratorRequest(
                session_id="session-retry",
                run_id="run-retry",
                user_id="user-1",
                query="Was ist das Ohmsche Gesetz?",
                max_tokens=256,
            )
        )
    finally:
        GapDetector.detect = original_detect

    assert result.validation_result["decision"] == "accept"
    assert result.validation_result["retry_count"] == 1
    assert result.validation_result["retry_control"]["stop_reason"] == "accepted_no_retry"
    assert result.llm_generation["retry"]["count"] == 1
    assert len(gateway.calls) == 2
    assert any(
        transition.get("metadata", {}).get("retry_attempt") == 1
        for transition in result.execution_trace
    )


@pytest.mark.asyncio
async def test_orchestrator_block_retry_forces_context_mode():
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
    orchestrator.validator = _SequencedValidator(["block", "accept"])
    _enable_retryable_compression(orchestrator)
    original_detect = GapDetector.detect
    GapDetector.detect = _force_gap("LOAD_CONTEXT")

    try:
        result = await orchestrator.run(
            OrchestratorRequest(
                session_id="session-block",
                run_id="run-block",
                user_id="user-1",
                query="Was ist Gauss?",
                max_tokens=256,
            )
        )
    finally:
        GapDetector.detect = original_detect

    assert result.validation_result["retry_count"] == 1
    assert result.validation_result["retry_control"]["stop_reason"] == "accepted_no_retry"
    assert result.llm_generation["context_debug"]["mode"] == "CONTEXT"
    assert result.llm_generation["context_debug"]["force_context"] is True
    assert result.llm_generation["context_debug"]["librarian"]["route"] == "RUN_CONTEXT"
    assert result.llm_generation["context_debug"]["librarian"]["primary_source"] == "chroma"


@pytest.mark.asyncio
async def test_orchestrator_retry_limit_prevents_endless_loop_on_block():
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
    orchestrator.validator = _SequencedValidator(["block", "block", "block"])
    _enable_retryable_compression(orchestrator)
    original_detect = GapDetector.detect
    GapDetector.detect = _force_gap("LOAD_CONTEXT")

    try:
        result = await orchestrator.run(
            OrchestratorRequest(
                session_id="session-limit",
                run_id="run-limit",
                user_id="user-1",
                query="Was ist Gauss?",
                max_tokens=256,
            )
        )
    finally:
        GapDetector.detect = original_detect

    # Current policy may stop before retry_limit when information gain stays low.
    assert len(gateway.calls) == 2
    assert result.validation_result["decision"] == "block"
    assert result.validation_result["retry_count"] == 1
    assert result.validation_result["retry_control"]["stop_reason"] in {
        "low_information_gain",
        "retry_limit_reached",
    }
    assert result.llm_generation["retry"]["count"] == 1
