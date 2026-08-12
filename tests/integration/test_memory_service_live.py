"""Optional live service-mode tests for liara-memory over real Redis/Postgres."""

import os
import uuid

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
from services.memory.tier_store import FactStore, SessionStore
from services.memory import BackedMemoryServiceStore, create_memory_service_app
from services.memory_adapter import RemoteMemoryAdapter


RUN_LIVE_MEMORY_TESTS = os.getenv("RUN_LIVE_MEMORY_TESTS") == "1"
REDIS_URL = os.getenv("REDIS_URL")
POSTGRES_URL = os.getenv("POSTGRES_URL")
QDRANT_URL = os.getenv("QDRANT_URL")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_MEMORY_TESTS or not REDIS_URL or not POSTGRES_URL,
    reason="live memory service tests require RUN_LIVE_MEMORY_TESTS=1 plus REDIS_URL and POSTGRES_URL",
)


@pytest.mark.asyncio
class TestLiveMemoryService:
    async def test_remote_memory_adapter_round_trip_against_real_backed_service(self):
        suffix = uuid.uuid4().hex[:8]
        store = BackedMemoryServiceStore(
            session_store=SessionStore(redis_url=REDIS_URL),
            fact_store=FactStore(
                postgres_url=POSTGRES_URL,
                table_name=f"memory_facts_service_live_{suffix}",
            ),
        )
        app = create_memory_service_app(store=store)
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        try:
            append_response = await adapter.append_history(
                MemoryHistoryAppendRequest(
                    session_id=f"session-{suffix}",
                    run_id=f"run-{suffix}",
                    user_id="live-user",
                    role="user",
                    content="hello from live memory service",
                )
            )
            query_history = await adapter.query_history(
                MemoryHistoryQueryRequest(session_id=f"session-{suffix}", limit=10)
            )
            upsert_response = await adapter.upsert_fact(
                MemoryFactUpsertRequest(
                    namespace=f"prefs-{suffix}",
                    key="model",
                    value="qwen-small",
                    tags=["live"],
                )
            )
            query_facts = await adapter.query_facts(
                MemoryFactQueryRequest(namespace=f"prefs-{suffix}", key="model")
            )

            assert append_response.status.status == "success"
            assert query_history.items[0].content == "hello from live memory service"
            assert upsert_response.status.status == "success"
            assert query_facts.items[0].value == "qwen-small"
        finally:
            await client.aclose()
            await store.close()

    async def test_health_endpoint_against_real_backed_service(self):
        suffix = uuid.uuid4().hex[:8]
        store = BackedMemoryServiceStore(
            session_store=SessionStore(redis_url=REDIS_URL),
            fact_store=FactStore(
                postgres_url=POSTGRES_URL,
                table_name=f"memory_facts_service_health_{suffix}",
            ),
        )
        app = create_memory_service_app(store=store)
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

        try:
            response = await client.get("/health")
            payload = response.json()

            assert response.status_code == 200
            assert payload["status"]["status"] == "success"
            assert payload["backend_health"]["postgres"] == "healthy"
            assert payload["backend_health"]["redis"] == "healthy"
            if QDRANT_URL:
                assert payload["backend_health"]["qdrant"] == "healthy"
        finally:
            await client.aclose()
            await store.close()

    async def test_retrieval_round_trip_against_real_backed_service(self):
        if not QDRANT_URL:
            pytest.skip("live retrieval cutover test requires QDRANT_URL")
        suffix = uuid.uuid4().hex[:8]
        store = BackedMemoryServiceStore(
            session_store=SessionStore(redis_url=REDIS_URL),
            fact_store=FactStore(
                postgres_url=POSTGRES_URL,
                table_name=f"memory_facts_service_retrieval_{suffix}",
            ),
        )
        app = create_memory_service_app(store=store)
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        try:
            upsert_response = await adapter.upsert_retrieval(
                MemoryRetrievalUpsertRequest(
                    document_id=f"doc-{suffix}",
                    content="python retrieval service live validation",
                    source="live-docs",
                    metadata={"topic": "python"},
                )
            )
            query_response = await adapter.query_retrieval(
                MemoryRetrievalQueryRequest(query="python retrieval", top_k=5)
            )

            assert upsert_response.status.status == "success"
            assert upsert_response.status.backend == "qdrant"
            assert any(item.document_id == f"doc-{suffix}" for item in query_response.items)
        finally:
            await client.aclose()
            await store.close()

    async def test_embedding_generate_against_real_backed_service(self):
        suffix = uuid.uuid4().hex[:8]
        store = BackedMemoryServiceStore(
            session_store=SessionStore(redis_url=REDIS_URL),
            fact_store=FactStore(
                postgres_url=POSTGRES_URL,
                table_name=f"memory_facts_service_embedding_{suffix}",
            ),
        )
        app = create_memory_service_app(store=store)
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        try:
            response = await adapter.generate_embedding(
                MemoryEmbeddingRequest(
                    input_text="python live embedding validation",
                    model="embed-live",
                    metadata={"topic": "python"},
                )
            )

            assert response.status.status == "partial"
            assert response.item is not None
            assert response.item.model == "embed-live"
            assert response.item.dimensions == len(response.item.vector)
        finally:
            await client.aclose()
            await store.close()
