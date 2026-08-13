import pytest
from unittest.mock import patch, MagicMock

from services.config import Settings
from services.memory.stores.base import PersistentStorageUnavailableError
from services.memory.stores.factory import create_default_memory_service_store
from services.memory.stores.backed import BackedMemoryServiceStore
from services.memory.stores.in_memory import InMemoryMemoryServiceStore


def test_persistence_policy_development_returns_in_memory(monkeypatch):
    monkeypatch.setattr(Settings, "PERSISTENCE_POLICY", "development")
    store = create_default_memory_service_store()
    assert isinstance(store, InMemoryMemoryServiceStore)
    health = store.get_health()
    assert health["effective_store_mode"] == "in_memory"
    assert health["persistence_policy"] == "development"


def test_persistence_policy_strict_fails_closed_when_postgres_fails(monkeypatch):
    monkeypatch.setattr(Settings, "PERSISTENCE_POLICY", "strict")

    with patch("services.memory.stores.factory.BackedMemoryServiceStore") as mock_backed_cls:
        mock_instance = MagicMock()
        mock_instance.get_health.return_value = {
            "metadata": {
                "backend_health": {
                    "postgres": "unavailable",
                    "redis": "unavailable",
                    "qdrant": "unavailable",
                }
            }
        }
        mock_backed_cls.return_value = mock_instance

        with pytest.raises(PersistentStorageUnavailableError, match="postgres"):
            create_default_memory_service_store()


def test_persistence_policy_degraded_falls_back_with_degradation_codes(monkeypatch):
    monkeypatch.setattr(Settings, "PERSISTENCE_POLICY", "degraded")

    with patch("services.memory.stores.factory.BackedMemoryServiceStore") as mock_backed_cls:
        mock_instance = MagicMock()
        mock_instance.get_health.return_value = {
            "metadata": {
                "backend_health": {
                    "postgres": "unavailable",
                    "redis": "unavailable",
                }
            }
        }
        mock_backed_cls.return_value = mock_instance

        store = create_default_memory_service_store()
        assert isinstance(store, InMemoryMemoryServiceStore)
        health = store.get_health()
        assert health["effective_store_mode"] == "degraded_in_memory"
        assert health["fallback_reason_code"] == "POSTGRES_UNAVAILABLE"
        assert "POSTGRES_UNAVAILABLE" in health["degradation_codes"]


def test_backed_degraded_mode_keeps_primary_persistence_when_optional_backend_fails(monkeypatch):
    monkeypatch.setattr(Settings, "PERSISTENCE_POLICY", "strict")
    monkeypatch.setattr(Settings, "CHROMA_HOST", "")
    monkeypatch.setattr(Settings, "NEO4J_URL", "")
    monkeypatch.setattr(Settings, "QDRANT_URL", "")

    store = BackedMemoryServiceStore(
        context_store=None,  # Optional Chroma missing
        graph_store=None,    # Optional Neo4j missing
        retrieval_index=None,
    )

    health = store.get_health()
    assert health["effective_store_mode"] == "backed_degraded"
    assert health["degraded"] is True
    assert "CHROMA_UNAVAILABLE" in health["degradation_codes"]
    assert "NEO4J_UNAVAILABLE" in health["degradation_codes"]
    assert "session_store" in health["available_capabilities"]
    assert "fact_store" in health["available_capabilities"]
