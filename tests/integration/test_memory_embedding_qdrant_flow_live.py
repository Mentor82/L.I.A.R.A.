"""Optional real live test for Memory -> Embedding -> Qdrant flow."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from services.config import Settings
from services.contracts import MemoryRetrievalQueryRequest, MemoryRetrievalUpsertRequest
from services.memory.tier_store import FactStore, SessionStore
from services.memory import BackedMemoryServiceStore, create_memory_service_app
from services.memory_adapter import RemoteMemoryAdapter


RUN_LIVE_MEMORY_FLOW_TESTS = os.getenv("RUN_LIVE_MEMORY_FLOW_TESTS") == "1"
REDIS_URL = os.getenv("REDIS_URL")
POSTGRES_URL = os.getenv("POSTGRES_URL")
QDRANT_URL = os.getenv("QDRANT_URL")
EMBEDDING_SERVICE_BASE_URL = os.getenv("EMBEDDING_SERVICE_BASE_URL")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_MEMORY_FLOW_TESTS
    or not REDIS_URL
    or not POSTGRES_URL
    or not QDRANT_URL
    or not EMBEDDING_SERVICE_BASE_URL,
    reason=(
        "live memory->embedding->qdrant flow test requires RUN_LIVE_MEMORY_FLOW_TESTS=1 "
        "plus REDIS_URL, POSTGRES_URL, QDRANT_URL, EMBEDDING_SERVICE_BASE_URL"
    ),
)


@pytest.mark.asyncio
class TestLiveMemoryEmbeddingQdrantFlow:
    async def test_memory_to_embedding_to_qdrant_round_trip(self, monkeypatch):
        suffix = uuid.uuid4().hex[:8]
        table_name = f"memory_facts_flow_live_{suffix}"
        document_id = f"doc-flow-{suffix}"
        collection_name = f"liara_retrieval_live_{suffix}"

        monkeypatch.setattr(Settings, "QDRANT_COLLECTION", collection_name)
        monkeypatch.setattr(Settings, "QDRANT_VECTOR_SIZE", 1024)

        store = BackedMemoryServiceStore(
            session_store=SessionStore(redis_url=REDIS_URL),
            fact_store=FactStore(postgres_url=POSTGRES_URL, table_name=table_name),
            embedding_service_base_url=EMBEDDING_SERVICE_BASE_URL,
            embedding_service_timeout_seconds=30.0,
        )
        app = create_memory_service_app(store=store)
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        adapter = RemoteMemoryAdapter("http://testserver", client=client)

        try:
            embedding_health = await client.get("/health/backends")
            embedding_health.raise_for_status()
            backend_health = embedding_health.json().get("backend_health", {})
            assert backend_health.get("embedding") == "healthy"
            assert backend_health.get("qdrant") == "healthy"

            upsert_response = await adapter.upsert_retrieval(
                MemoryRetrievalUpsertRequest(
                    document_id=document_id,
                    content="openvino embedding pipeline validates qdrant retrieval flow",
                    source="live-flow",
                    metadata={"topic": "openvino", "suite": "live-flow"},
                )
            )
            assert upsert_response.status.status == "success"
            assert upsert_response.status.backend == "qdrant"

            query_response = await adapter.query_retrieval(
                MemoryRetrievalQueryRequest(
                    query="openvino qdrant retrieval",
                    top_k=5,
                    filters={"topic": "openvino"},
                )
            )
            assert query_response.status.status == "success"
            assert query_response.status.backend == "qdrant"
            assert any(item.document_id == document_id for item in query_response.items)
        finally:
            await client.aclose()
            await store.close()
