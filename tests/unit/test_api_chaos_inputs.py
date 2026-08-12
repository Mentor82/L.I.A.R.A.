from __future__ import annotations

import httpx
import pytest

from services.api import create_api_app
from services.contracts import OrchestratorResponse
from services.memory.store import EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import MemoryLayer
from services.memory_adapter import InProcessMemoryAdapter


@pytest.fixture(autouse=True)
def _default_local_sandbox_mode(monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")


class _FakeOrchestrator:
    async def run(self, request):
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
                "gen_ms": 2.0,
                "context_debug": {"mode": "NONE", "sources": {"postgres": 0}},
            },
            validation_result={
                "passed": True,
                "decision": "accept",
                "checks": {},
                "issues": [],
                "confidence_score": 0.99,
                "retry_count": 0,
            },
            execution_trace=[],
        )


def _build_app():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    return create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=adapter)


@pytest.mark.asyncio
async def test_chat_rejects_missing_message_field() -> None:
    app = _build_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "chaos-1",
                "user_id": "user-1",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_invalid_feedback_score_range() -> None:
    app = _build_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "chaos-2",
                "user_id": "user-2",
                "message": "hello",
                "user_feedback_score": 1.7,
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_non_object_message_type() -> None:
    app = _build_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "chaos-3",
                "user_id": "user-3",
                "message": {"unexpected": "object"},
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_invalid_attachments_shape() -> None:
    app = _build_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "chaos-4",
                "user_id": "user-4",
                "message": "hello",
                "attachments": ["not-an-attachment-object"],
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_stays_operational_after_invalid_payload() -> None:
    app = _build_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        invalid_response = await client.post(
            "/chat",
            json={
                "session_id": "chaos-5",
                "user_id": "user-5",
                "message": "hello",
                "user_feedback_stars": 99,
            },
        )
        valid_response = await client.post(
            "/chat",
            json={
                "session_id": "chaos-5",
                "user_id": "user-5",
                "message": "ping",
            },
        )

    assert invalid_response.status_code == 422
    assert valid_response.status_code == 200
    payload = valid_response.json()
    assert payload["response"].startswith("echo:")
