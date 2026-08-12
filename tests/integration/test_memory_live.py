"""
Optional live integration tests for Redis and Postgres-backed memory stores.

These tests are skipped unless explicitly enabled via environment variables.
"""

import os
import uuid

import pytest

from services.memory.tier_store import FactStore, SessionStore


RUN_LIVE_MEMORY_TESTS = os.getenv("RUN_LIVE_MEMORY_TESTS") == "1"
REDIS_URL = os.getenv("REDIS_URL")
POSTGRES_URL = os.getenv("POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_MEMORY_TESTS or not REDIS_URL or not POSTGRES_URL,
    reason="live memory tests require RUN_LIVE_MEMORY_TESTS=1 plus REDIS_URL and POSTGRES_URL",
)


@pytest.mark.asyncio
class TestLiveMemoryStores:
    """Verify real backing stores when explicitly enabled."""

    async def test_session_store_round_trip_against_live_redis(self):
        store = SessionStore(redis_url=REDIS_URL)
        key = f"live:session:{uuid.uuid4()}"
        payload = {"state": "active", "tools": ["web_search"]}

        try:
            await store.set(key, payload, ttl_seconds=30)
            assert await store.exists(key) is True
            assert await store.get(key) == payload
        finally:
            await store.delete(key)
            await store.close()

    async def test_fact_store_round_trip_against_live_postgres(self):
        table_name = f"memory_facts_live_{uuid.uuid4().hex[:8]}"
        store = FactStore(
            postgres_url=POSTGRES_URL,
            table_name=table_name,
        )
        key = f"live:fact:{uuid.uuid4()}"
        payload = {"query": "live check", "result": "ok"}

        try:
            await store.initialize()
            await store.set(key, payload)
            assert await store.exists(key) is True
            assert await store.get(key) == payload
            await store.delete(key)
            assert await store.exists(key) is False
        finally:
            await store.close()
