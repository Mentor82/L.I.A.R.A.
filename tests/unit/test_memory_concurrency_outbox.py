import asyncio
import time
import pytest

from services.memory.stores.backed import BackedMemoryServiceStore
from services.memory.stores.in_memory import InMemoryMemoryServiceStore
from services.contracts import MemoryFactUpsertRequest


@pytest.mark.asyncio
async def test_in_memory_store_async_lock():
    store = InMemoryMemoryServiceStore()
    
    async def concurrent_upsert(index: int):
        req = MemoryFactUpsertRequest(
            namespace="concurrency_test",
            key=f"key_{index}",
            value=f"value_{index}",
            source="unit-test",
            confidence=0.9,
        )
        return await store.upsert_fact(req)

    results = await asyncio.gather(*(concurrent_upsert(i) for i in range(10)))
    assert len(results) == 10
    health = store.get_health()
    assert health["effective_store_mode"] == "in_memory"


@pytest.mark.asyncio
async def test_outbox_event_recording_data_efficiency():
    store = BackedMemoryServiceStore()
    event = await store.record_outbox_event(
        aggregate_type="fact",
        aggregate_id="prefs:model",
        payload={"namespace": "prefs", "key": "model", "version": 1},
    )

    assert event["event_id"].startswith("outbox_")
    assert event["status"] == "PENDING"
    assert event["fencing_token"] == 1
    assert "raw_sensitive_memory" not in event["payload"]


@pytest.mark.asyncio
async def test_outbox_claim_lease_ttl_and_fencing():
    store = BackedMemoryServiceStore()
    await store.record_outbox_event(
        aggregate_type="fact",
        aggregate_id="fact_1",
        payload={"namespace": "default", "key": "fact_1"},
    )

    # Claim event with owner_1 and short lease TTL (0.1s)
    claimed = await store.claim_outbox_events(owner_id="worker_1", lease_ttl_seconds=0.1, limit=5)
    assert len(claimed) == 1
    assert claimed[0]["claim_owner"] == "worker_1"
    assert claimed[0]["fencing_token"] == 2

    # Attempting immediate second claim yields empty (lease active)
    claimed_again = await store.claim_outbox_events(owner_id="worker_2", lease_ttl_seconds=10.0)
    assert len(claimed_again) == 0

    # Wait for lease TTL to expire
    await asyncio.sleep(0.15)

    # Worker 2 can now claim the expired lease
    reclaimed = await store.claim_outbox_events(owner_id="worker_2", lease_ttl_seconds=10.0)
    assert len(reclaimed) == 1
    assert reclaimed[0]["claim_owner"] == "worker_2"
    assert reclaimed[0]["fencing_token"] == 3  # Fencing token incremented


@pytest.mark.asyncio
async def test_outbox_completion_with_fencing_token():
    store = BackedMemoryServiceStore()
    event = await store.record_outbox_event(
        aggregate_type="session",
        aggregate_id="sess_100",
        payload={"session_id": "sess_100"},
    )

    claimed = await store.claim_outbox_events(owner_id="worker_1", lease_ttl_seconds=10.0)
    fencing = claimed[0]["fencing_token"]

    # Completion with stale fencing token (fencing - 1) fails
    stale_res = await store.complete_outbox_event(event["event_id"], fencing_token=fencing - 1)
    assert stale_res is False

    # Completion with current fencing token succeeds
    ok_res = await store.complete_outbox_event(event["event_id"], fencing_token=fencing)
    assert ok_res is True


@pytest.mark.asyncio
async def test_outbox_failure_retries_and_permanent_failure():
    store = BackedMemoryServiceStore()
    event = await store.record_outbox_event(
        aggregate_type="context",
        aggregate_id="doc_123",
        payload={"document_id": "doc_123"},
    )

    # Fail 4 times (max_retries = 3)
    for i in range(2):
        claimed = await store.claim_outbox_events(owner_id="worker_1", lease_ttl_seconds=10.0)
        fencing = claimed[0]["fencing_token"]
        await store.fail_outbox_event(event["event_id"], fencing_token=fencing, error_code="QDRANT_TIMEOUT", max_retries=3)

    # Third failure exceeds max_retries=3
    claimed = await store.claim_outbox_events(owner_id="worker_1", lease_ttl_seconds=10.0)
    fencing = claimed[0]["fencing_token"]
    await store.fail_outbox_event(event["event_id"], fencing_token=fencing, error_code="QDRANT_TIMEOUT", max_retries=3)

    # Check status is now FAILED_PERMANENT
    events = store._outbox_events
    assert events[0]["status"] == "FAILED_PERMANENT"
    assert events[0]["retry_count"] == 3
    assert events[0]["last_error_code"] == "QDRANT_TIMEOUT"
