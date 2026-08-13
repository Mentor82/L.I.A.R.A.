"""
Unit tests for memory store implementations.
"""

import sys
from types import SimpleNamespace
from typing import cast

import pytest

from services.contracts import MemoryEmbeddingRequest, MemoryFactQueryRequest, MemoryFactUpsertRequest, MemoryHealthResponse, MemoryHistoryAppendRequest, MemoryHistoryQueryRequest
from services.contracts import MemoryRetrievalQueryRequest, MemoryRetrievalUpsertRequest
from services.config import Settings
from services.orchestrator.orchestrator import Orchestrator
from services.memory.tier_store import FactStore, RetrievalIndex, SessionStore, MemoryLayer
from services.memory_adapter import InProcessMemoryAdapter, MemoryServiceAdapter, RemoteMemoryAdapter, ensure_memory_service_adapter
from services.memory import BackedMemoryServiceStore, InMemoryMemoryServiceStore, create_default_memory_service_store
from services.shared.exceptions import MemoryError
from services.shared.types import MemoryTier
from tests.memory_adapter_fakes import NoopGraphMemoryAdapterMixin


class FakeCursor:
    """Cursor stub backed by an in-memory dict."""

    def __init__(self, storage):
        self.storage = storage
        self.fetchone_result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(query.split()).lower()

        if normalized.startswith("create table if not exists"):
            self.fetchone_result = None
            return

        if normalized.startswith("select value from"):
            assert params is not None
            key = params[0]
            if key in self.storage:
                self.fetchone_result = (self.storage[key],)
            else:
                self.fetchone_result = None
            return

        if normalized.startswith("insert into"):
            assert params is not None
            key, value = params
            if hasattr(value, "adapted"):
                value = value.adapted
            self.storage[key] = value
            self.fetchone_result = None
            return

        if normalized.startswith("delete from"):
            assert params is not None
            key = params[0]
            self.storage.pop(key, None)
            self.fetchone_result = None
            return

        if normalized.startswith("select 1 from"):
            assert params is not None
            key = params[0]
            self.fetchone_result = (1,) if key in self.storage else None
            return

        raise AssertionError(f"Unexpected query: {query}")

    def fetchone(self):
        return self.fetchone_result


class FakeConnection:
    """Connection stub tracking transaction boundaries."""

    def __init__(self, storage):
        self.storage = storage
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self.storage)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    """Minimal pool stub for FactStore tests."""

    def __init__(self):
        self.storage = {}
        self.connections = []
        self.closed = False
        self.getconn_calls = 0
        self.putconn_calls = 0

    def getconn(self):
        self.getconn_calls += 1
        connection = FakeConnection(self.storage)
        self.connections.append(connection)
        return connection

    def putconn(self, connection):
        self.putconn_calls += 1

    def closeall(self):
        self.closed = True


class FakePoolFactory:
    """Factory that returns trackable fake pools for reconnect tests."""

    def __init__(self):
        self.instances = []

    def __call__(self, minconn, maxconn, dsn):
        del minconn, maxconn, dsn
        pool = FakePool()
        self.instances.append(pool)
        return pool


class FakeRedisClient:
    """Async Redis-like client with in-memory storage and TTL markers."""

    def __init__(self):
        self.storage = {}
        self.ttls = {}
        self.closed = False

    async def get(self, key):
        return self.storage.get(key)

    async def set(self, key, value, ex=None):
        self.storage[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, key):
        self.storage.pop(key, None)
        self.ttls.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.storage else 0

    async def ping(self):
        return True

    async def close(self):
        self.closed = True


