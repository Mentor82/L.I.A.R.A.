"""HTTP regression coverage for backed memory without a graph backend."""

from fastapi.testclient import TestClient
import pytest

from services.config import Settings
from services.memory import BackedMemoryServiceStore, create_memory_service_app
from services.memory.stores.base import EphemeralMemoryStore
from services.memory.governance import MemoryLifecycleGovernance


@pytest.fixture
def fallback_store(monkeypatch):
    monkeypatch.setattr(Settings, "QDRANT_URL", "")
    monkeypatch.setattr(Settings, "CHROMA_HOST", "")
    monkeypatch.setattr(Settings, "NEO4J_URL", "")
    return BackedMemoryServiceStore(
        session_store=EphemeralMemoryStore(), fact_store=EphemeralMemoryStore(),
    )


def test_relation_fallback_write_read_isolation_and_governed_cleanup(fallback_store, monkeypatch):
    with TestClient(create_memory_service_app(fallback_store)) as client:
        edge = {"source": "message:fallback", "relation": "SUPPORTS", "target": "fact:marker", "session_id": "one", "run_id": "run-one"}
        rejected = client.post("/relations/upsert", json=edge)
        assert rejected.status_code == 200
        assert rejected.json()["status"]["status"] == "failed"
        assert client.post("/relations/expand", json={"session_id": "one"}).json()["items"] == []
        write = client.post("/relations/upsert", json={**edge, "validated": True, "metadata": {"ephemeral": True, "valid_until_ts": 1}})
        assert write.status_code == 200
        assert write.json()["status"]["error"] == "relation_fallback_in_memory"
        assert write.json()["status"]["degraded"] is True
        read = client.post("/relations/expand", json={"session_id": "one", "run_id": "run-one", "query": "marker"})
        assert read.status_code == 200
        assert read.json()["items"] == write.json()["items"]
        assert client.post("/relations/expand", json={"session_id": "other"}).json()["items"] == []
        # A new HTTP app over the same backing store retains the relation.
        with TestClient(create_memory_service_app(fallback_store)) as fresh:
            assert fresh.post("/relations/expand", json={"session_id": "one"}).json()["items"] == write.json()["items"]
        monkeypatch.setattr(MemoryLifecycleGovernance, "cleanup_allowed", lambda *args, **kwargs: (False, "test-denied"))
        blocked = client.post("/relations/cleanup-expired", json={"session_id": "one", "now_ts": 2})
        assert blocked.status_code == 200
        assert blocked.json()["removed"] == 0
        assert blocked.json()["status"]["error"] == "relation_cleanup_disabled_by_policy"
        monkeypatch.setattr(MemoryLifecycleGovernance, "cleanup_allowed", lambda *args, **kwargs: (True, "test-approved"))
        cleaned = client.post("/relations/cleanup-expired", json={"session_id": "one", "now_ts": 2})
        assert cleaned.status_code == 200
        assert cleaned.json()["removed"] == 1
        assert client.post("/relations/expand", json={"session_id": "one"}).json()["items"] == []


def test_relation_fallback_instances_do_not_share_edges(fallback_store):
    other = BackedMemoryServiceStore(session_store=EphemeralMemoryStore(), fact_store=EphemeralMemoryStore())
    assert fallback_store._relation_fallback_store is not other._relation_fallback_store
