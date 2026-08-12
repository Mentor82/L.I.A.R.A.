"""Unit tests for inference invocation adapter abstraction."""

import pytest

from services.contracts import InferenceRequest, InferenceResult
from services.config import Settings
from services.inference.invocation import (
    DirectInferenceInvoker,
    QueueReadyInferenceInvoker,
    ensure_inference_invoker,
)
from services.inference.queue import RedisStreamsInferenceQueueClient


@pytest.mark.asyncio
class TestInferenceInvocation:
    async def test_direct_invoker_calls_gateway(self):
        class FakeGateway:
            async def infer(self, request):
                return InferenceResult(content=request.prompt, provider="ollama", model="m")

        invoker = DirectInferenceInvoker(FakeGateway())
        result = await invoker.infer(InferenceRequest(prompt="hello"))
        assert result.content == "hello"

    async def test_queue_ready_falls_back_to_direct(self):
        class FakeGateway:
            async def infer(self, request):
                return InferenceResult(content="ok", provider="ollama", model="m")

        invoker = QueueReadyInferenceInvoker(direct_gateway=FakeGateway(), queue_client=None)
        result = await invoker.infer(InferenceRequest(prompt="x"))
        assert result.content == "ok"
        assert result.metadata.get("invocation_mode") == "queue_fallback_direct"

    async def test_queue_ready_without_transport_or_fallback_raises(self):
        invoker = QueueReadyInferenceInvoker(direct_gateway=None, queue_client=None)
        with pytest.raises(RuntimeError):
            await invoker.infer(InferenceRequest(prompt="x"))

    async def test_ensure_inference_invoker_defaults_to_direct(self):
        class FakeGateway:
            async def infer(self, request):
                return InferenceResult(content="ok", provider="ollama", model="m")

        invoker = ensure_inference_invoker(FakeGateway())
        result = await invoker.infer(InferenceRequest(prompt="x"))
        assert result.content == "ok"

    async def test_ensure_inference_invoker_queue_mode_uses_queue_ready(self):
        class FakeGateway:
            async def infer(self, request):
                return InferenceResult(content="ok", provider="ollama", model="m")

        invoker = ensure_inference_invoker(FakeGateway(), mode="queue")
        result = await invoker.infer(InferenceRequest(prompt="x"))
        assert result.metadata.get("invocation_mode") == "queue_fallback_direct"

    async def test_ensure_inference_invoker_queue_mode_builds_redis_client_when_configured(self, monkeypatch):
        class FakeGateway:
            async def infer(self, request):
                return InferenceResult(content="ok", provider="ollama", model="m")

        monkeypatch.setattr(Settings, "REDIS_URL", "redis://:pw@127.0.0.1:6380/0")
        invoker = ensure_inference_invoker(FakeGateway(), mode="queue")

        assert isinstance(invoker, QueueReadyInferenceInvoker)
        assert isinstance(invoker.queue_client, RedisStreamsInferenceQueueClient)

    async def test_queue_request_response_success(self):
        class FakeQueueClient:
            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                return {
                    "content": "queue-ok",
                    "provider": "ollama",
                    "model": "qwen2.5:3b",
                    "status": "success",
                    "stop_reason": "stop",
                    "metadata": {},
                }

        invoker = QueueReadyInferenceInvoker(
            queue_client=FakeQueueClient(),
            direct_gateway=None,
            timeout_seconds=0.2,
            max_retries=0,
        )
        result = await invoker.infer(InferenceRequest(prompt="x", provider="ollama"))
        assert result.content == "queue-ok"
        assert result.metadata.get("invocation_mode") == "queue"
        assert result.metadata.get("queue_attempt") == 1
        assert result.metadata.get("queue_correlation_id")

    async def test_queue_timeout_retries_then_returns_timeout_without_fallback(self):
        class FakeQueueClient:
            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                raise TimeoutError("worker timeout")

        invoker = QueueReadyInferenceInvoker(
            queue_client=FakeQueueClient(),
            direct_gateway=None,
            timeout_seconds=0.05,
            max_retries=1,
            retry_backoff_seconds=0,
            enable_fallback=False,
        )
        result = await invoker.infer(InferenceRequest(prompt="x", provider="hybrid"))
        assert result.status == "timeout"
        assert result.stop_reason == "timeout"
        assert result.metadata.get("invocation_mode") == "queue"
        assert len(result.metadata.get("queue_errors", [])) >= 1

    async def test_queue_failure_falls_back_to_direct(self):
        class FakeQueueClient:
            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                raise RuntimeError("queue unavailable")

        class FakeGateway:
            async def infer(self, request):
                return InferenceResult(content=f"direct:{request.prompt}", provider="ollama", model="m")

        invoker = QueueReadyInferenceInvoker(
            queue_client=FakeQueueClient(),
            direct_gateway=FakeGateway(),
            timeout_seconds=0.1,
            max_retries=0,
        )
        result = await invoker.infer(InferenceRequest(prompt="x", provider="ollama"))
        assert result.content == "direct:x"
        assert result.metadata.get("invocation_mode") == "queue_fallback_direct"
        assert "queue unavailable" in " ".join(result.metadata.get("queue_errors", []))
