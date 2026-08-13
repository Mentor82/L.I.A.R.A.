import logging

from services.config import Settings
from services.memory.stores.backed import BackedMemoryServiceStore
from services.memory.stores.base import MemoryServiceStore, PersistentStorageUnavailableError
from services.memory.stores.in_memory import InMemoryMemoryServiceStore

_LOGGER = logging.getLogger(__name__)


def create_default_memory_service_store() -> MemoryServiceStore:
    """Return the memory service store according to PERSISTENCE_POLICY (strict | degraded | development)."""
    policy = str(getattr(Settings, "PERSISTENCE_POLICY", "strict")).lower()

    if policy == "development":
        _LOGGER.info("PERSISTENCE_POLICY=development: Using InMemoryMemoryServiceStore")
        return InMemoryMemoryServiceStore()

    try:
        store = BackedMemoryServiceStore()
        health = store.get_health() if hasattr(store, "get_health") else {}
        active_backends = health.get("metadata", {}).get("backend_health", {}) if isinstance(health, dict) else {}
        
        # Check required relational backend
        postgres_ok = active_backends.get("postgres") == "healthy" or active_backends.get("postgres") == "ok"
        if not postgres_ok and active_backends:
            if policy == "strict":
                raise PersistentStorageUnavailableError("Required relational memory storage capability (postgres) is unavailable under strict policy.")
            elif policy == "degraded":
                _LOGGER.warning("Required persistent storage unavailable under degraded policy; falling back to InMemoryMemoryServiceStore")
                fallback_store = InMemoryMemoryServiceStore()
                setattr(fallback_store, "_effective_store_mode", "degraded_in_memory")
                setattr(fallback_store, "_fallback_reason_code", "POSTGRES_UNAVAILABLE")
                setattr(fallback_store, "_degradation_codes", ["POSTGRES_UNAVAILABLE"])
                return fallback_store

        return store

    except PersistentStorageUnavailableError:
        raise
    except Exception as exc:
        _LOGGER.error("Failed to instantiate BackedMemoryServiceStore: %s", exc)
        if policy == "strict":
            raise PersistentStorageUnavailableError(f"Persistent memory storage initialization failed under strict policy: {exc}") from exc
        elif policy == "degraded":
            _LOGGER.warning("BackedMemoryServiceStore failed under degraded policy; falling back to InMemoryMemoryServiceStore")
            fallback_store = InMemoryMemoryServiceStore()
            setattr(fallback_store, "_effective_store_mode", "degraded_in_memory")
            setattr(fallback_store, "_fallback_reason_code", "POSTGRES_UNAVAILABLE")
            setattr(fallback_store, "_degradation_codes", ["POSTGRES_UNAVAILABLE"])
            return fallback_store
        else:
            return InMemoryMemoryServiceStore()
