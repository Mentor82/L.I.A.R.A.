from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.config import Settings
from services.memory.app import create_memory_service_app
from services.memory.stores.backed import BackedMemoryServiceStore
from services.memory.stores.base import EphemeralMemoryStore
from services.memory.tier_store import ContextStore
from services.contracts import ContextScope


@pytest.mark.parametrize("backend_fails", [False, True])
def test_context_fallback_survives_write_and_search_without_http_500(monkeypatch, backend_fails):
    monkeypatch.setattr(Settings, "CHROMA_HOST", "")
    monkeypatch.setattr(Settings, "QDRANT_URL", "")
    monkeypatch.setattr(Settings, "NEO4J_URL", "")
    context = SimpleNamespace(
        context_upsert=AsyncMock(side_effect=RuntimeError("backend offline")),
        context_search=AsyncMock(side_effect=RuntimeError("backend offline")),
    ) if backend_fails else None
    store = BackedMemoryServiceStore(
        session_store=EphemeralMemoryStore(), fact_store=EphemeralMemoryStore(), context_store=context,
    )
    with TestClient(create_memory_service_app(store)) as client:
        write = client.post("/context/upsert", json={
            "document_id": "fallback-regression", "content": "unique fallback marker",
            "scope": {"session_id": "fallback-session", "run_id": "fallback-run"},
        })
        found = client.post("/context/search", json={
            "query": "unique fallback marker", "scope": {"session_id": "fallback-session"},
        })
        isolated = client.post("/context/search", json={
            "query": "unique fallback marker", "scope": {"session_id": "other-session"},
        })
    assert write.status_code == found.status_code == isolated.status_code == 200
    assert write.json()["status"]["status"] == "partial"
    assert found.json()["status"]["degraded"] is True
    assert [item["document_id"] for item in found.json()["items"]] == ["fallback-regression"]
    assert isolated.json()["items"] == []


async def test_chroma_search_combines_session_and_run_with_and():
    captured = {}

    def query(**kwargs):
        captured.update(kwargs)
        assert kwargs["where"] == {"$and": [
            {"session_id": {"$eq": "s"}}, {"run_id": {"$eq": "r"}},
        ]}
        return {"ids": [["doc"]], "documents": [["content"]], "distances": [[0.0]],
                "metadatas": [[{"session_id": "s", "run_id": "r"}]]}

    client = SimpleNamespace(get_or_create_collection=lambda **kw: SimpleNamespace(query=query))
    store = ContextStore(client=client)
    hits = await store.context_search("content", ContextScope(session_id="s", run_id="r"))
    assert hits[0]["scope"] == {"session_id": "s", "run_id": "r"}
