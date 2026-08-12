"""Integration tests for queue-mode inference invocation.

Covers queue request/response behavior beyond unit-level adapter checks.
"""

import asyncio

import pytest

from services.contracts import InferenceRequest, InferenceResult, OrchestratorRequest, ToolExecutionResult
from services.orchestrator.orchestrator import Orchestrator
from services.inference.invocation import QueueReadyInferenceInvoker
from services.memory_adapter import InProcessMemoryAdapter
from services.shared.types import RunState


class FakeMemoryLayer:
    async def get(self, tier, key, default=None):
        del tier, key
        return default

    async def set(self, tier, key, value, ttl_seconds=None):
        del tier, key, value, ttl_seconds

    async def delete(self, tier, key):
        del tier, key

    async def exists(self, tier, key):
        del tier, key
        return False


class FakeToolCoordinator:
    async def execute_tools_parallel(self, requests):
        return {
            req.tool_name: ToolExecutionResult(
                tool_name=req.tool_name,
                status="success",
                output=f"output:{req.tool_name}",
            )
            for req in requests
        }


class FakeDirectGateway:
    async def infer(self, request):
        return InferenceResult(
            content=f"direct:{request.prompt[:12]}",
            provider="ollama",
            model="qwen2.5:3b",
            status="success",
            ttft_ms=10.0,
            gen_ms=20.0,
            stop_reason="stop",
        )


@pytest.mark.asyncio
class TestInferenceQueueModeIntegration:
    async def test_queue_mode_success_returns_worker_result(self):
        class FakeQueueClient:
            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                return {
                    "content": "queue-success",
                    "provider": "ollama",
                    "model": "qwen2.5:3b",
                    "status": "success",
                    "ttft_ms": 11.0,
                    "gen_ms": 22.0,
                    "stop_reason": "stop",
                    "metadata": {},
                }

        invoker = QueueReadyInferenceInvoker(
            queue_client=FakeQueueClient(),
            direct_gateway=None,
            timeout_seconds=0.05,
            max_retries=0,
        )

        result = await invoker.infer(InferenceRequest(prompt="hello", provider="ollama"))
        assert result.content == "queue-success"
        assert result.metadata.get("invocation_mode") == "queue"
        assert result.metadata.get("queue_attempt") == 1

    async def test_queue_mode_retries_before_success(self):
        class FlakyQueueClient:
            def __init__(self):
                self.calls = 0

            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient queue failure")
                return {
                    "content": "queue-recovered",
                    "provider": "ollama",
                    "model": "qwen2.5:3b",
                    "status": "success",
                    "stop_reason": "stop",
                    "metadata": {},
                }

        queue_client = FlakyQueueClient()
        invoker = QueueReadyInferenceInvoker(
            queue_client=queue_client,
            direct_gateway=None,
            timeout_seconds=0.05,
            max_retries=1,
            retry_backoff_seconds=0,
        )

        result = await invoker.infer(InferenceRequest(prompt="hello", provider="ollama"))
        assert result.content == "queue-recovered"
        assert result.metadata.get("queue_attempt") == 2
        assert queue_client.calls == 2

    async def test_queue_mode_timeout_returns_timeout_envelope_without_fallback(self):
        class SlowQueueClient:
            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                await asyncio.sleep(0.05)
                return {
                    "content": "too-late",
                    "provider": "ollama",
                    "model": "qwen2.5:3b",
                    "status": "success",
                    "stop_reason": "stop",
                    "metadata": {},
                }

        invoker = QueueReadyInferenceInvoker(
            queue_client=SlowQueueClient(),
            direct_gateway=None,
            timeout_seconds=0.01,
            max_retries=1,
            retry_backoff_seconds=0,
            enable_fallback=False,
        )

        result = await invoker.infer(InferenceRequest(prompt="hello", provider="hybrid"))
        assert result.status == "timeout"
        assert result.stop_reason == "timeout"
        assert result.content == ""
        assert result.metadata.get("invocation_mode") == "queue"
        assert len(result.metadata.get("queue_errors", [])) == 2

    async def test_orchestrator_queue_fallback_preserves_response_contract(self):
        class BrokenQueueClient:
            async def request_response(self, payload, correlation_id, timeout_seconds):
                del payload, correlation_id, timeout_seconds
                raise RuntimeError("worker offline")

        invoker = QueueReadyInferenceInvoker(
            queue_client=BrokenQueueClient(),
            direct_gateway=FakeDirectGateway(),
            timeout_seconds=0.02,
            max_retries=0,
        )

        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=invoker,
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        response = await orchestrator.run(
            OrchestratorRequest(
                session_id="s1",
                run_id="r1",
                user_id="u1",
                query="What is Python?",
                max_tokens=64,
            )
        )

        payload = response.model_dump()
        assert response.state_final == RunState.COMPLETE.value
        assert set(payload.keys()) == {
            "run_id",
            "final_response",
            "tools_executed",
            "tool_results",
            "state_final",
            "llm_generation",
            "validation_result",
            "execution_trace",
        }
        assert response.final_response.startswith("direct:")
        # Migration contract is additive: required keys must exist while extra
        # observability fields are allowed.
        assert {
            "content",
            "provider",
            "model",
            "ttft_ms",
            "gen_ms",
        }.issubset(set(response.llm_generation.keys()))
