from __future__ import annotations

import types

import pytest

from services.contracts import InferenceRequest
from services.inference.providers.openvino import OpenVINOProvider


@pytest.mark.asyncio
async def test_openvino_provider_reuses_pipeline(monkeypatch):
    calls = {"init": 0, "generate": 0}

    class FakePipeline:
        def __init__(self, model_dir: str, device: str):
            calls["init"] += 1
            self.model_dir = model_dir
            self.device = device

        def generate(self, prompt: str, max_new_tokens: int):
            calls["generate"] += 1
            return f"{prompt}:{max_new_tokens}:{self.device}"

    fake_module = types.SimpleNamespace(LLMPipeline=FakePipeline)
    monkeypatch.setitem(__import__("sys").modules, "openvino_genai", fake_module)

    provider = OpenVINOProvider(model_dir="/fake/model", device="CPU")

    first = await provider.infer(InferenceRequest(prompt="one", provider="openvino", max_tokens=32))
    second = await provider.infer(InferenceRequest(prompt="two", provider="openvino", max_tokens=16))

    assert first.content == "one:32:CPU"
    assert second.content == "two:16:CPU"
    assert first.load_ms is not None and first.load_ms >= 0
    assert second.load_ms == 0
    assert calls == {"init": 1, "generate": 2}


@pytest.mark.asyncio
async def test_openvino_provider_missing_model_dir():
    provider = OpenVINOProvider(model_dir="", device="CPU")

    result = await provider.infer(InferenceRequest(prompt="x", provider="openvino"))

    assert result.provider == "openvino"
    assert result.status == "failed"
    assert result.error == "OPENVINO model directory not configured"


@pytest.mark.asyncio
async def test_openvino_provider_uses_vlm_pipeline_for_multimodal_bundle(monkeypatch, tmp_path):
    (tmp_path / "openvino_language_model.xml").write_text("language", encoding="utf-8")
    (tmp_path / "openvino_vision_embeddings_model.xml").write_text("vision", encoding="utf-8")
    calls = {"llm": 0, "vlm": 0}

    class FakeLlmPipeline:
        def __init__(self, model_dir: str, device: str):
            calls["llm"] += 1

    class FakeVlmResult:
        texts = ['{"requires_external_information":true}']

    class FakeVlmPipeline:
        def __init__(self, model_dir: str, device: str):
            calls["vlm"] += 1

        def generate(self, prompt: str, max_new_tokens: int):
            return FakeVlmResult()

    fake_module = types.SimpleNamespace(LLMPipeline=FakeLlmPipeline, VLMPipeline=FakeVlmPipeline)
    monkeypatch.setitem(__import__("sys").modules, "openvino_genai", fake_module)

    provider = OpenVINOProvider(model_dir=str(tmp_path), device="NPU")
    result = await provider.infer(InferenceRequest(prompt="classify", provider="openvino", max_tokens=32))

    assert result.content == '{"requires_external_information":true}'
    assert result.metadata["pipeline_kind"] == "vlm"
    assert calls == {"llm": 0, "vlm": 1}