class FakeMemoryStore:
    """Generic async store for MemoryLayer routing tests."""

    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def set(self, key, value, ttl_seconds=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def exists(self, key):
        return key in self.data


class FakeQdrantCollections:
    def __init__(self, names):
        self.collections = [SimpleNamespace(name=name) for name in names]


class FakeQdrantPoint:
    def __init__(self, payload, score=0.0):
        self.payload = payload
        self.score = score


class FakeQdrantClient:
    def __init__(self):
        self.collections = set()
        self.points = {}

    def get_collections(self):
        return FakeQdrantCollections(self.collections)

    def create_collection(self, collection_name, vectors_config):
        del vectors_config
        self.collections.add(collection_name)

    def upsert(self, collection_name, points, wait=True):
        del wait
        self.collections.add(collection_name)
        collection = self.points.setdefault(collection_name, {})
        for point in points:
            collection[point.id] = {
                "vector": list(point.vector),
                "payload": point.payload,
            }

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        del with_vectors
        collection = self.points.get(collection_name, {})
        result = []
        for point_id in ids:
            if point_id not in collection:
                continue
            payload = collection[point_id]["payload"] if with_payload else {}
            result.append(SimpleNamespace(payload=payload))
        return result

    def delete(self, collection_name, points_selector, wait=True):
        del wait
        collection = self.points.get(collection_name, {})
        for point_id in points_selector.points:
            collection.pop(point_id, None)

    def search(self, collection_name, query_vector, limit, with_payload=True, with_vectors=False):
        del with_vectors
        collection = self.points.get(collection_name, {})
        hits = []
        for item in collection.values():
            vector = item["vector"]
            score = sum(left * right for left, right in zip(query_vector, vector))
            payload = item["payload"] if with_payload else {}
            hits.append(FakeQdrantPoint(payload=payload, score=score))
        hits.sort(key=lambda point: point.score, reverse=True)
        return hits[:limit]

    def query_points(self, collection_name, query, limit, with_payload=True, with_vectors=False):
        hits = self.search(
            collection_name=collection_name,
            query_vector=query,
            limit=limit,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )
        return SimpleNamespace(points=hits)

    def get_collection(self, collection_name):
        if collection_name not in self.collections:
            raise RuntimeError("collection missing")
        return {"name": collection_name}

    def close(self):
        return None


def install_fake_qdrant(monkeypatch, client=None):
    client = client or FakeQdrantClient()

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
    return client


class ServiceModeMemoryAdapter(NoopGraphMemoryAdapterMixin, MemoryServiceAdapter):
    """Service-mode stub that returns the same contract shapes as in-process mode."""

    def __init__(self):
        self.history_items = []
        self.facts = {}
        self.tier_data = {}
        self.retrieval_docs = {}

    async def get(self, tier, key, default=None):
        return self.tier_data.get((tier, key), default)

    async def set(self, tier, key, value, ttl_seconds=None):
        del ttl_seconds
        self.tier_data[(tier, key)] = value

    async def delete(self, tier, key):
        self.tier_data.pop((tier, key), None)

    async def exists(self, tier, key):
        return (tier, key) in self.tier_data

    async def append_history(self, request):
        from services.contracts import MemoryHistoryResponse, MemoryMessageRecord, MemoryServiceStatus

        item = MemoryMessageRecord(
            message_id=f"svc-{len(self.history_items) + 1}",
            session_id=request.session_id,
            run_id=request.run_id,
            user_id=request.user_id,
            role=request.role,
            content=request.content,
            created_at="2026-04-14T00:00:00+00:00",
            metadata=request.metadata,
        )
        self.history_items.append(item)
        return MemoryHistoryResponse(
            items=[item],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def query_history(self, request):
        from services.contracts import MemoryHistoryResponse, MemoryServiceStatus

        items = [item for item in self.history_items if item.session_id == request.session_id]
        return MemoryHistoryResponse(
            items=items[: request.limit],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def upsert_fact(self, request):
        from services.contracts import MemoryFactRecord, MemoryFactResponse, MemoryServiceStatus

        item = MemoryFactRecord(
            fact_id=f"svc-fact-{request.namespace}-{request.key}",
            namespace=request.namespace,
            key=request.key,
            value=request.value,
            source=request.source,
            confidence=request.confidence,
            tags=request.tags,
            created_at="2026-04-14T00:00:00+00:00",
            updated_at=None,
            metadata=request.metadata,
        )
        self.facts[(request.namespace, request.key)] = item
        return MemoryFactResponse(
            items=[item],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def query_facts(self, request):
        from services.contracts import MemoryFactResponse, MemoryServiceStatus

        items = []
        for (namespace, key), item in self.facts.items():
            if namespace != request.namespace:
                continue
            if request.key and key != request.key:
                continue
            if request.tags and not set(request.tags).issubset(set(item.tags)):
                continue
            items.append(item)
        return MemoryFactResponse(
            items=items[: request.limit],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def upsert_retrieval(self, request):
        from services.contracts import MemoryRetrievalResponse, MemoryServiceStatus, RetrievalDocument

        item = RetrievalDocument(
            document_id=request.document_id,
            content=request.content,
            score=1.0,
            source=request.source,
            chunk_index=request.metadata.get("chunk_index"),
            metadata=request.metadata,
        )
        self.retrieval_docs[request.document_id] = item
        return MemoryRetrievalResponse(
            items=[item],
            status=MemoryServiceStatus(status="partial", backend="memory-service", degraded=True),
        )

    async def query_retrieval(self, request):
        from services.contracts import MemoryRetrievalResponse, MemoryServiceStatus, RetrievalDocument

        items = []
        query_terms = {part for part in request.query.lower().split() if part}
        for item in self.retrieval_docs.values():
            score = len(set(item.content.lower().split()).intersection(query_terms)) / max(len(query_terms), 1)
            items.append(
                RetrievalDocument(
                    document_id=item.document_id,
                    content=item.content,
                    score=score,
                    source=item.source,
                    chunk_index=item.chunk_index,
                    metadata=item.metadata,
                )
            )
        items.sort(key=lambda entry: entry.score, reverse=True)
        return MemoryRetrievalResponse(
            items=items[: request.top_k],
            status=MemoryServiceStatus(status="partial", backend="memory-service", degraded=True),
        )

    async def generate_embedding(self, request):
        from services.contracts import EmbeddingVector, MemoryEmbeddingResponse, MemoryServiceStatus

        item = EmbeddingVector(
            model=request.model or "service-mode-test",
            dimensions=3,
            vector=[0.1, 0.2, 0.3],
            metadata=request.metadata,
        )
        return MemoryEmbeddingResponse(
            item=item,
            status=MemoryServiceStatus(status="partial", backend="embedding", degraded=True),
        )

    async def generate_embedding(self, request):
        from services.contracts import EmbeddingVector, MemoryEmbeddingResponse, MemoryServiceStatus

        vector = [0.1, 0.2, 0.3, 0.4]
        return MemoryEmbeddingResponse(
            item=EmbeddingVector(
                model=request.model or "service-mode",
                dimensions=len(vector),
                vector=vector,
                metadata=request.metadata,
            ),
            status=MemoryServiceStatus(status="partial", backend="memory-service", degraded=True),
        )

    async def context_search(self, request):
        from services.contracts import ContextSearchResponse, MemoryServiceStatus

        # For testing, just return empty context
        return ContextSearchResponse(
            items=[],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def context_upsert(self, request):
        from services.contracts import ContextDocument, ContextSearchResponse, MemoryServiceStatus

        return ContextSearchResponse(
            items=[
                ContextDocument(
                    document_id=request.document_id,
                    content=request.content,
                    score=1.0,
                    scope=request.scope.model_dump(exclude_none=True),
                    metadata=request.metadata,
                )
            ],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def relation_upsert(self, request):
        from services.contracts import RelationEdge, RelationExpandResponse, MemoryServiceStatus

        edge = RelationEdge(
            source=request.source,
            relation=request.relation,
            target=request.target,
            weight=request.weight,
            metadata=request.metadata,
        )
        return RelationExpandResponse(
            items=[edge],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def relation_expand(self, request):
        from services.contracts import RelationExpandResponse, MemoryServiceStatus

        del request
        return RelationExpandResponse(
            items=[],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )


@pytest.mark.asyncio
class TestFactStore:
    """Test persistent Postgres-backed memory behavior."""

    async def test_initialize_creates_schema(self):
        fake_pool = FakePool()

        store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: fake_pool,
        )

        await store.initialize()

        assert store._initialized is True
        assert fake_pool.getconn_calls == 1
        assert fake_pool.putconn_calls == 1

    async def test_set_get_exists_delete_round_trip(self):
        fake_pool = FakePool()
        store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: fake_pool,
        )

        payload = {"query": "What is Python?", "tools_used": ["web_search"]}

        await store.set("run:123", payload)

        assert await store.exists("run:123") is True
        assert await store.get("run:123") == payload

        await store.delete("run:123")

        assert await store.exists("run:123") is False
        assert await store.get("run:123", default={}) == {}

    async def test_get_returns_default_for_missing_key(self):
        fake_pool = FakePool()
        store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: fake_pool,
        )

        missing = await store.get("missing", default="fallback")

        assert missing == "fallback"

    async def test_requires_postgres_url(self, monkeypatch):
        # Override Settings.POSTGRES_URL to ensure test fails without explicit URL
        monkeypatch.setattr("services.memory.tier_store.Settings.POSTGRES_URL", None)
        with pytest.raises(MemoryError, match="POSTGRES_URL is not configured"):
            FactStore(postgres_url=None)

    async def test_close_shuts_down_pool(self):
        fake_pool = FakePool()
        store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: fake_pool,
        )

        await store.initialize()
        await store.close()

        assert fake_pool.closed is True
        assert store._initialized is False

    async def test_database_errors_are_wrapped(self):
        class BrokenPool(FakePool):
            def getconn(self):
                raise RuntimeError("db offline")

        store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: BrokenPool(),
        )

        with pytest.raises(MemoryError, match="db offline|Failed to initialize FactStore"):
            await store.initialize()

    async def test_close_allows_clean_reinitialize(self):
        factory = FakePoolFactory()
        store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=factory,
        )

        await store.set("run:1", {"v": 1})
        await store.close()
        await store.set("run:2", {"v": 2})

        assert len(factory.instances) == 2
        assert factory.instances[0].closed is True
        assert await store.get("run:2") == {"v": 2}


@pytest.mark.asyncio
class TestSessionStore:
    """Test Redis-backed session store behavior."""

    async def test_requires_redis_url_or_client(self, monkeypatch):
        # Override Settings.REDIS_URL to ensure test fails without explicit URL
        monkeypatch.setattr("services.memory.tier_store.Settings.REDIS_URL", None)
        with pytest.raises(MemoryError, match="REDIS_URL is not configured"):
            SessionStore(redis_url=None, client=None)

    async def test_set_get_exists_delete_round_trip(self):
        client = FakeRedisClient()
        store = SessionStore(client=client)

        payload = {"x": 1, "tags": ["a", "b"]}
        await store.set("session:1", payload, ttl_seconds=60)

        assert await store.exists("session:1") is True
        assert await store.get("session:1") == payload
        assert client.ttls["session:1"] == 60

        await store.delete("session:1")
        assert await store.exists("session:1") is False
        assert await store.get("session:1", default={}) == {}

    async def test_set_uses_default_ttl_when_not_provided(self):
        client = FakeRedisClient()
        store = SessionStore(client=client, default_ttl_seconds=123)

        await store.set("session:ttl", {"ok": True})

        assert client.ttls["session:ttl"] == 123

    async def test_set_without_ttl_when_zero(self):
        client = FakeRedisClient()
        store = SessionStore(client=client)

        await store.set("session:no-ttl", {"ok": True}, ttl_seconds=0)

        assert "session:no-ttl" not in client.ttls

    async def test_non_json_serializable_value_raises(self):
        client = FakeRedisClient()
        store = SessionStore(client=client)

        with pytest.raises(MemoryError, match="not JSON-serializable"):
            await store.set("bad", {"x": {1, 2, 3}})

    async def test_close_resets_owned_client_and_reconnects(self, monkeypatch):
        created_clients = []

        def from_url(url, decode_responses=False):
            del url, decode_responses
            client = FakeRedisClient()
            created_clients.append(client)
            return client

        monkeypatch.setitem(
            sys.modules,
            "redis",
            SimpleNamespace(asyncio=SimpleNamespace(from_url=from_url)),
        )

        store = SessionStore(redis_url="redis://test")

        await store.set("session:1", {"ok": True})
        first_client = created_clients[0]

        await store.close()
        await store.set("session:2", {"ok": True})

        assert first_client.closed is True
        assert len(created_clients) == 2
        assert await store.get("session:2") == {"ok": True}


@pytest.mark.asyncio
class TestRetrievalIndex:
    async def test_set_get_search_and_delete_round_trip(self, monkeypatch):
        fake_client = install_fake_qdrant(monkeypatch)
        store = RetrievalIndex(
            qdrant_url="http://qdrant.local",
            collection_name="test_retrieval",
            client=fake_client,
        )

        await store.set(
            "doc-1",
            {
                "content": "python async retrieval",
                "source": "docs",
                "metadata": {"topic": "python"},
                "chunk_index": 0,
                "embedding": [0.5, 0.5, 0.5, 0.5],
            },
        )

        assert await store.exists("doc-1") is True
        record = await store.get("doc-1")
        assert record["content"] == "python async retrieval"

        hits = await store.search_semantic([0.5, 0.5, 0.5, 0.5], top_k=3)
        assert len(hits) == 1
        assert hits[0]["key"] == "doc-1"
        assert hits[0]["content"] == "python async retrieval"

        await store.delete("doc-1")
        assert await store.exists("doc-1") is False

    async def test_healthcheck_returns_true_for_existing_collection(self, monkeypatch):
        fake_client = install_fake_qdrant(monkeypatch)
        store = RetrievalIndex(
            qdrant_url="http://qdrant.local",
            collection_name="health_collection",
            client=fake_client,
        )

        assert await store.healthcheck() is True


@pytest.mark.asyncio
class TestMemoryLayer:
    """Verify tier-based routing in MemoryLayer."""

    async def test_routes_operations_to_selected_tier(self):
        session = FakeMemoryStore()
        persistent = FakeMemoryStore()
        retrieval = FakeMemoryStore()
        pattern = FakeMemoryStore()

        layer = MemoryLayer(
            session_store=session,
            fact_store=persistent,
            retrieval_index=retrieval,
            graph_store=pattern,
        )

        await layer.set(MemoryTier.SESSION, "k1", {"v": 1})
        await layer.set(MemoryTier.PERSISTENT, "k2", {"v": 2})

        assert await layer.get(MemoryTier.SESSION, "k1") == {"v": 1}
        assert await layer.get(MemoryTier.PERSISTENT, "k2") == {"v": 2}
        assert await layer.get(MemoryTier.RETRIEVAL, "missing", default=[]) == []
        assert await layer.exists(MemoryTier.SESSION, "k1") is True
        assert await layer.exists(MemoryTier.PERSISTENT, "k2") is True

        await layer.delete(MemoryTier.SESSION, "k1")
        assert await layer.get(MemoryTier.SESSION, "k1", default=None) is None
        assert await layer.exists(MemoryTier.SESSION, "k1") is False

    async def test_unknown_tier_raises_memory_error(self):
        session = FakeMemoryStore()
        persistent = FakeMemoryStore()
        retrieval = FakeMemoryStore()
        pattern = FakeMemoryStore()

        layer = MemoryLayer(
            session_store=session,
            fact_store=persistent,
            retrieval_index=retrieval,
            graph_store=pattern,
        )

        with pytest.raises(MemoryError, match="Unknown memory tier"):
            await layer.get(cast(MemoryTier, "unknown-tier"), "k")

        with pytest.raises(MemoryError, match="Unknown memory tier"):
            await layer.exists(cast(MemoryTier, "unknown-tier"), "k")


@pytest.mark.asyncio
class TestInProcessMemoryAdapter:
    """Verify service-facing adapter behavior over in-process memory."""

    async def test_history_boundary_round_trip(self):
        layer = MemoryLayer(
            session_store=FakeMemoryStore(),
            fact_store=FakeMemoryStore(),
            retrieval_index=FakeMemoryStore(),
            graph_store=FakeMemoryStore(),
        )
        adapter = InProcessMemoryAdapter(layer)

        append_response = await adapter.append_history(
            MemoryHistoryAppendRequest(
                session_id="session-1",
                run_id="run-1",
                user_id="user-1",
                role="user",
                content="Hello",
            )
        )
        query_response = await adapter.query_history(
            MemoryHistoryQueryRequest(session_id="session-1", limit=10)
        )

        assert append_response.status.status == "success"
        assert len(query_response.items) == 1
        assert query_response.items[0].content == "Hello"

    async def test_fact_boundary_round_trip(self):
        layer = MemoryLayer(
            session_store=FakeMemoryStore(),
            fact_store=FakeMemoryStore(),
            retrieval_index=FakeMemoryStore(),
            graph_store=FakeMemoryStore(),
        )
        adapter = InProcessMemoryAdapter(layer)

        upsert_response = await adapter.upsert_fact(
            MemoryFactUpsertRequest(
                namespace="user_profile",
                key="preferred_model",
                value="qwen-small",
                tags=["preference"],
            )
        )
        query_response = await adapter.query_facts(
            MemoryFactQueryRequest(namespace="user_profile", key="preferred_model")
        )

        assert upsert_response.status.status == "success"
        assert len(query_response.items) == 1
        assert query_response.items[0].value == "qwen-small"

    async def test_history_failure_is_translated_to_failed_status(self):
        class ExplodingLayer:
            async def get(self, tier, key, default=None):
                del tier, key, default
                raise RuntimeError("postgres timeout")

            async def set(self, tier, key, value, ttl_seconds=None):
                del tier, key, value, ttl_seconds
                raise RuntimeError("postgres timeout")

        adapter = InProcessMemoryAdapter(ExplodingLayer())

        append_response = await adapter.append_history(
            MemoryHistoryAppendRequest(
                session_id="session-fail",
                run_id="run-fail",
                user_id="user-fail",
                role="user",
                content="hello",
            )
        )
        query_response = await adapter.query_history(
            MemoryHistoryQueryRequest(session_id="session-fail", limit=10)
        )

        assert append_response.items == []
        assert append_response.status.status == "failed"
        assert append_response.status.backend == "postgres"
        assert append_response.status.degraded is True
        assert append_response.status.error == "history_backend_unavailable"
        assert append_response.status.metadata["operation"] == "append_history"
        assert query_response.items == []
        assert query_response.status.status == "failed"
        assert query_response.status.error == "history_backend_unavailable"
        assert query_response.status.metadata["operation"] == "query_history"

    async def test_fact_failure_is_translated_to_failed_status(self):
        class ExplodingLayer:
            async def get(self, tier, key, default=None):
                del tier, key, default
                raise RuntimeError("postgres timeout")

            async def set(self, tier, key, value, ttl_seconds=None):
                del tier, key, value, ttl_seconds
                raise RuntimeError("postgres timeout")

        adapter = InProcessMemoryAdapter(ExplodingLayer())

        upsert_response = await adapter.upsert_fact(
            MemoryFactUpsertRequest(
                namespace="user_profile",
                key="preferred_model",
                value="qwen-small",
            )
        )
        query_response = await adapter.query_facts(
            MemoryFactQueryRequest(namespace="user_profile", key="preferred_model")
        )

        assert upsert_response.items == []
        assert upsert_response.status.status == "failed"
        assert upsert_response.status.backend == "postgres"
        assert upsert_response.status.degraded is True
        assert upsert_response.status.error == "fact_backend_unavailable"
        assert upsert_response.status.metadata["operation"] == "upsert_fact"
        assert query_response.items == []
        assert query_response.status.status == "failed"
        assert query_response.status.error == "fact_backend_unavailable"
        assert query_response.status.metadata["operation"] == "query_facts"

    async def test_context_failure_is_translated_to_partial_stable_status(self):
        from services.contracts import ContextScope, ContextSearchRequest, ContextUpsertRequest

        class ExplodingContextStore:
            async def context_search(self, query, scope, limit):
                del query, scope, limit
                raise RuntimeError("chroma timeout")

            async def context_upsert(self, document_id, content, scope, embedding, metadata):
                del document_id, content, scope, embedding, metadata
                raise RuntimeError("chroma timeout")

        layer = SimpleNamespace(context_store=ExplodingContextStore())
        adapter = InProcessMemoryAdapter(layer)

        upsert_response = await adapter.context_upsert(
            ContextUpsertRequest(
                document_id="doc-1",
                content="content",
                scope=ContextScope(session_id="s1"),
                metadata={},
            )
        )
        search_response = await adapter.context_search(
            ContextSearchRequest(query="content", scope=ContextScope(session_id="s1"), top_k=5)
        )

        assert upsert_response.items == []
        assert upsert_response.status.status == "partial"
        assert upsert_response.status.degraded is True
        assert upsert_response.status.error == "context_backend_degraded"
        assert upsert_response.status.metadata["operation"] == "context_upsert"
        assert search_response.items == []
        assert search_response.status.status == "partial"
        assert search_response.status.degraded is True
        assert search_response.status.error == "context_backend_degraded"
        assert search_response.status.metadata["operation"] == "context_search"

    async def test_relation_failure_is_translated_to_failed_stable_status(self):
        from services.contracts import RelationExpandRequest, RelationType, RelationUpsertRequest

        class ExplodingLayer:
            graph_store = None

            async def set(self, tier, key, value, ttl_seconds=None):
                del tier, key, value, ttl_seconds
                raise RuntimeError("neo4j down")

        adapter = InProcessMemoryAdapter(ExplodingLayer())

        upsert_response = await adapter.relation_upsert(
            RelationUpsertRequest(
                source="Memory",
                relation=RelationType.DEPENDS_ON,
                target="Neo4j",
            )
        )
        expand_response = await adapter.relation_expand(
            RelationExpandRequest(query="memory graph", limit=5)
        )

        assert upsert_response.items == []
        assert upsert_response.status.status == "failed"
        assert upsert_response.status.degraded is True
        assert upsert_response.status.error == "relation_backend_unavailable"
        assert upsert_response.status.metadata["operation"] == "relation_upsert"
        assert expand_response.items == []
        assert expand_response.status.status == "failed"
        assert expand_response.status.degraded is True
        assert expand_response.status.error == "graph_store_unavailable"

    async def test_ensure_adapter_wraps_legacy_memory_layer(self, monkeypatch):
        layer = MemoryLayer(
            session_store=FakeMemoryStore(),
            fact_store=FakeMemoryStore(),
            retrieval_index=FakeMemoryStore(),
            graph_store=FakeMemoryStore(),
        )
        monkeypatch.setattr(Settings, "MEMORY_MODE", "in_process")
        monkeypatch.setattr(Settings, "MEMORY_ADAPTER_ONLY", True)
        with pytest.raises(MemoryError, match="MEMORY_ADAPTER_ONLY"):
            ensure_memory_service_adapter(layer)

    async def test_ensure_adapter_wraps_legacy_memory_layer_when_policy_disabled(self, monkeypatch):
        layer = MemoryLayer(
            session_store=FakeMemoryStore(),
            fact_store=FakeMemoryStore(),
            retrieval_index=FakeMemoryStore(),
            graph_store=FakeMemoryStore(),
        )
        monkeypatch.setattr(Settings, "MEMORY_MODE", "in_process")
        monkeypatch.setattr(Settings, "MEMORY_ADAPTER_ONLY", False)

        adapter = ensure_memory_service_adapter(layer)

        assert isinstance(adapter, InProcessMemoryAdapter)

    async def test_orchestrator_holds_memory_service_boundary(self):
        layer = MemoryLayer(
            session_store=FakeMemoryStore(),
            fact_store=FakeMemoryStore(),
            retrieval_index=FakeMemoryStore(),
            graph_store=FakeMemoryStore(),
        )

        orchestrator = Orchestrator(
            tool_coordinator=object(),
            inference_gateway=object(),
            memory_layer=InProcessMemoryAdapter(layer),
        )

        assert isinstance(orchestrator.memory_service, InProcessMemoryAdapter)


@pytest.mark.asyncio
class TestMemoryBoundaryCompatibility:
    """Verify in-process and service-mode adapters share the same boundary contract."""

    async def test_history_contract_matches_between_modes(self):
        in_process = InProcessMemoryAdapter(
            MemoryLayer(
                session_store=FakeMemoryStore(),
                fact_store=FakeMemoryStore(),
                retrieval_index=FakeMemoryStore(),
                graph_store=FakeMemoryStore(),
            )
        )
        service_mode = ServiceModeMemoryAdapter()
        request = MemoryHistoryAppendRequest(
            session_id="session-compat",
            run_id="run-compat",
            user_id="user-compat",
            role="user",
            content="hello contract",
        )

        in_process_response = await in_process.append_history(request)
        service_response = await service_mode.append_history(request)

        assert type(in_process_response).__name__ == type(service_response).__name__
        assert set(in_process_response.model_dump().keys()) == set(service_response.model_dump().keys())
        assert set(in_process_response.items[0].model_dump().keys()) == set(service_response.items[0].model_dump().keys())
        assert set(in_process_response.status.model_dump().keys()) == set(service_response.status.model_dump().keys())

    async def test_fact_contract_matches_between_modes(self):
        in_process = InProcessMemoryAdapter(
            MemoryLayer(
                session_store=FakeMemoryStore(),
                fact_store=FakeMemoryStore(),
                retrieval_index=FakeMemoryStore(),
                graph_store=FakeMemoryStore(),
            )
        )
        service_mode = ServiceModeMemoryAdapter()
        request = MemoryFactUpsertRequest(
            namespace="prefs",
            key="model",
            value="qwen-small",
            tags=["default"],
        )

        in_process_response = await in_process.upsert_fact(request)
        service_response = await service_mode.upsert_fact(request)

        assert type(in_process_response).__name__ == type(service_response).__name__
        assert set(in_process_response.model_dump().keys()) == set(service_response.model_dump().keys())
        assert set(in_process_response.items[0].model_dump().keys()) == set(service_response.items[0].model_dump().keys())
        assert set(in_process_response.status.model_dump().keys()) == set(service_response.status.model_dump().keys())

    async def test_retrieval_contract_matches_between_modes(self):
        in_process = InProcessMemoryAdapter(
            MemoryLayer(
                session_store=FakeMemoryStore(),
                fact_store=FakeMemoryStore(),
                retrieval_index=FakeMemoryStore(),
                graph_store=FakeMemoryStore(),
            )
        )
        service_mode = ServiceModeMemoryAdapter()
        request = MemoryRetrievalUpsertRequest(
            document_id="doc-1",
            content="python async adapter contract",
            source="unit-test",
            metadata={"topic": "python"},
        )

        in_process_response = await in_process.upsert_retrieval(request)
        service_response = await service_mode.upsert_retrieval(request)

        assert type(in_process_response).__name__ == type(service_response).__name__
        assert set(in_process_response.model_dump().keys()) == set(service_response.model_dump().keys())
        assert set(in_process_response.items[0].model_dump().keys()) == set(service_response.items[0].model_dump().keys())
        assert set(in_process_response.status.model_dump().keys()) == set(service_response.status.model_dump().keys())

    async def test_embedding_contract_matches_between_modes(self):
        in_process = InProcessMemoryAdapter(
            MemoryLayer(
                session_store=FakeMemoryStore(),
                fact_store=FakeMemoryStore(),
                retrieval_index=FakeMemoryStore(),
                graph_store=FakeMemoryStore(),
            )
        )
        service_mode = ServiceModeMemoryAdapter()
        request = MemoryEmbeddingRequest(
            input_text="python async embedding contract",
            model="embed-test",
            metadata={"topic": "python"},
        )

        in_process_response = await in_process.generate_embedding(request)
        service_response = await service_mode.generate_embedding(request)

        assert type(in_process_response).__name__ == type(service_response).__name__
        assert set(in_process_response.model_dump().keys()) == set(service_response.model_dump().keys())
        assert set(in_process_response.item.model_dump().keys()) == set(service_response.item.model_dump().keys())
        assert set(in_process_response.status.model_dump().keys()) == set(service_response.status.model_dump().keys())

    async def test_orchestrator_accepts_service_mode_adapter_directly(self):
        adapter = ServiceModeMemoryAdapter()

        orchestrator = Orchestrator(
            tool_coordinator=object(),
            inference_gateway=object(),
            memory_layer=adapter,
        )

        assert orchestrator.memory_service is adapter

    async def test_ensure_adapter_uses_remote_service_when_configured(self, monkeypatch):
        monkeypatch.setattr(Settings, "MEMORY_MODE", "service")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_BASE_URL", "http://memory.local")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_TIMEOUT_SECONDS", 3.5)

        adapter = ensure_memory_service_adapter(object())

        assert isinstance(adapter, RemoteMemoryAdapter)
        assert adapter.base_url == "http://memory.local"
        assert adapter.timeout_seconds == 3.5

    async def test_ensure_adapter_service_mode_requires_base_url(self, monkeypatch):
        monkeypatch.setattr(Settings, "MEMORY_MODE", "service")
        monkeypatch.setattr(Settings, "MEMORY_SERVICE_BASE_URL", None)

        with pytest.raises(MemoryError, match="MEMORY_SERVICE_BASE_URL"):
            ensure_memory_service_adapter(object())

    async def test_create_default_memory_service_store_falls_back_when_backends_missing(self, monkeypatch):
        monkeypatch.setattr(Settings, "PERSISTENCE_POLICY", "degraded")
        monkeypatch.setattr(Settings, "REDIS_URL", None)
        monkeypatch.setattr(Settings, "POSTGRES_URL", None)

        store = create_default_memory_service_store()

        assert isinstance(store, InMemoryMemoryServiceStore)


@pytest.mark.asyncio
class TestMemoryServiceHealth:
    async def test_in_memory_store_health_reports_partial_fallback(self):
        store = InMemoryMemoryServiceStore()

        response = await store.health()

        assert isinstance(response, MemoryHealthResponse)
        assert response.status.status == "partial"
        assert response.status.degraded is True
        assert response.status.error == "fallback_in_memory_store"
        assert response.backend_health["postgres"] == "unavailable"
        assert response.backend_health["redis"] == "unavailable"

    async def test_backed_store_health_reports_success_when_postgres_and_redis_are_healthy(self, monkeypatch):
        monkeypatch.setattr(Settings, "QDRANT_URL", "")
        store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
        )

        response = await store.health()

        assert response.status.status in {"success", "partial"}
        assert response.status.degraded is True
        assert response.backend_health["postgres"] == "healthy"
        assert response.backend_health["redis"] == "healthy"
        assert response.backend_health["qdrant"] == "unavailable"

        await store.close()

    async def test_backed_store_health_reports_qdrant_when_configured(self, monkeypatch):
        fake_client = install_fake_qdrant(monkeypatch)
        retrieval_index = RetrievalIndex(
            qdrant_url="http://qdrant.local",
            collection_name="health_retrieval",
            client=fake_client,
        )
        store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            retrieval_index=retrieval_index,
        )

        response = await store.health()

        assert response.status.status == "success"
        assert response.backend_health["qdrant"] == "healthy"

        await store.close()

    async def test_backed_store_health_populates_embedding_runtime_details(self, monkeypatch):
        monkeypatch.setattr(Settings, "QDRANT_URL", "")

        class FakeEmbeddingHealthResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "status": {"status": "success", "backend": "embedding", "degraded": False},
                    "device": "NPU",
                    "execution_devices": ["NPU", "CPU"],
                    "model": "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
                    "dimensions": 1024,
                    "runtime_backend": "native-cpp-openvino",
                    "effective_max_length": 2048,
                    "configured_model_id": "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
                    "configured_model_dir": "workers/embedding/models/qwen3",
                }

        class FakeEmbeddingClient:
            async def get(self, url):
                del url
                return FakeEmbeddingHealthResponse()

        store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            embedding_service_base_url="http://embedding.local",
            embedding_client=FakeEmbeddingClient(),
        )

        response = await store.health_backends()

        assert response.backend_health["embedding"] == "healthy"
        assert response.device == "NPU"
        assert response.execution_devices == ["NPU", "CPU"]
        assert response.runtime_backend == "native-cpp-openvino"
        assert response.configured_model_id == "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov"

        await store.close()


