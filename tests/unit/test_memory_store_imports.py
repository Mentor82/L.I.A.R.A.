"""Unit tests verifying backward compatibility of imports from services.memory.store."""

from __future__ import annotations

import pytest


def test_memory_store_class_imports():
    from services.memory.store import (
        MemoryServiceStore,
        BackedMemoryServiceStore,
        InMemoryMemoryServiceStore,
        EphemeralMemoryStore,
        NullMemoryStore,
    )

    assert MemoryServiceStore is not None
    assert BackedMemoryServiceStore is not None
    assert InMemoryMemoryServiceStore is not None
    assert EphemeralMemoryStore is not None
    assert NullMemoryStore is not None


def test_memory_store_factory_import():
    from services.memory.store import create_default_memory_service_store

    assert callable(create_default_memory_service_store)


def test_memory_store_helper_imports():
    from services.memory.store import (
        _estimate_token_count,
        _embedding_text_fingerprint,
        _is_truthy,
        _context_contains_sensitive_data,
        _relation_metadata_with_defaults,
    )

    assert callable(_estimate_token_count)
    assert callable(_embedding_text_fingerprint)
    assert _is_truthy(True) is True
    assert _context_contains_sensitive_data("api_key=12345") is True
    meta = _relation_metadata_with_defaults(
        {"foo": "bar"},
        validated=False,
        explicit_acceptance=False,
        session_id="session-1",
        run_id="run-1",
    )
    assert meta["foo"] == "bar"
    assert meta["kind"] == "relation"
