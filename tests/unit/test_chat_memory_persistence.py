from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.api.app import create_default_memory_adapter
from services.config import Settings
from services.contracts import InferenceResult
from services.memory_adapter import RemoteMemoryAdapter
from services.orchestrator import generation_pipeline
from services.orchestrator.defs.context_channels import merge_context_channels


def test_api_honors_configured_memory_service(monkeypatch):
    monkeypatch.setattr(Settings, "MEMORY_MODE", "service")
    monkeypatch.setattr(Settings, "MEMORY_SERVICE_BASE_URL", "http://memory.test:8020")
    adapter = create_default_memory_adapter()
    assert isinstance(adapter, RemoteMemoryAdapter)
    assert adapter.base_url == "http://memory.test:8020"


async def test_retrieved_history_reaches_actual_inference_prompt(monkeypatch):
    infer = AsyncMock(return_value=InferenceResult(content="marker", provider="ollama", model="test"))
    orchestrator = SimpleNamespace(inference=SimpleNamespace(infer=infer))
    monkeypatch.setattr(generation_pipeline, "build_prompt", lambda **kw: "system prompt")
    monkeypatch.setattr(generation_pipeline, "select_inference_provider_for_step", lambda *a, **kw: ("ollama", {}))
    context = merge_context_channels({"history": [SimpleNamespace(role="user", content="My code is marker-123")]})
    await generation_pipeline.generate_llm_response(
        orchestrator, query="What was my code?", previous_compressed_context=context,
    )
    prompt = infer.await_args.args[0].prompt
    assert "user: My code is marker-123" in prompt
    assert "untrusted historical data" in prompt
    assert prompt.endswith("What was my code?")
