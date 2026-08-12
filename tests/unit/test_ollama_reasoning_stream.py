from __future__ import annotations

import pytest

from services.contracts import InferenceRequest
from services.inference.ollama_reasoning_stream import OllamaReasoningStream
from services.inference.providers.ollama import OllamaProvider


def test_reasoning_stream_tracks_thinking_and_answer_for_chat_chunks():
    stream = OllamaReasoningStream()

    first = stream.process_chunk(
        {
            "message": {
                "thinking": "Step 1: inspect\n- gather facts\n",
                "content": "",
            },
            "done": False,
        }
    )
    second = stream.process_chunk(
        {
            "message": {
                "thinking": "",
                "content": "Final answer.",
            },
            "done": True,
        }
    )
    final = stream.finalize()

    assert first["phase"] == "thinking"
    assert second["phase"] == "answer"
    assert final["thinking"].startswith("Step 1")
    assert final["answer"] == "Final answer."
    assert final["metrics"]["thinking_tokens"] > 0
    assert final["metrics"]["step_hits"] >= 1
    assert final["metrics"]["bullet_hits"] >= 1
    assert final["metrics"]["reasoning_depth_score"] > 0


def test_reasoning_stream_tracks_generate_chunks_from_top_level_fields():
    stream = OllamaReasoningStream()

    stream.process_chunk({"thinking": "1. plan\n2. solve\n", "response": "", "done": False})
    stream.process_chunk({"thinking": "", "response": "42", "done": True})
    final = stream.finalize()

    assert final["thinking"] == "1. plan\n2. solve\n"
    assert final["answer"] == "42"
    assert final["metrics"]["numbered_hits"] >= 2
    assert final["metrics"]["answer_tokens"] >= 1


@pytest.mark.asyncio
async def test_ollama_provider_includes_reasoning_metadata(monkeypatch):
    provider = OllamaProvider(host="127.0.0.1", port=11434, model="qwen-test")

    class FakeStreamResponse:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield '{"thinking":"Step 1: inspect\\n- validate\\n","response":"","done":false}'
            yield '{"thinking":"","response":"Result text","done":true,"total_duration":2000000000,"prompt_eval_duration":500000000,"load_duration":100000000}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *args, **kwargs):
            return FakeStreamResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient())

    result = await provider.infer(InferenceRequest(prompt="hello", provider="ollama"))

    assert result.content == "Result text"
    assert result.gen_ms == pytest.approx(2000.0)
    assert result.ttft_ms == pytest.approx(500.0)
    assert result.load_ms == pytest.approx(100.0)
    assert result.metadata["reasoning"]["thinking"].startswith("Step 1")
    assert result.metadata["reasoning"]["thinking_tokens"] > 0
    assert result.metadata["reasoning"]["reasoning_depth_score"] > 0