"""Migration contract tests for orchestrator response compatibility.

Ensures external response shape remains stable across invocation refactors.
"""

import pytest

from services.config import Settings
from services.contracts import (
    InferenceResult,
    MemoryEmbeddingRequest,
    MemoryHealthResponse,
    MemoryRetrievalQueryRequest,
    OrchestratorRequest,
    OrchestratorResponse,
    ToolExecutionResult,
)
from services.orchestrator.orchestrator import Orchestrator
from services.inference.invocation import QueueReadyInferenceInvoker
from services.memory_adapter import InProcessMemoryAdapter, RemoteMemoryAdapter
from services.shared.types import RunState


class FakeMemoryLayer:
    """Minimal memory layer for orchestrator construction in migration tests."""

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


class FakeInferenceGatewayDirect:
    async def infer(self, request):
        return InferenceResult(
            content=f"answer:{request.prompt[:16]}",
            provider="ollama",
            model="qwen2.5:3b",
            status="success",
            ttft_ms=12.0,
            gen_ms=34.0,
            stop_reason="stop",
        )


class FakeInferenceGatewayQueueFallback(FakeInferenceGatewayDirect):
    invocation_mode = "queue"


class BrokenQueueClient:
    async def request_response(self, payload, correlation_id, timeout_seconds):
        del payload, correlation_id, timeout_seconds
        raise RuntimeError("queue worker unreachable")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeMemoryHttpClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, json):
        self.calls.append((url, json))
        if url.endswith("/retrieval/query"):
            return _FakeResponse(
                {
                    "items": [
                        {
                            "document_id": "doc-1",
                            "content": "python async adapters",
                            "score": 0.91,
                            "source": "kb",
                            "chunk_index": 0,
                            "metadata": {"topic": "python"},
                        }
                    ],
                    "status": {
                        "status": "success",
                        "backend": "memory-service",
                        "degraded": False,
                        "error": None,
                        "metadata": {},
                    },
                }
            )
        if url.endswith("/embedding/generate"):
            return _FakeResponse(
                {
                    "item": {
                        "model": "embed-small",
                        "dimensions": 3,
                        "vector": [0.12, 0.34, 0.56],
                        "metadata": {"source": "test"},
                    },
                    "status": {
                        "status": "success",
                        "backend": "embedding",
                        "degraded": False,
                        "error": None,
                        "metadata": {},
                    },
                }
            )
        return _FakeResponse({"items": [], "status": {"status": "success", "backend": "memory-service", "degraded": False, "error": None, "metadata": {}}})


