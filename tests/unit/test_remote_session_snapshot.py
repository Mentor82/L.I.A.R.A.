"""Exercise chat -> remote adapter -> memory HTTP boundary -> session store."""

from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from services.api import create_api_app
from services.contracts import OrchestratorResponse
from services.memory import BackedMemoryServiceStore, InMemoryMemoryServiceStore, create_memory_service_app
from services.memory.stores.base import EphemeralMemoryStore
from services.memory_adapter import RemoteMemoryAdapter
from services.shared.types import MemoryTier


@pytest.mark.asyncio
@pytest.mark.parametrize("backed", [False, True])
async def test_remote_snapshot_roundtrip_missing_update_and_ttl(backed):
    store = InMemoryMemoryServiceStore()
    if backed:
        store = object.__new__(BackedMemoryServiceStore)
        store.session_store = EphemeralMemoryStore()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_memory_service_app(store)), base_url="http://memory")
    async with client:
        adapter = RemoteMemoryAdapter("http://memory", client=client)
        default = object()
        assert await adapter.get(MemoryTier.SESSION, "session:one", default) is default
        snapshot = {"session_id": "one", "last_run_id": "run-1", "metadata": {"sandbox_root": "C:/ai/LIARA"}}
        await adapter.set(MemoryTier.SESSION, "session:one", snapshot, ttl_seconds=120)
        assert await adapter.get(MemoryTier.SESSION, "session:one") == snapshot
        assert await adapter.get(MemoryTier.SESSION, "session:other") is None
        snapshot["last_run_id"] = "run-2"
        await adapter.set(MemoryTier.SESSION, "session:one", snapshot)
        # Recreate the HTTP app/client; no snapshot state lives in the adapter.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_memory_service_app(store))) as fresh_client:
            fresh = RemoteMemoryAdapter("http://memory", client=fresh_client)
            assert (await fresh.get(MemoryTier.SESSION, "session:one"))["last_run_id"] == "run-2"
        value, expiry = store.session_store._items["session:one"]
        store.session_store._items["session:one"] = (value, 0)
        assert await adapter.get(MemoryTier.SESSION, "session:one", default) is default


@pytest.mark.asyncio
async def test_snapshot_validation_and_backend_errors_are_not_hidden():
    store = InMemoryMemoryServiceStore()
    store.session_store.set = AsyncMock(side_effect=RuntimeError("backend unavailable"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_memory_service_app(store), raise_app_exceptions=False)) as client:
        adapter = RemoteMemoryAdapter("http://memory", client=client)
        with pytest.raises(ValidationError):
            await adapter.set(MemoryTier.SESSION, "session:one", {"session_id": "other"})
        with pytest.raises(ValidationError):
            await adapter.set(MemoryTier.SESSION, "session:one", {"session_id": "one"}, ttl_seconds=0)
        with pytest.raises(NotImplementedError):
            await adapter.set(MemoryTier.PERSISTENT, "session:one", {})
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.set(MemoryTier.SESSION, "session:one", {"session_id": "one"})


@pytest.mark.asyncio
async def test_normal_chat_snapshot_can_be_retrieved_through_new_api_instance(monkeypatch, caplog):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    store = InMemoryMemoryServiceStore()
    memory_app = create_memory_service_app(store)

    class Orchestrator:
        async def run(self, request):
            return OrchestratorResponse(run_id=request.run_id, final_response="snapshot-marker", state_final="complete")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=memory_app)) as memory_client:
        adapter = RemoteMemoryAdapter("http://memory", client=memory_client)
        api = create_api_app(orchestrator=Orchestrator(), memory_adapter=adapter)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api") as client:
            response = await client.post("/chat", json={"session_id": "roundtrip", "user_id": "test", "message": "Hallo"})
        assert response.status_code == 200
        fresh_adapter = RemoteMemoryAdapter("http://memory", client=memory_client)
        fresh_api = create_api_app(orchestrator=Orchestrator(), memory_adapter=fresh_adapter)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh_api), base_url="http://api") as client:
            snapshot = await client.get("/session", params={"session_id": "roundtrip", "user_id": "test"})
            history = await client.get("/history", params={"session_id": "roundtrip"})
        assert snapshot.status_code == 200
        assert snapshot.json()["last_run_id"] == response.json()["run_id"]
        assert snapshot.json()["metadata"]["sandbox_root"]
        assert len(history.json()["items"]) == 2
        assert "Session snapshot write failed" not in caplog.text
