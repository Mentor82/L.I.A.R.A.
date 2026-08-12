from __future__ import annotations

import pytest

from services.contracts import (
    EmbeddingVector,
    MemoryEmbeddingResponse,
    MemoryRetrievalQueryRequest,
    MemoryRetrievalUpsertRequest,
    MemoryServiceStatus,
)
from services.memory.store import BackedMemoryServiceStore


class _FakeSessionStore:
    async def set(self, key, value, ttl_seconds=None):
        del key, value, ttl_seconds

    async def get(self, key, default=None):
        del key
        return default


class _FakeFactStore:
    def __init__(self):
        self.data = {}

    async def set(self, key, value):
        self.data[key] = value

    async def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeRetrievalIndex:
    def __init__(self):
        self.set_calls = []
        self.search_hits = []

    async def set(self, key, value):
        self.set_calls.append((key, value))

    async def search_semantic(self, query_embedding, top_k=5):
        del query_embedding
        return list(self.search_hits)[:top_k]


@pytest.mark.asyncio
async def test_retrieval_upsert_chunks_never_exceed_effective_max_length(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MAX_LENGTH", "32")
    monkeypatch.setenv("RETRIEVAL_CHUNK_MAX_TOKENS", "64")
    monkeypatch.setenv("RETRIEVAL_CHUNK_OVERLAP_TOKENS", "8")

    session_store = _FakeSessionStore()
    fact_store = _FakeFactStore()
    retrieval_index = _FakeRetrievalIndex()

    store = BackedMemoryServiceStore(
        session_store=session_store,
        fact_store=fact_store,
        retrieval_index=retrieval_index,
    )

    async def _fake_generate_embedding(request):
        del request
        return MemoryEmbeddingResponse(
            item=EmbeddingVector(
                model="unit-test",
                dimensions=4,
                vector=[0.1, 0.2, 0.3, 0.4],
                metadata={},
            ),
            status=MemoryServiceStatus(status="success", backend="embedding"),
        )

    store.generate_embedding = _fake_generate_embedding  # type: ignore[method-assign]

    content = " ".join(f"tok{i}" for i in range(120))
    response = await store.upsert_retrieval(
        MemoryRetrievalUpsertRequest(
            document_id="doc-long",
            content=content,
            source="unit-test",
            metadata={"topic": "chunking"},
        )
    )

    assert response.status.status == "success"
    assert len(response.items) >= 3
    assert len(retrieval_index.set_calls) >= len(response.items)

    for item in response.items:
        assert item.metadata["chunk_token_count"] <= 32
        assert item.metadata["effective_max_length"] == 32


@pytest.mark.asyncio
async def test_two_level_retrieval_prefilters_docs_then_returns_chunks(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_TWO_LEVEL_ENABLED", "1")
    monkeypatch.setenv("RETRIEVAL_LEVEL1_TOP_DOCS", "1")
    monkeypatch.setenv("RETRIEVAL_LEVEL1_SCAN_K", "8")
    monkeypatch.setenv("RETRIEVAL_LEVEL2_SCAN_K", "8")

    session_store = _FakeSessionStore()
    fact_store = _FakeFactStore()
    retrieval_index = _FakeRetrievalIndex()

    fact_store.data["retrieval_doc:docA#chunk-0"] = {
        "document_id": "docA#chunk-0",
        "content": "alpha doc best chunk",
        "source": "unit-test",
        "chunk_index": 0,
        "metadata": {"retrieval_doc_root_id": "docA", "retrieval_level": "chunk"},
    }
    fact_store.data["retrieval_doc:docB#chunk-0"] = {
        "document_id": "docB#chunk-0",
        "content": "beta doc other chunk",
        "source": "unit-test",
        "chunk_index": 0,
        "metadata": {"retrieval_doc_root_id": "docB", "retrieval_level": "chunk"},
    }

    retrieval_index.search_hits = [
        {
            "key": "docA#summary",
            "metadata": {"retrieval_doc_root_id": "docA", "retrieval_level": "doc_summary"},
            "score": 0.98,
            "record": {},
            "content": "",
            "source": "unit-test",
            "chunk_index": -1,
        },
        {
            "key": "docB#summary",
            "metadata": {"retrieval_doc_root_id": "docB", "retrieval_level": "doc_summary"},
            "score": 0.45,
            "record": {},
            "content": "",
            "source": "unit-test",
            "chunk_index": -1,
        },
        {
            "key": "docA#chunk-0",
            "metadata": {"retrieval_doc_root_id": "docA", "retrieval_level": "chunk"},
            "score": 0.91,
            "record": {},
            "content": "alpha doc best chunk",
            "source": "unit-test",
            "chunk_index": 0,
        },
        {
            "key": "docB#chunk-0",
            "metadata": {"retrieval_doc_root_id": "docB", "retrieval_level": "chunk"},
            "score": 0.94,
            "record": {},
            "content": "beta doc other chunk",
            "source": "unit-test",
            "chunk_index": 0,
        },
    ]

    store = BackedMemoryServiceStore(
        session_store=session_store,
        fact_store=fact_store,
        retrieval_index=retrieval_index,
    )

    async def _fake_generate_embedding(request):
        del request
        return MemoryEmbeddingResponse(
            item=EmbeddingVector(
                model="unit-test",
                dimensions=4,
                vector=[0.1, 0.2, 0.3, 0.4],
                metadata={},
            ),
            status=MemoryServiceStatus(status="success", backend="embedding"),
        )

    store.generate_embedding = _fake_generate_embedding  # type: ignore[method-assign]

    response = await store.query_retrieval(
        MemoryRetrievalQueryRequest(query="alpha", top_k=3)
    )

    assert response.status.status == "success"
    assert len(response.items) == 1
    assert response.items[0].document_id == "docA#chunk-0"
    assert response.items[0].metadata["retrieval_strategy"] == "two_level"