@pytest.mark.asyncio
class TestOrchestratorMigrationContract:
    async def test_orchestrator_response_schema_stable_direct_mode(self):
        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayDirect(),
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        request = OrchestratorRequest(
            session_id="s1",
            run_id="r1",
            user_id="u1",
            query="What is Python?",
            max_tokens=64,
        )

        response = await orchestrator.run(request)
        assert isinstance(response, OrchestratorResponse)

        payload = response.model_dump()
        expected_keys = set(OrchestratorResponse.model_fields.keys())
        assert set(payload.keys()) == expected_keys
        assert "content" in payload["llm_generation"]
        assert "provider" in payload["llm_generation"]
        assert response.state_final == RunState.COMPLETE.value

    async def test_orchestrator_response_schema_stable_queue_fallback_mode(self):
        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayQueueFallback(),
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        request = OrchestratorRequest(
            session_id="s1",
            run_id="r2",
            user_id="u1",
            query="What is Python?",
            max_tokens=64,
        )

        response = await orchestrator.run(request)
        assert isinstance(response, OrchestratorResponse)

        payload = response.model_dump()
        assert set(payload.keys()) == set(OrchestratorResponse.model_fields.keys())
        assert "content" in payload["llm_generation"]
        assert "provider" in payload["llm_generation"]
        assert response.state_final == RunState.COMPLETE.value

    async def test_direct_and_queue_modes_keep_same_contract_fields(self):
        direct = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayDirect(),
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )
        queue_mode = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayQueueFallback(),
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        req = OrchestratorRequest(
            session_id="s1",
            run_id="r3",
            user_id="u1",
            query="Tell me the current time",
            max_tokens=64,
        )

        direct_resp = await direct.run(req)
        queue_resp = await queue_mode.run(req)

        assert set(direct_resp.model_dump().keys()) == set(queue_resp.model_dump().keys())
        assert set(direct_resp.llm_generation.keys()) == set(queue_resp.llm_generation.keys())
        assert set(direct_resp.validation_result.keys()) == set(queue_resp.validation_result.keys())

    async def test_orchestrator_service_mode_keeps_response_schema(self, monkeypatch):
        monkeypatch.setattr(Settings, "MEMORY_MODE", "service")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_BASE_URL", "http://memory.local")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_TIMEOUT_SECONDS", 2.0)

        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayDirect(),
            memory_layer=FakeMemoryLayer(),
        )

        assert isinstance(orchestrator.memory_service, RemoteMemoryAdapter)

        response = await orchestrator.run(
            OrchestratorRequest(
                session_id="s1",
                run_id="r4",
                user_id="u1",
                query="What is Python?",
                max_tokens=64,
            )
        )

        payload = response.model_dump()
        assert response.state_final == RunState.COMPLETE.value
        assert set(payload.keys()) == set(OrchestratorResponse.model_fields.keys())
        assert "content" in payload["llm_generation"]
        assert "provider" in payload["llm_generation"]

    async def test_queue_error_without_fallback_keeps_schema(self):
        invoker = QueueReadyInferenceInvoker(
            queue_client=BrokenQueueClient(),
            direct_gateway=None,
            timeout_seconds=0.01,
            max_retries=0,
            enable_fallback=False,
        )

        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=invoker,
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        response = await orchestrator.run(
            OrchestratorRequest(
                session_id="s1",
                run_id="r5",
                user_id="u1",
                query="What is Python?",
                max_tokens=64,
            )
        )

        payload = response.model_dump()
        assert response.state_final == RunState.COMPLETE.value
        assert set(payload.keys()) == set(OrchestratorResponse.model_fields.keys())
        assert "content" in payload["llm_generation"]
        assert "provider" in payload["llm_generation"]
        assert response.llm_generation["content"] == ""

    async def test_llm_trace_metadata_has_consistent_mode_context_direct(self):
        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayDirect(),
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        response = await orchestrator.run(
            OrchestratorRequest(
                session_id="s1",
                run_id="r6",
                user_id="u1",
                query="What is Python?",
                max_tokens=64,
            )
        )

        llm_transition = next(item for item in reversed(response.execution_trace) if (item.get("to") == "llm_generation" or item.get("to_state") == "llm_generation") and item.get("metadata"))
        metadata = llm_transition.get("metadata") or {}
        assert metadata.get("invocation_mode") == "direct"
        assert metadata.get("memory_mode") == "in_process"
        assert metadata.get("memory_adapter") == "InProcessMemoryAdapter"
        assert metadata.get("queue_error_count") == 0

    async def test_llm_trace_metadata_has_service_mode_context(self, monkeypatch):
        monkeypatch.setattr(Settings, "MEMORY_MODE", "service")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_BASE_URL", "http://memory.local")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_TIMEOUT_SECONDS", 2.0)

        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayDirect(),
            memory_layer=FakeMemoryLayer(),
        )

        response = await orchestrator.run(
            OrchestratorRequest(
                session_id="s1",
                run_id="r7",
                user_id="u1",
                query="What is Python?",
                max_tokens=64,
            )
        )

        llm_transition = next(item for item in reversed(response.execution_trace) if (item.get("to") == "llm_generation" or item.get("to_state") == "llm_generation") and item.get("metadata"))
        metadata = llm_transition.get("metadata") or {}
        assert metadata.get("invocation_mode") == "direct"
        assert metadata.get("memory_mode") == "service"
        assert metadata.get("memory_adapter") == "RemoteMemoryAdapter"

    async def test_llm_trace_metadata_includes_queue_error_context(self):
        invoker = QueueReadyInferenceInvoker(
            queue_client=BrokenQueueClient(),
            direct_gateway=None,
            timeout_seconds=0.01,
            max_retries=0,
            enable_fallback=False,
        )

        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=invoker,
            memory_layer=InProcessMemoryAdapter(FakeMemoryLayer()),
        )

        response = await orchestrator.run(
            OrchestratorRequest(
                session_id="s1",
                run_id="r8",
                user_id="u1",
                query="What is Python?",
                max_tokens=64,
            )
        )

        llm_transition = next(item for item in reversed(response.execution_trace) if (item.get("to") == "llm_generation" or item.get("to_state") == "llm_generation") and item.get("metadata"))
        metadata = llm_transition.get("metadata") or {}
        assert metadata.get("invocation_mode") == "queue"
        assert metadata.get("inference_status") == "failed"
        assert metadata.get("queue_error_count") == 1
        assert "queue worker unreachable" in (metadata.get("inference_error") or "")

    async def test_interface_checkpoint_a_orchestrator_uses_service_boundary_only(self, monkeypatch):
        monkeypatch.setattr(Settings, "MEMORY_MODE", "service")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_BASE_URL", "http://memory.local")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_TIMEOUT_SECONDS", 2.0)

        legacy_memory = FakeMemoryLayer()
        orchestrator = Orchestrator(
            tool_coordinator=FakeToolCoordinator(),
            inference_gateway=FakeInferenceGatewayDirect(),
            memory_layer=legacy_memory,
        )

        assert isinstance(orchestrator.memory_service, RemoteMemoryAdapter)
        assert orchestrator.memory_service is not legacy_memory
        assert not hasattr(orchestrator.memory_service, "session_store")
        assert not hasattr(orchestrator.memory_service, "fact_store")

    async def test_interface_checkpoint_b_consumes_team2_retrieval_embedding_and_health_contracts(self):
        adapter = RemoteMemoryAdapter("http://memory.local", client=_FakeMemoryHttpClient())

        retrieval_response = await adapter.query_retrieval(
            MemoryRetrievalQueryRequest(
                query="python adapters",
                top_k=3,
                filters={"topic": "python"},
            )
        )
        embedding_response = await adapter.generate_embedding(
            MemoryEmbeddingRequest(input_text="python adapters", model="embed-small")
        )

        health_response = MemoryHealthResponse.model_validate(
            {
                "status": {
                    "status": "partial",
                    "backend": "memory-service",
                    "degraded": True,
                    "error": "qdrant_unavailable",
                    "metadata": {"mode": "service"},
                },
                "backend_health": {
                    "postgres": "healthy",
                    "redis": "healthy",
                    "qdrant": "unavailable",
                },
            }
        )

        assert retrieval_response.status.status == "success"
        assert retrieval_response.items[0].document_id == "doc-1"
        assert retrieval_response.items[0].metadata.get("topic") == "python"
        assert embedding_response.status.status == "success"
        assert embedding_response.item is not None
        assert embedding_response.item.dimensions == 3
        assert health_response.status.status == "partial"
        assert health_response.backend_health["qdrant"] == "unavailable"
