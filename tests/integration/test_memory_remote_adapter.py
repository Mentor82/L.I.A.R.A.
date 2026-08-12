"""
Integration tests for RemoteMemoryAdapter against the stub liara-memory service.
"""

import httpx
import pytest

from services.contracts import (
    MemoryEmbeddingRequest,
    MemoryFactQueryRequest,
    MemoryFactUpsertRequest,
    MemoryHistoryAppendRequest,
    MemoryHistoryQueryRequest,
    MemoryRetrievalQueryRequest,
    MemoryRetrievalUpsertRequest,
)
from services.memory_adapter import RemoteMemoryAdapter
from services.memory import create_memory_service_app


@pytest.mark.asyncio
class TestRemoteMemoryAdapterIntegration:
    """Exercise the HTTP adapter against a real ASGI app."""

    async def test_history_round_trip_against_stub_service(self):
        app = create_memory_service_app()
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        append_response = await adapter.append_history(
            MemoryHistoryAppendRequest(
                session_id="session-remote",
                run_id="run-remote",
                user_id="user-remote",
                role="user",
                content="hello from remote adapter",
            )
        )
        query_response = await adapter.query_history(
            MemoryHistoryQueryRequest(session_id="session-remote", limit=10)
        )

        assert append_response.status.backend == "memory-service"
        assert len(query_response.items) == 1
        assert query_response.items[0].content == "hello from remote adapter"

        await client.aclose()

    async def test_fact_round_trip_against_stub_service(self):
        app = create_memory_service_app()
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        upsert_response = await adapter.upsert_fact(
            MemoryFactUpsertRequest(
                namespace="prefs",
                key="model",
                value="qwen-small",
                tags=["default"],
            )
        )
        query_response = await adapter.query_facts(
            MemoryFactQueryRequest(namespace="prefs", key="model")
        )

        assert upsert_response.status.backend == "memory-service"
        assert len(query_response.items) == 1
        assert query_response.items[0].value == "qwen-small"

        await client.aclose()

    async def test_health_endpoint_reports_fallback_mode_for_stub_service(self):
        app = create_memory_service_app()
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

        response = await client.get("/health")
        payload = response.json()

        assert response.status_code == 200
        assert payload["status"]["status"] == "partial"
        assert payload["status"]["error"] == "fallback_in_memory_store"
        assert payload["backend_health"]["postgres"] == "unavailable"
        assert payload["backend_health"]["redis"] == "unavailable"

        await client.aclose()

    async def test_health_backends_endpoint_reports_backend_map(self):
        app = create_memory_service_app()
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

        response = await client.get("/health/backends")
        payload = response.json()

        assert response.status_code == 200
        assert set(payload["backend_health"].keys()) == {"redis", "postgres", "qdrant", "neo4j", "chroma", "embedding"}

        await client.aclose()

    async def test_retrieval_round_trip_against_stub_service(self):
        app = create_memory_service_app()
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        upsert_response = await adapter.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-python",
                content="python async retrieval service",
                source="docs",
                metadata={"topic": "python"},
            )
        )
        query_response = await adapter.query_retrieval(
            MemoryRetrievalQueryRequest(query="python retrieval", top_k=5)
        )

        assert upsert_response.status.status == "partial"
        assert len(query_response.items) == 1
        assert query_response.items[0].document_id == "doc-python"
        assert query_response.items[0].score > 0

        await client.aclose()

    async def test_embedding_generate_against_stub_service(self):
        app = create_memory_service_app()
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        response = await adapter.generate_embedding(
            MemoryEmbeddingRequest(
                input_text="python async embedding service",
                model="embed-stub",
                metadata={"topic": "python"},
            )
        )

        assert response.status.status == "partial"
        assert response.item is not None
        assert response.item.model == "embed-stub"
        assert response.item.dimensions == len(response.item.vector)

        await client.aclose()