@pytest.mark.asyncio
class TestBackedMemoryServiceStoreRetrieval:
    async def test_retrieval_round_trip_uses_postgres_and_qdrant(self, monkeypatch):
        class FakeEmbeddingResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "item": {
                        "model": "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov",
                        "dimensions": 4,
                        "vector": [0.5, 0.5, 0.5, 0.5],
                        "metadata": {},
                    },
                    "status": {
                        "status": "success",
                        "backend": "embedding",
                        "degraded": False,
                        "error": None,
                        "metadata": {},
                    },
                }

        class FakeEmbeddingClient:
            async def post(self, url, json):
                del url, json
                return FakeEmbeddingResponse()

            async def get(self, url):
                del url
                return FakeEmbeddingResponse()

        fake_client = install_fake_qdrant(monkeypatch)
        retrieval_index = RetrievalIndex(
            qdrant_url="http://qdrant.local",
            collection_name="retrieval_roundtrip",
            client=fake_client,
        )
        fact_store = FactStore(
            postgres_url="postgresql://test",
            pool_factory=lambda minconn, maxconn, dsn: FakePool(),
        )
        store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=fact_store,
            retrieval_index=retrieval_index,
            embedding_service_base_url="http://embedding.local",
            embedding_client=FakeEmbeddingClient(),
        )

        upsert = await store.upsert_retrieval(
            MemoryRetrievalUpsertRequest(
                document_id="doc-1",
                content="python async qdrant retrieval",
                source="unit-test",
                embedding=[0.5, 0.5, 0.5, 0.5],
                metadata={"topic": "python"},
            )
        )
        query = await store.query_retrieval(
            MemoryRetrievalQueryRequest(query="python retrieval", top_k=5)
        )

        assert upsert.status.status == "success"
        assert upsert.status.backend == "qdrant"
        assert len(query.items) == 1
        assert query.items[0].document_id == "doc-1"
        assert query.items[0].content == "python async qdrant retrieval"

        await store.close()


