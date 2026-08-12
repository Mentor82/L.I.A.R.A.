from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier
from tests.memory_adapter_fakes import NoopGraphMemoryAdapterMixin


class _FakeInferenceGateway:
    async def infer(self, request):
        return SimpleNamespace()


class _FakeMemoryAdapter(NoopGraphMemoryAdapterMixin, MemoryServiceAdapter):
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


class _FakeRewardScorer:
    def __init__(self, score):
        self._score = score

    def score_action(self, action: str, input_text: str, context=None):
        return dict(self._score)


@pytest.mark.asyncio
async def test_reward_routing_blocks_high_risk_query():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    orchestrator.reward_routing_enabled = True
    orchestrator.reward_routing_block_threshold = 0.85
    orchestrator.reward_routing_conf_threshold = 0.70
    orchestrator.reward_scorer = _FakeRewardScorer(
        {
            "model_available": True,
            "eval_binary": 0,
            "risk_score": 0.95,
            "confidence": 0.91,
            "source": "test_reward_model",
        }
    )

    selected = await orchestrator._select_tools("What is the current time?")

    assert selected == []
    assert orchestrator._last_route_debug["reason"] == "reward_model_risk_block"
    reward_meta = orchestrator._last_route_debug["metadata"].get("reward_routing", {})
    assert reward_meta.get("block") is True
    assert reward_meta.get("source") == "test_reward_model"


@pytest.mark.asyncio
async def test_reward_routing_keeps_tools_for_safe_query():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    orchestrator.reward_routing_enabled = True
    orchestrator.reward_scorer = _FakeRewardScorer(
        {
            "model_available": True,
            "eval_binary": 1,
            "risk_score": 0.10,
            "confidence": 0.88,
            "source": "test_reward_model",
        }
    )

    selected = await orchestrator._select_tools("What is the current time?")

    assert selected == ["sys"]
    assert orchestrator._last_route_debug["reason"].startswith("sys_")
    reward_meta = orchestrator._last_route_debug["metadata"].get("reward_routing", {})
    assert reward_meta.get("block") is False
    assert reward_meta.get("eval_binary") == 1
