from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.contracts import InferenceResult
from services.orchestrator import generation_pipeline


@pytest.mark.parametrize("model,budget", [("qwen2.5:3b", 48), (None, None)])
async def test_chat_model_and_budget_reach_inference(monkeypatch, model, budget):
    infer = AsyncMock(return_value=InferenceResult(
        content="LIARA bereit", provider="ollama", model=model or "configured-model",
    ))
    orchestrator = SimpleNamespace(inference=SimpleNamespace(infer=infer))
    monkeypatch.setattr(generation_pipeline, "build_prompt", lambda **kwargs: kwargs["query"])
    monkeypatch.setattr(
        generation_pipeline, "select_inference_provider_for_step",
        lambda *args, **kwargs: ("ollama", {}),
    )

    text, metadata = await generation_pipeline.generate_llm_response(
        orchestrator, query="Antworte nur mit: LIARA bereit",
        preferred_provider="ollama", preferred_model=model, max_tokens=budget,
    )

    request = infer.await_args.args[0]
    assert request.model == model
    assert request.max_tokens == (budget if budget is not None else 2048)
    assert text == "LIARA bereit"
    assert metadata["model"] == (model or "configured-model")
    assert metadata["inference_status"] == "success"
    assert not metadata.get("fallback_applied")


async def test_failed_helper_preserves_main_model_and_budget(monkeypatch):
    infer = AsyncMock(side_effect=[
        InferenceResult(content="", provider="openvino_npu_helper", model="helper", status="failed"),
        InferenceResult(content="OK", provider="ollama", model="qwen2.5:3b"),
    ])
    orchestrator = SimpleNamespace(
        inference=SimpleNamespace(infer=infer), default_inference_provider="ollama",
    )
    monkeypatch.setattr(generation_pipeline, "build_prompt", lambda **kwargs: kwargs["query"])
    monkeypatch.setattr(
        generation_pipeline, "select_inference_provider_for_step",
        lambda *args, **kwargs: ("openvino_npu_helper", {"helper_offload_used": True}),
    )

    text, metadata = await generation_pipeline.generate_llm_response(
        orchestrator, query="Test", preferred_model="qwen2.5:3b", max_tokens=48,
    )

    helper, main = [call.args[0] for call in infer.await_args_list]
    assert helper.model is None
    assert main.model == "qwen2.5:3b"
    assert helper.max_tokens == main.max_tokens == 48
    assert text == "OK"
    assert metadata["model"] == "qwen2.5:3b"