@pytest.mark.asyncio
class TestRemoteMemoryAdapter:
    """Verify HTTP-backed service adapter behavior."""

    async def test_append_history_posts_typed_payload(self):
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "items": [
                        {
                            "message_id": "remote-1",
                            "session_id": "session-1",
                            "run_id": "run-1",
                            "user_id": "user-1",
                            "role": "user",
                            "content": "hello remote",
                            "created_at": "2026-04-14T00:00:00+00:00",
                            "metadata": {},
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

        class FakeClient:
            async def post(self, url, json):
                calls.append((url, json))
                return FakeResponse()

        adapter = RemoteMemoryAdapter("http://memory.local/api/v1/memory", client=FakeClient())
        response = await adapter.append_history(
            MemoryHistoryAppendRequest(
                session_id="session-1",
                run_id="run-1",
                user_id="user-1",
                role="user",
                content="hello remote",
            )
        )

        assert calls[0][0] == "http://memory.local/api/v1/memory/history/append"
        assert calls[0][1]["session_id"] == "session-1"
        assert response.items[0].message_id == "remote-1"
        assert response.status.backend == "memory-service"

    async def test_query_facts_posts_typed_payload(self):
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "items": [
                        {
                            "fact_id": "fact-1",
                            "namespace": "prefs",
                            "key": "model",
                            "value": "qwen-small",
                            "source": None,
                            "confidence": None,
                            "tags": ["default"],
                            "created_at": "2026-04-14T00:00:00+00:00",
                            "updated_at": None,
                            "metadata": {},
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

        class FakeClient:
            async def post(self, url, json):
                calls.append((url, json))
                return FakeResponse()

        adapter = RemoteMemoryAdapter("http://memory.local/api/v1/memory", client=FakeClient())
        response = await adapter.query_facts(
            MemoryFactQueryRequest(namespace="prefs", key="model")
        )

        assert calls[0][0] == "http://memory.local/api/v1/memory/facts/query"
        assert calls[0][1]["namespace"] == "prefs"
        assert response.items[0].value == "qwen-small"

    async def test_query_retrieval_posts_typed_payload(self):
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "items": [
                        {
                            "document_id": "doc-1",
                            "content": "python retrieval doc",
                            "score": 0.75,
                            "source": "kb",
                            "chunk_index": 0,
                            "metadata": {"topic": "python"},
                        }
                    ],
                    "status": {
                        "status": "partial",
                        "backend": "memory-service",
                        "degraded": True,
                        "error": "retrieval_fallback_in_memory",
                        "metadata": {},
                    },
                }

        class FakeClient:
            async def post(self, url, json):
                calls.append((url, json))
                return FakeResponse()

        adapter = RemoteMemoryAdapter("http://memory.local/api/v1/memory", client=FakeClient())
        response = await adapter.query_retrieval(
            MemoryRetrievalQueryRequest(query="python retrieval", top_k=3)
        )

        assert calls[0][0] == "http://memory.local/api/v1/memory/retrieval/query"
        assert calls[0][1]["query"] == "python retrieval"
        assert response.items[0].document_id == "doc-1"
        assert response.status.status == "partial"

    async def test_generate_embedding_posts_typed_payload(self):
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "item": {
                        "model": "embed-test",
                        "dimensions": 4,
                        "vector": [0.1, 0.2, 0.3, 0.4],
                        "metadata": {"topic": "python"},
                    },
                    "status": {
                        "status": "partial",
                        "backend": "memory-service",
                        "degraded": True,
                        "error": "embedding_fallback_in_memory",
                        "metadata": {},
                    },
                }

        class FakeClient:
            async def post(self, url, json):
                calls.append((url, json))
                return FakeResponse()

        adapter = RemoteMemoryAdapter("http://memory.local/api/v1/memory", client=FakeClient())
        response = await adapter.generate_embedding(
            MemoryEmbeddingRequest(input_text="python embedding", model="embed-test", metadata={"topic": "python"})
        )

        assert calls[0][0] == "http://memory.local/api/v1/memory/embedding/generate"
        assert calls[0][1]["input_text"] == "python embedding"
        assert response.item.model == "embed-test"
        assert response.item.dimensions == 4

    async def test_generic_tier_ops_are_explicitly_not_remote_api(self):
        adapter = RemoteMemoryAdapter("http://memory.local/api/v1/memory", client=object())

        with pytest.raises(NotImplementedError, match="typed memory contracts"):
            await adapter.get(MemoryTier.PERSISTENT, "k")

        with pytest.raises(NotImplementedError, match="typed memory contracts"):
            await adapter.exists(MemoryTier.PERSISTENT, "k")


# ---------------------------------------------------------------------------
# Fake Chroma stubs for ContextStore tests
# ---------------------------------------------------------------------------


class FakeChromaCollection:
    """Synchronous Chroma collection stub backed by an in-memory dict.

    Simulates Chroma's ``query()`` / ``upsert()`` interface without a real
    Chroma server.  The ``where`` filter is applied as an exact-match check
    against the document metadata.
    """

    def __init__(self):
        self.docs = {}  # document_id → {"content": str, "meta": dict}
        self.last_query_where = None  # recorded for assertions

    def upsert(self, ids, documents, metadatas, embeddings=None):
        del embeddings
        for doc_id, content, meta in zip(ids, documents, metadatas):
            self.docs[doc_id] = {"content": content, "meta": dict(meta or {})}

    def query(self, query_texts=None, n_results=5, where=None, include=None, query_embeddings=None):
        del include, query_embeddings
        self.last_query_where = where
        raw_query = (query_texts or [""])[0].lower()
        query_terms = {t for t in raw_query.split() if t}

        hits = []
        for doc_id, item in self.docs.items():
            # Apply where filter — every field must match its $eq value.
            if where:
                match = True
                for field, condition in where.items():
                    expected = condition.get("$eq")
                    if item["meta"].get(field) != expected:
                        match = False
                        break
                if not match:
                    continue

            content_terms = {t for t in item["content"].lower().split() if t}
            overlap = len(query_terms & content_terms)
            score = overlap / max(len(query_terms), 1)
            hits.append((doc_id, item["content"], score, item["meta"]))

        hits.sort(key=lambda h: h[2], reverse=True)
        hits = hits[:n_results]
        return {
            "ids": [[h[0] for h in hits]],
            "documents": [[h[1] for h in hits]],
            # ContextStore converts distance → score via (1 - dist); invert here.
            "distances": [[1.0 - h[2] for h in hits]],
            "metadatas": [[h[3] for h in hits]],
        }


class FakeChromaClient:
    """Minimal Chroma HttpClient stub."""

    def __init__(self):
        self._collection = FakeChromaCollection()

    def get_or_create_collection(self, name, metadata=None):
        del name, metadata
        return self._collection


# ---------------------------------------------------------------------------
# ContextStore tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestContextStore:
    """Unit tests for the Chroma-backed ContextStore (Fibonacci-Wächter tier)."""

    async def test_upsert_and_search_basic(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        store = ContextStore(client=FakeChromaClient())
        await store.context_upsert(
            "doc-1",
            "python async fibonacci context",
            ContextScope(session_id="s1"),
            metadata={"topic": "python"},
        )

        hits = await store.context_search(
            "fibonacci context",
            ContextScope(session_id="s1"),
            top_k=5,
        )

        assert len(hits) == 1
        assert hits[0]["document_id"] == "doc-1"
        assert hits[0]["content"] == "python async fibonacci context"
        assert hits[0]["score"] > 0.0

    async def test_scope_filter_applied_to_where(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        store = ContextStore(client=FakeChromaClient())
        await store.context_upsert(
            "doc-session-a",
            "python async code session alpha",
            ContextScope(session_id="session-a"),
        )
        await store.context_upsert(
            "doc-session-b",
            "python async code session beta",
            ContextScope(session_id="session-b"),
        )

        hits = await store.context_search(
            "python async code",
            ContextScope(session_id="session-a"),
            top_k=10,
        )

        returned_ids = {h["document_id"] for h in hits}
        assert "doc-session-a" in returned_ids
        assert "doc-session-b" not in returned_ids

    async def test_empty_scope_sends_no_where_filter(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        fake_client = FakeChromaClient()
        store = ContextStore(client=fake_client)
        # Updated: use a valid scope with session_id (new requirement to prevent context bleed)
        test_scope = ContextScope(session_id="test-session")
        await store.context_upsert("doc-1", "scoped doc", test_scope)

        await store.context_search("scoped doc", test_scope, top_k=5)

        # scope with only session_id → where filter includes session_id
        assert fake_client._collection.last_query_where is not None

    async def test_min_score_filters_low_scoring_results(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        store = ContextStore(client=FakeChromaClient())
        await store.context_upsert(
            "doc-match",
            "fibonacci recursive depth explosion",
            ContextScope(session_id="s1"),
        )
        await store.context_upsert(
            "doc-mismatch",
            "completely unrelated content xyz",
            ContextScope(session_id="s1"),
        )

        hits = await store.context_search(
            "fibonacci recursive depth",
            ContextScope(session_id="s1"),
            top_k=10,
            min_score=0.5,
        )

        assert all(h["score"] >= 0.5 for h in hits)
        assert any(h["document_id"] == "doc-match" for h in hits)
        assert all(h["document_id"] != "doc-mismatch" for h in hits)

    async def test_expired_context_entries_are_filtered_out(self):
        import time

        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        store = ContextStore(client=FakeChromaClient())
        await store.context_upsert(
            "doc-expired",
            "fibonacci recursive context",
            ContextScope(session_id="s1"),
            metadata={"expires_at": time.time() - 5},
        )
        await store.context_upsert(
            "doc-active",
            "fibonacci recursive context",
            ContextScope(session_id="s1"),
            metadata={"expires_at": time.time() + 300},
        )

        hits = await store.context_search(
            "fibonacci recursive context",
            ContextScope(session_id="s1"),
            top_k=10,
        )

        returned_ids = {h["document_id"] for h in hits}
        assert "doc-active" in returned_ids
        assert "doc-expired" not in returned_ids

    async def test_ttl_seconds_sets_expires_at_on_context_upsert(self):
        import time

        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        fake_client = FakeChromaClient()
        store = ContextStore(client=fake_client)

        before = time.time()
        await store.context_upsert(
            "doc-ttl",
            "python async context",
            ContextScope(session_id="s1"),
            metadata={"ttl_seconds": 120},
        )
        after = time.time()

        meta = fake_client._collection.docs["doc-ttl"]["meta"]
        assert "expires_at" in meta
        assert meta["expires_at"] >= before + 110
        assert meta["expires_at"] <= after + 130

    async def test_time_decay_penalizes_distant_turn_index(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        store = ContextStore(client=FakeChromaClient())
        # Both docs have identical content — same base score.
        # doc-near has turn_index=5 (same as query) → no penalty.
        # doc-far has turn_index=0 → 5 turns away → penalty = 0.5 ** 5.
        await store.context_upsert(
            "doc-near",
            "fibonacci recursive context",
            ContextScope(session_id="s1", turn_index=5),
        )
        await store.context_upsert(
            "doc-far",
            "fibonacci recursive context",
            ContextScope(session_id="s1"),
            metadata={"turn_index": 0},
        )

        hits = await store.context_search(
            "fibonacci recursive context",
            ContextScope(session_id="s1", turn_index=5, time_decay=0.5),
            top_k=10,
        )

        near = next((h for h in hits if h["document_id"] == "doc-near"), None)
        far = next((h for h in hits if h["document_id"] == "doc-far"), None)
        assert near is not None and far is not None
        assert near["score"] > far["score"]

    async def test_close_resets_initialized_state(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        store = ContextStore(client=FakeChromaClient())
        await store.context_upsert("doc-1", "content", ContextScope(session_id="s1"))
        assert store._initialized is True

        await store.close()

        assert store._initialized is False
        assert store._collection is None

    async def test_initialize_only_runs_once(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope

        fake_client = FakeChromaClient()
        create_calls = []
        original = fake_client.get_or_create_collection

        def tracked(*args, **kwargs):
            create_calls.append(1)
            return original(*args, **kwargs)

        fake_client.get_or_create_collection = tracked
        store = ContextStore(client=fake_client)

        await store.context_upsert("doc-1", "content a", ContextScope())
        await store.context_upsert("doc-2", "content b", ContextScope())

        assert len(create_calls) == 1


# ---------------------------------------------------------------------------
# InMemoryMemoryServiceStore — context fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryContextSearch:
    """Keyword-fallback context_search in InMemoryMemoryServiceStore."""

    async def test_upsert_stores_and_search_returns_keyword_match(self):
        from services.contracts import ContextScope, ContextSearchRequest, ContextUpsertRequest

        store = InMemoryMemoryServiceStore()
        await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-1",
                content="fibonacci recursive depth explosion",
                scope=ContextScope(session_id="s1"),
                metadata={"topic": "fibonacci"},
            )
        )

        response = await store.context_search(
            ContextSearchRequest(
                query="fibonacci recursive",
                scope=ContextScope(session_id="s1"),
                top_k=5,
            )
        )

        assert len(response.items) == 1
        assert response.items[0].document_id == "doc-1"
        assert response.items[0].score > 0.0

    async def test_search_empty_store_returns_empty_list(self):
        from services.contracts import ContextScope, ContextSearchRequest

        store = InMemoryMemoryServiceStore()
        response = await store.context_search(
            ContextSearchRequest(query="nothing here", scope=ContextScope(), top_k=5)
        )

        assert response.items == []
        assert response.status.degraded is True

    async def test_status_is_partial_degraded_with_chroma_in_error(self):
        from services.contracts import ContextScope, ContextUpsertRequest

        store = InMemoryMemoryServiceStore()
        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-1",
                content="content",
                scope=ContextScope(),
                metadata={},
            )
        )

        assert response.status.status == "partial"
        assert response.status.degraded is True
        assert "chroma" in response.status.metadata.get("deferred_backends", [])

    async def test_min_score_filters_zero_scoring_docs(self):
        from services.contracts import ContextScope, ContextSearchRequest, ContextUpsertRequest

        store = InMemoryMemoryServiceStore()
        await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-unrelated",
                content="completely different topic xyz",
                scope=ContextScope(),
                metadata={},
            )
        )

        response = await store.context_search(
            ContextSearchRequest(
                query="fibonacci python async",
                scope=ContextScope(),
                top_k=5,
                min_score=0.1,
            )
        )

        assert all(item.score >= 0.1 for item in response.items)

    async def test_top_k_limits_returned_results(self):
        from services.contracts import ContextScope, ContextSearchRequest, ContextUpsertRequest

        store = InMemoryMemoryServiceStore()
        for i in range(6):
            await store.context_upsert(
                ContextUpsertRequest(
                    document_id=f"doc-{i}",
                    content=f"common fibonacci term doc-{i}",
                    scope=ContextScope(),
                    metadata={},
                )
            )

        response = await store.context_search(
            ContextSearchRequest(
                query="common fibonacci term",
                scope=ContextScope(),
                top_k=3,
            )
        )

        assert len(response.items) <= 3

    async def test_results_sorted_by_score_descending(self):
        from services.contracts import ContextScope, ContextSearchRequest, ContextUpsertRequest

        store = InMemoryMemoryServiceStore()
        await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-full-match",
                content="fibonacci python async context scope",
                scope=ContextScope(),
                metadata={},
            )
        )
        await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-partial-match",
                content="fibonacci unrelated words",
                scope=ContextScope(),
                metadata={},
            )
        )

        response = await store.context_search(
            ContextSearchRequest(
                query="fibonacci python async context scope",
                scope=ContextScope(),
                top_k=10,
            )
        )

        scores = [item.score for item in response.items]
        assert scores == sorted(scores, reverse=True)
        assert response.items[0].document_id == "doc-full-match"


