"""Unit tests: chat flow with embedding.

Verifies that the embedding pipeline is triggered correctly during
upsert and query operations within BackedMemoryServiceStore.

Flow under test:
  chat → upsert_retrieval (no pre-computed vector)
       → generate_embedding called automatically
       → vector stored in Qdrant
  chat → query_retrieval
       → generate_embedding called to embed the query
       → returns ranked results from Qdrant
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from services.contracts import (
    MemoryEmbeddingResponse,
    MemoryRetrievalQueryRequest,
    MemoryRetrievalUpsertRequest,
)
from services.memory import BackedMemoryServiceStore
from services.memory.tier_store import FactStore, RetrievalIndex, SessionStore


# ---------------------------------------------------------------------------
# Minimal fakes (self-contained, no imports from other test modules)
# ---------------------------------------------------------------------------

FAKE_VECTOR = [0.1, 0.2, 0.3, 0.4]
FAKE_VECTOR_SIZE = len(FAKE_VECTOR)


class FakeEmbeddingResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "item": {
                "model": "test-embed-model",
                "dimensions": FAKE_VECTOR_SIZE,
                "vector": FAKE_VECTOR,
                "metadata": {"backend": "fake"},
            },
            "status": {
                "status": "success",
                "backend": "embedding",
                "degraded": False,
                "error": None,
                "metadata": {},
            },
        }


class TrackingEmbeddingClient:
    """Records every call to generate_embedding."""

    def __init__(self):
        self.calls: list[dict] = []

    async def post(self, url: str, json: dict):  # noqa: A002
        self.calls.append({"url": url, "payload": json})
        return FakeEmbeddingResponse()


class FakeRedisClient:
    def __init__(self):
        self.storage: dict = {}

    async def get(self, key):
        return self.storage.get(key)

    async def set(self, key, value, ex=None):
        self.storage[key] = value

    async def delete(self, key):
        self.storage.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.storage else 0

    async def ping(self):
        return True

    async def close(self):
        pass


class FakeCursor:
    def __init__(self, storage):
        self.storage = storage
        self.fetchone_result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=None):
        q = " ".join(query.split()).lower()
        if q.startswith("create table if not exists"):
            return
        if q.startswith("select value from"):
            key = params[0]
            self.fetchone_result = (self.storage[key],) if key in self.storage else None
        elif q.startswith("insert into"):
            key, value = params
            self.storage[key] = getattr(value, "adapted", value)
        elif q.startswith("delete from"):
            self.storage.pop(params[0], None)
        elif q.startswith("select 1 from"):
            self.fetchone_result = (1,) if params[0] in self.storage else None

    def fetchone(self):
        return self.fetchone_result


class FakeConnection:
    def __init__(self, storage):
        self.storage = storage

    def cursor(self):
        return FakeCursor(self.storage)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakePool:
    def __init__(self):
        self.storage: dict = {}

    def getconn(self):
        return FakeConnection(self.storage)

    def putconn(self, _):
        pass

    def closeall(self):
        pass


class FakeQdrantPoint:
    def __init__(self, payload, score=0.0):
        self.payload = payload
        self.score = score


class FakeQdrantClient:
    def __init__(self):
        self.collections: set = set()
        self.points: dict = {}

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.collections]
        )

    def create_collection(self, collection_name, vectors_config):
        del vectors_config
        self.collections.add(collection_name)

    def upsert(self, collection_name, points, wait=True):
        del wait
        self.collections.add(collection_name)
        coll = self.points.setdefault(collection_name, {})
        for point in points:
            coll[point.id] = {"vector": list(point.vector), "payload": point.payload}

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        del with_vectors
        coll = self.points.get(collection_name, {})
        result = []
        for pid in ids:
            if pid in coll:
                payload = coll[pid]["payload"] if with_payload else {}
                result.append(SimpleNamespace(payload=payload))
        return result

    def search(self, collection_name, query_vector, limit, with_payload=True, with_vectors=False):
        del with_vectors
        coll = self.points.get(collection_name, {})
        hits = []
        for item in coll.values():
            score = sum(left * right for left, right in zip(query_vector, item["vector"]))
            payload = item["payload"] if with_payload else {}
            hits.append(FakeQdrantPoint(payload=payload, score=score))
        hits.sort(key=lambda p: p.score, reverse=True)
        return hits[:limit]

    def query_points(self, collection_name, query, limit, with_payload=True, with_vectors=False):
        hits = self.search(collection_name, query, limit, with_payload=with_payload)
        return SimpleNamespace(points=hits)

    def get_collection(self, collection_name):
        if collection_name not in self.collections:
            raise RuntimeError("collection not found")
        return {"name": collection_name}

    def close(self):
        pass


def _install_fake_qdrant(monkeypatch, client: FakeQdrantClient) -> None:
    class FakePointStruct:
        def __init__(self, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    class FakePointIdsList:
        def __init__(self, points):
            self.points = points

    class FakeVectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    fake_models = SimpleNamespace(
        PointStruct=FakePointStruct,
        PointIdsList=FakePointIdsList,
        VectorParams=FakeVectorParams,
        Distance=SimpleNamespace(COSINE="cosine"),
    )
    monkeypatch.setitem(
        sys.modules,
        "qdrant_client",
        SimpleNamespace(QdrantClient=lambda url: client, models=fake_models),
    )


def _make_store(monkeypatch) -> tuple[BackedMemoryServiceStore, TrackingEmbeddingClient, FakeQdrantClient]:
    """Build a BackedMemoryServiceStore with fake backends and a tracking embedding client."""
    qdrant_client = FakeQdrantClient()
    _install_fake_qdrant(monkeypatch, qdrant_client)

    retrieval_index = RetrievalIndex(
        qdrant_url="http://qdrant.local:6333",
        collection_name="chat_embed_test",
        client=qdrant_client,
    )
    fact_store = FactStore(
        postgres_url="postgresql://test",
        pool_factory=lambda minconn, maxconn, dsn: FakePool(),
    )
    embedding_client = TrackingEmbeddingClient()
    store = BackedMemoryServiceStore(
        session_store=SessionStore(client=FakeRedisClient()),
        fact_store=fact_store,
        retrieval_index=retrieval_index,
        embedding_service_base_url="http://embedding.local",
        embedding_client=embedding_client,
    )
    return store, embedding_client, qdrant_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestEmbeddingChatFlow:
    """Verify that the embedding service is called at the right points in the chat flow."""

    async def test_upsert_without_embedding_triggers_generate_embedding(self, monkeypatch):
        """When a document is upserted with no pre-computed vector, generate_embedding must be called."""
        store, embedding_client, _ = _make_store(monkeypatch)

        response = await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-embed-1",
                content="Ich heisse Mira und arbeite in Ulm.",
                source="chat-turn-1",
                metadata={"topic": "identity"},
            )
        )

        # Upsert must succeed via Qdrant (not in-memory fallback)
        assert response.status.status == "success"
        assert response.status.backend == "qdrant"

        # Two-level ingestion calls embedding for chunk + doc summary.
        assert len(embedding_client.calls) == 2
        call_sources = {
            call["payload"].get("metadata", {}).get("source")
            for call in embedding_client.calls
        }
        assert "retrieval_upsert" in call_sources
        assert "retrieval_upsert_summary" in call_sources
        assert all(call["url"].endswith("/embedding/generate") for call in embedding_client.calls)

        await store.close()

    async def test_upsert_with_precomputed_embedding_skips_generate_embedding(self, monkeypatch):
        """When a pre-computed vector is provided, generate_embedding must NOT be called."""
        store, embedding_client, _ = _make_store(monkeypatch)

        response = await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-precomputed",
                content="Budget 120000 Euro fuer Solarpanels.",
                source="chat-turn-2",
                embedding=FAKE_VECTOR,
                metadata={"topic": "budget"},
            )
        )

        assert response.status.status == "success"
        assert response.status.backend == "qdrant"

        # Precomputed chunk vector skips chunk embedding, but summary embedding is still generated.
        assert len(embedding_client.calls) == 1
        call = embedding_client.calls[0]
        assert call["payload"].get("metadata", {}).get("source") == "retrieval_upsert_summary"
        assert call["url"].endswith("/embedding/generate")

        await store.close()

    async def test_query_retrieval_triggers_generate_embedding_for_query(self, monkeypatch):
        """query_retrieval must call generate_embedding to encode the search query."""
        store, embedding_client, _ = _make_store(monkeypatch)

        # First, upsert a document (will also call generate_embedding once)
        await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-query-test",
                content="Mira arbeitet in Ulm.",
                source="chat-context",
                metadata={"topic": "location"},
            )
        )
        embedding_client.calls.clear()  # Reset counter, focus on query phase

        query_response = await store.query_retrieval(
            MemoryRetrievalQueryRequest(
                query="Wo arbeitet Mira?",
                top_k=3,
            )
        )

        # Query must succeed
        assert query_response.status.status == "success"
        assert query_response.status.backend == "qdrant"

        # Embedding service must have been called for the query text
        assert len(embedding_client.calls) == 1
        call = embedding_client.calls[0]
        assert call["url"].endswith("/embedding/generate")
        assert "Mira" in call["payload"]["input_text"]

        await store.close()

    async def test_full_embed_upsert_query_round_trip(self, monkeypatch):
        """
        Full round-trip:
          1. Upsert two documents → embedding generated for each
          2. Query → embedding generated for query → relevant document returned
        """
        store, embedding_client, _ = _make_store(monkeypatch)

        await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-rt-1",
                content="Mira heisst Mira und kommt aus Ulm.",
                source="session-rt",
                metadata={"topic": "identity"},
            )
        )
        await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-rt-2",
                content="Budget fuer das Projekt betraegt 120000 Euro.",
                source="session-rt",
                metadata={"topic": "budget"},
            )
        )

        # Two-level ingestion: each upsert triggers chunk + summary embedding.
        assert len(embedding_client.calls) == 4

        query_response = await store.query_retrieval(
            MemoryRetrievalQueryRequest(
                query="Wie heisst die Person und woher kommt sie?",
                top_k=2,
            )
        )

        # Plus one query embedding call.
        assert len(embedding_client.calls) == 5

        # Results must come back from Qdrant
        assert query_response.status.backend == "qdrant"
        assert len(query_response.items) > 0
        assert all(item.document_id.startswith("doc-rt-") for item in query_response.items)

        await store.close()

    async def test_upsert_without_embedding_service_url_returns_failed_status(self, monkeypatch):
        """When EMBEDDING_SERVICE_BASE_URL is not configured, upsert must return a clear failure."""
        # Ensure no env var bleeds in and silently overrides the None we pass
        monkeypatch.delenv("EMBEDDING_SERVICE_BASE_URL", raising=False)

        qdrant_client = FakeQdrantClient()
        _install_fake_qdrant(monkeypatch, qdrant_client)

        retrieval_index = RetrievalIndex(
            qdrant_url="http://qdrant.local:6333",
            collection_name="chat_embed_no_svc",
            client=qdrant_client,
        )
        fact_store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: FakePool(),
        )
        store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=fact_store,
            retrieval_index=retrieval_index,
            embedding_service_base_url=None,  # Not configured
        )

        response = await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-no-svc",
                content="Kein Embedding-Service vorhanden.",
                source="test",
                metadata={},
            )
        )

        assert response.status.degraded is True
        assert response.status.status == "failed"
        assert "embedding" in (response.status.error or "").lower()

        await store.close()
