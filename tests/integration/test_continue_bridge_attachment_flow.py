import httpx
import pytest

import scripts.continue_openai_bridge as bridge
from services.api import create_api_app
from services.contracts import OrchestratorResponse
from services.memory.store import EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import MemoryLayer
from services.memory_adapter import InProcessMemoryAdapter


class CapturingFakeOrchestrator:
    def __init__(self):
        self.last_request = None

    async def run(self, request):
        self.last_request = request
        return OrchestratorResponse(
            run_id=request.run_id,
            final_response=f"echo: {request.query}",
            tools_executed=[],
            tool_results={},
            state_final="complete",
            llm_generation={
                "provider": "mock",
                "model": "mock-model",
                "ttft_ms": 1.0,
                "gen_ms": 1.0,
                "context_debug": {"mode": "MEMORY", "sources": {"chroma": 0, "qdrant": 0, "postgres": 0}},
            },
            validation_result={
                "passed": True,
                "decision": "accept",
                "checks": {},
                "issues": [],
                "confidence_score": 1.0,
                "suggestions": None,
                "retry_count": 0,
            },
            execution_trace=[],
        )


@pytest.fixture(autouse=True)
def _default_local_sandbox_mode(monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")


@pytest.mark.asyncio
async def test_bridge_responses_endpoint_calls_real_api_with_attachment(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    orchestrator = CapturingFakeOrchestrator()
    api_app = create_api_app(orchestrator=orchestrator, memory_adapter=adapter)

    original_async_client = bridge.httpx.AsyncClient

    def _bridge_async_client(*args, **kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.ASGITransport(app=api_app)
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(bridge.httpx, "AsyncClient", _bridge_async_client)

    transport = httpx.ASGITransport(app=bridge.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/responses",
            json={
                "model": "liara-agent",
                "user": "continue-integration-user",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Nutze die Datei fuer den Plan."},
                            {
                                "type": "input_file",
                                "filename": "plan.txt",
                                "media_type": "text/plain",
                                "text": "Schritt 1\nSchritt 2",
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_text"].startswith("echo: ")
    assert orchestrator.last_request is not None
    assert orchestrator.last_request.user_id == "continue-integration-user"
    assert orchestrator.last_request.attachments[0].name == "plan.txt"
    assert orchestrator.last_request.attachments[0].text_content == "Schritt 1\nSchritt 2"
    assert "Bereitgestellte Dateien/Anhänge" in orchestrator.last_request.query