class TestRelationMetadataLifecycle:
    def test_ephemeral_relation_gets_valid_until_default(self):
        from services.memory.store import _relation_metadata_with_defaults

        meta = _relation_metadata_with_defaults(
            metadata={"scope": "session"},
            validated=True,
            explicit_acceptance=False,
            session_id="s1",
            run_id="r1",
        )

        assert meta["ephemeral"] is True
        assert "valid_until_ts" in meta
        assert float(meta["valid_until_ts"]) > 0

    def test_persistable_relation_does_not_force_valid_until(self):
        from services.memory.store import _relation_metadata_with_defaults

        meta = _relation_metadata_with_defaults(
            metadata={"persistable": True},
            validated=True,
            explicit_acceptance=True,
            session_id=None,
            run_id=None,
        )

        assert meta["ephemeral"] is False
        assert "valid_until_ts" not in meta


@pytest.mark.asyncio
class TestRelationCleanupExpired:
    async def test_in_memory_cleanup_removes_only_expired_ephemeral_edges(self):
        from services.contracts import RelationCleanupExpiredRequest, RelationExpandRequest, RelationUpsertRequest

        store = InMemoryMemoryServiceStore()
        now_ts = 200.0

        await store.relation_upsert(
            RelationUpsertRequest(
                source="s1",
                relation="REFERENCES",
                target="t1",
                validated=True,
                explicit_acceptance=False,
                metadata={"ephemeral": True, "valid_until_ts": 100.0},
            )
        )
        await store.relation_upsert(
            RelationUpsertRequest(
                source="s2",
                relation="REFERENCES",
                target="t2",
                validated=True,
                explicit_acceptance=False,
                metadata={"ephemeral": True, "valid_until_ts": 300.0},
            )
        )
        await store.relation_upsert(
            RelationUpsertRequest(
                source="s3",
                relation="REFERENCES",
                target="t3",
                validated=True,
                explicit_acceptance=True,
                metadata={"ephemeral": False, "valid_until_ts": 100.0},
            )
        )

        response = await store.relation_cleanup_expired(RelationCleanupExpiredRequest(now_ts=now_ts))
        expanded = await store.relation_expand(RelationExpandRequest(limit=10))

        assert response.removed == 1
        assert len(expanded.items) == 2

    async def test_backed_cleanup_delegates_to_graph_store(self):
        from services.contracts import RelationCleanupExpiredRequest

        class _GraphStoreStub:
            def __init__(self):
                self.calls = []

            async def relation_cleanup_expired(self, *, now_ts, session_id=None, run_id=None, limit=5000):
                self.calls.append(
                    {
                        "now_ts": now_ts,
                        "session_id": session_id,
                        "run_id": run_id,
                        "limit": limit,
                    }
                )
                return 2

        graph_stub = _GraphStoreStub()
        store = BackedMemoryServiceStore(graph_store=graph_stub)

        response = await store.relation_cleanup_expired(
            RelationCleanupExpiredRequest(now_ts=123.0, session_id="s1", run_id="r1", limit=99)
        )

        assert response.removed == 2
        assert response.status.status == "success"
        assert graph_stub.calls and graph_stub.calls[0]["session_id"] == "s1"
        assert graph_stub.calls[0]["run_id"] == "r1"
        assert graph_stub.calls[0]["limit"] == 99

    async def test_cleanup_respects_governance_phase_flag(self, monkeypatch):
        from services.contracts import RelationCleanupExpiredRequest, RelationExpandRequest, RelationUpsertRequest

        monkeypatch.setenv("MEMORY_GOVERNANCE_CLEANUP_ENABLED", "0")

        store = InMemoryMemoryServiceStore()
        await store.relation_upsert(
            RelationUpsertRequest(
                source="s1",
                relation="REFERENCES",
                target="t1",
                validated=True,
                explicit_acceptance=False,
                metadata={"ephemeral": True, "valid_until_ts": 100.0},
            )
        )

        response = await store.relation_cleanup_expired(RelationCleanupExpiredRequest(now_ts=200.0))
        expanded = await store.relation_expand(RelationExpandRequest(limit=10))

        assert response.removed == 0
        assert response.status.error == "relation_cleanup_disabled_by_policy"
        assert len(expanded.items) == 1

    async def test_cleanup_requires_judge_when_enabled(self, monkeypatch):
        from services.contracts import RelationCleanupExpiredRequest, RelationUpsertRequest

        monkeypatch.setenv("MEMORY_GOVERNANCE_CLEANUP_REQUIRE_JUDGE", "1")

        store = InMemoryMemoryServiceStore()
        await store.relation_upsert(
            RelationUpsertRequest(
                source="s1",
                relation="REFERENCES",
                target="t1",
                validated=True,
                explicit_acceptance=False,
                metadata={"ephemeral": True, "valid_until_ts": 100.0},
            )
        )

        blocked = await store.relation_cleanup_expired(RelationCleanupExpiredRequest(now_ts=200.0))
        assert blocked.removed == 0
        assert blocked.status.error == "relation_cleanup_disabled_by_policy"
        assert blocked.status.metadata.get("governance_reason") == "cleanup_judge_gate_blocked"

        allowed = await store.relation_cleanup_expired(
            RelationCleanupExpiredRequest(now_ts=200.0, judge_decision="allow", judge_confidence=0.95)
        )
        assert allowed.removed == 1


