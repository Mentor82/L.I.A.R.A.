"""Factory helper for instantiating default memory service stores."""

from __future__ import annotations

from services.memory.stores.backed import BackedMemoryServiceStore
from services.memory.stores.base import MemoryServiceStore
from services.memory.stores.in_memory import InMemoryMemoryServiceStore


def create_default_memory_service_store() -> MemoryServiceStore:
    """Return the real store when backing services are configured, else an in-memory fallback."""
    try:
        return BackedMemoryServiceStore()
    except Exception:
        return InMemoryMemoryServiceStore()