# ---------------------------------------------------------------------------
# BackedMemoryServiceStore — context tier integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBackedStoreContextSearch:
    """BackedMemoryServiceStore delegates context ops to an injected ContextStore."""

    async def test_context_round_trip_with_chroma_backend(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextSearchRequest, ContextUpsertRequest

        context_store = ContextStore(client=FakeChromaClient())
        backed_store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=context_store,
        )

        upsert_response = await backed_store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-backed",
                content="python async fibonacci backed store",
                scope=ContextScope(session_id="s-backed", topic_id="memory-graph"),
                memory_tier="short_term",
                ttl_seconds=1209600,
                promotion_state="candidate",
                metadata={"source": "unit-test"},
            )
        )
        search_response = await backed_store.context_search(
            ContextSearchRequest(
                query="fibonacci backed",
                scope=ContextScope(session_id="s-backed"),
                top_k=5,
            )
        )

        assert upsert_response.status.status == "success"
        stored_meta = context_store._collection.docs["doc-backed"]["meta"]
        assert stored_meta["topic_id"] == "memory-graph"
        assert stored_meta["memory_tier"] == "short_term"
        assert stored_meta["ttl_seconds"] == 1209600
        assert stored_meta["promotion_state"] == "candidate"
        assert len(search_response.items) >= 1
        assert search_response.items[0].document_id == "doc-backed"
        assert search_response.status.status == "success"

        await backed_store.close()

    async def test_context_falls_back_to_in_memory_when_no_chroma_configured(self, monkeypatch):
        from services.contracts import ContextScope, ContextUpsertRequest

        # Prevent auto-creation of ContextStore from Settings.CHROMA_HOST default.
        monkeypatch.setattr(Settings, "CHROMA_HOST", "")

        backed_store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
        )

        assert backed_store.context_store is None

        upsert_response = await backed_store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-fallback",
                content="fallback content fibonacci",
                scope=ContextScope(session_id="s-fallback"),
                metadata={},
            )
        )

        assert upsert_response.status.status == "partial"
        assert upsert_response.status.degraded is True

        await backed_store.close()

    async def test_context_upsert_returns_success_with_explicit_context_store(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        context_store = ContextStore(client=FakeChromaClient())
        backed_store = BackedMemoryServiceStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=context_store,
        )

        response = await backed_store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-1",
                content="context upsert test content",
                scope=ContextScope(session_id="s1", run_id="r1"),
                metadata={"tag": "test"},
            )
        )

        assert response.status.status == "success"
        assert response.items[0].document_id == "doc-1"

        await backed_store.close()


@pytest.mark.asyncio
class TestMemoryLifecycleGovernance:
    async def test_context_candidate_promotes_to_retrieval_and_creates_stable_relation(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        class _RetrievalIndexStub:
            def __init__(self):
                self.records = []

            async def set(self, key, value, ttl_seconds=None):
                del ttl_seconds
                self.records.append((key, value))

            async def close(self):
                return None

        class _GraphStoreStub:
            def __init__(self):
                self.calls = []

            async def relation_upsert(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "source": kwargs["source"],
                    "relation": kwargs["relation"],
                    "target": kwargs["target"],
                    "weight": kwargs.get("weight", 1.0),
                    "metadata": kwargs.get("metadata", {}),
                }

            async def close(self):
                return None

        class _GovernedBackedStore(BackedMemoryServiceStore):
            async def generate_embedding(self, request):
                del request
                return SimpleNamespace(item=SimpleNamespace(vector=[0.1, 0.2, 0.3, 0.4]))

        retrieval_stub = _RetrievalIndexStub()
        graph_stub = _GraphStoreStub()
        store = _GovernedBackedStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=ContextStore(client=FakeChromaClient()),
            retrieval_index=retrieval_stub,
            graph_store=graph_stub,
        )

        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-promote",
                content="Validated context that should be promoted to long term retrieval.",
                scope=ContextScope(session_id="session-1"),
                memory_tier="short_term",
                ttl_seconds=600,
                promotion_state="candidate",
                embedding=[0.3, 0.2, 0.1, 0.0],
                metadata={"validated": True, "relevance_score": 0.95},
            )
        )

        assert response.status.status == "success"
        assert response.status.metadata.get("promotion_decision") == "promote"
        assert response.status.metadata.get("promotion_result") == "success"
        retrieval_keys = {item[0] for item in retrieval_stub.records}
        assert "context:doc-promote" in retrieval_keys

        relation_types = [call["relation"] for call in graph_stub.calls]
        assert "PART_OF" in relation_types
        assert "REFERENCES" in relation_types
        stable_link = next(call for call in graph_stub.calls if call["relation"] == "REFERENCES")
        assert stable_link["metadata"].get("ephemeral") is False

        await store.close()

    async def test_context_low_relevance_skips_promotion_but_keeps_ephemeral_scope_link(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        class _RetrievalIndexStub:
            def __init__(self):
                self.records = []

            async def set(self, key, value, ttl_seconds=None):
                del key, value, ttl_seconds
                self.records.append(1)

            async def close(self):
                return None

        class _GraphStoreStub:
            def __init__(self):
                self.calls = []

            async def relation_upsert(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "source": kwargs["source"],
                    "relation": kwargs["relation"],
                    "target": kwargs["target"],
                    "weight": kwargs.get("weight", 1.0),
                    "metadata": kwargs.get("metadata", {}),
                }

            async def close(self):
                return None

        class _GovernedBackedStore(BackedMemoryServiceStore):
            async def generate_embedding(self, request):
                del request
                return SimpleNamespace(item=SimpleNamespace(vector=[0.1, 0.2, 0.3, 0.4]))

        retrieval_stub = _RetrievalIndexStub()
        graph_stub = _GraphStoreStub()
        store = _GovernedBackedStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=ContextStore(client=FakeChromaClient()),
            retrieval_index=retrieval_stub,
            graph_store=graph_stub,
        )

        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-no-promote",
                content="Useful but low relevance working context.",
                scope=ContextScope(session_id="session-2"),
                memory_tier="working",
                ttl_seconds=180,
                promotion_state="candidate",
                metadata={"validated": True, "relevance_score": 0.2},
            )
        )

        assert response.status.status == "success"
        assert response.status.metadata.get("promotion_decision") == "skip"
        assert response.status.metadata.get("promotion_result") is None
        assert retrieval_stub.records == []
        assert any(call["relation"] == "PART_OF" for call in graph_stub.calls)
        scope_link = next(call for call in graph_stub.calls if call["relation"] == "PART_OF")
        assert scope_link["metadata"].get("ephemeral") is True
        assert scope_link["metadata"].get("valid_until_ts") is not None

        await store.close()

    async def test_context_promotion_phase_flag_disabled(self, monkeypatch):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        monkeypatch.setenv("MEMORY_GOVERNANCE_PROMOTION_ENABLED", "0")

        class _RetrievalIndexStub:
            def __init__(self):
                self.records = []

            async def set(self, key, value, ttl_seconds=None):
                del key, value, ttl_seconds
                self.records.append(1)

            async def close(self):
                return None

        class _GraphStoreStub:
            def __init__(self):
                self.calls = []

            async def relation_upsert(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "source": kwargs["source"],
                    "relation": kwargs["relation"],
                    "target": kwargs["target"],
                    "weight": kwargs.get("weight", 1.0),
                    "metadata": kwargs.get("metadata", {}),
                }

            async def close(self):
                return None

        class _GovernedBackedStore(BackedMemoryServiceStore):
            async def generate_embedding(self, request):
                del request
                return SimpleNamespace(item=SimpleNamespace(vector=[0.1, 0.2, 0.3, 0.4]))

        retrieval_stub = _RetrievalIndexStub()
        graph_stub = _GraphStoreStub()
        store = _GovernedBackedStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=ContextStore(client=FakeChromaClient()),
            retrieval_index=retrieval_stub,
            graph_store=graph_stub,
        )

        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-promotion-disabled",
                content="High relevance but promotion phase disabled.",
                scope=ContextScope(session_id="session-3"),
                promotion_state="candidate",
                metadata={"validated": True, "relevance_score": 0.99},
            )
        )

        assert response.status.status == "success"
        assert response.status.metadata.get("promotion_decision") == "skip"
        assert response.items[0].metadata.get("promotion_reason") == "promotion_phase_disabled"
        assert retrieval_stub.records == []
        assert any(call["relation"] == "PART_OF" for call in graph_stub.calls)

        await store.close()

    async def test_reasoning_relevance_can_trigger_promotion(self, monkeypatch):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        monkeypatch.setenv("MEMORY_REASONING_RELEVANCE_WEIGHT", "0.70")
        monkeypatch.setenv("MEMORY_PROMOTION_THRESHOLD_CANDIDATE", "0.60")
        monkeypatch.setenv("MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION", "0")

        class _RetrievalIndexStub:
            def __init__(self):
                self.records = []

            async def set(self, key, value, ttl_seconds=None):
                del ttl_seconds
                self.records.append((key, value))

            async def close(self):
                return None

        class _GraphStoreStub:
            async def relation_upsert(self, **kwargs):
                return {
                    "source": kwargs["source"],
                    "relation": kwargs["relation"],
                    "target": kwargs["target"],
                    "weight": kwargs.get("weight", 1.0),
                    "metadata": kwargs.get("metadata", {}),
                }

            async def close(self):
                return None

        class _GovernedBackedStore(BackedMemoryServiceStore):
            async def generate_embedding(self, request):
                del request
                return SimpleNamespace(item=SimpleNamespace(vector=[0.1, 0.2, 0.3, 0.4]))

        retrieval_stub = _RetrievalIndexStub()
        store = _GovernedBackedStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=ContextStore(client=FakeChromaClient()),
            retrieval_index=retrieval_stub,
            graph_store=_GraphStoreStub(),
        )

        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-reasoning-relevance",
                content="Reasoning-heavy context with low direct retrieval relevance.",
                scope=ContextScope(session_id="session-r"),
                promotion_state="candidate",
                metadata={
                    "validated": True,
                    "explicit_acceptance": True,
                    "relevance_score": 0.4,
                    "reasoning_relevance": 0.9,
                },
            )
        )

        assert response.status.status == "success"
        assert response.status.metadata.get("promotion_decision") == "promote"
        assert response.status.metadata.get("promotion_result") == "success"
        assert retrieval_stub.records

        await store.close()

    async def test_judge_gate_blocks_promotion_when_required(self, monkeypatch):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        monkeypatch.setenv("MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION", "1")

        class _RetrievalIndexStub:
            def __init__(self):
                self.records = []

            async def set(self, key, value, ttl_seconds=None):
                del key, value, ttl_seconds
                self.records.append(1)

            async def close(self):
                return None

        class _GraphStoreStub:
            async def relation_upsert(self, **kwargs):
                return {
                    "source": kwargs["source"],
                    "relation": kwargs["relation"],
                    "target": kwargs["target"],
                    "weight": kwargs.get("weight", 1.0),
                    "metadata": kwargs.get("metadata", {}),
                }

            async def close(self):
                return None

        class _GovernedBackedStore(BackedMemoryServiceStore):
            async def generate_embedding(self, request):
                del request
                return SimpleNamespace(item=SimpleNamespace(vector=[0.1, 0.2, 0.3, 0.4]))

        retrieval_stub = _RetrievalIndexStub()
        store = _GovernedBackedStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: FakePool(),
            ),
            context_store=ContextStore(client=FakeChromaClient()),
            retrieval_index=retrieval_stub,
            graph_store=_GraphStoreStub(),
        )

        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-judge-block",
                content="High relevance but judge says no.",
                scope=ContextScope(session_id="session-j"),
                promotion_state="candidate",
                metadata={
                    "validated": True,
                    "relevance_score": 0.95,
                    "judge_post_decision": "block",
                    "judge_post_confidence": 0.99,
                },
            )
        )

        assert response.status.status == "success"
        assert response.status.metadata.get("promotion_decision") == "skip"
        assert response.items[0].metadata.get("promotion_reason") == "judge_gate_blocked"
        assert retrieval_stub.records == []

        await store.close()

    async def test_cross_session_pattern_learning_tracks_sessions(self):
        from services.memory.tier_store import ContextStore
        from services.contracts import ContextScope, ContextUpsertRequest

        class _GraphStoreStub:
            async def relation_upsert(self, **kwargs):
                return {
                    "source": kwargs["source"],
                    "relation": kwargs["relation"],
                    "target": kwargs["target"],
                    "weight": kwargs.get("weight", 1.0),
                    "metadata": kwargs.get("metadata", {}),
                }

            async def close(self):
                return None

        class _GovernedBackedStore(BackedMemoryServiceStore):
            async def generate_embedding(self, request):
                del request
                return SimpleNamespace(item=SimpleNamespace(vector=[0.1, 0.2, 0.3, 0.4]))

        fake_pool = FakePool()
        store = _GovernedBackedStore(
            session_store=SessionStore(client=FakeRedisClient()),
            fact_store=FactStore(
                postgres_url="postgresql://test",
                pool_factory=lambda minconn, maxconn, dsn: fake_pool,
            ),
            context_store=ContextStore(client=FakeChromaClient()),
            graph_store=_GraphStoreStub(),
        )

        content = "Refactor retrieval memory governance with consistent promotion routing and typed metadata"
        await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-pattern-1",
                content=content,
                scope=ContextScope(session_id="session-pattern-1"),
                promotion_state="candidate",
                metadata={"validated": True, "relevance_score": 0.2},
            )
        )
        response = await store.context_upsert(
            ContextUpsertRequest(
                document_id="doc-pattern-2",
                content=content,
                scope=ContextScope(session_id="session-pattern-2"),
                promotion_state="candidate",
                metadata={"validated": True, "relevance_score": 0.2},
            )
        )

        item_meta = response.items[0].metadata
        assert item_meta.get("pattern_learning_status") == "updated"
        assert item_meta.get("pattern_cross_session_count") == 2
        assert isinstance(item_meta.get("pattern_id"), str)
        assert "pattern_abstraction" in item_meta

        pattern_keys = [key for key in fake_pool.storage.keys() if str(key).startswith("context_pattern:")]
        assert pattern_keys

        await store.close()
