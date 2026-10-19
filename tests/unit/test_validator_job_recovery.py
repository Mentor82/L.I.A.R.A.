import asyncio
import time
import threading
import pytest
from unittest.mock import patch

from services.contracts import ValidatorSubmitRequest
from services.memory.stores.in_memory import InMemoryMemoryServiceStore


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "failed"])
async def test_lease_is_cleared_for_each_terminal_execution(state):
    store = InMemoryMemoryServiceStore()
    with patch("services.memory.stores.in_memory._validator_async_enabled", return_value=False), \
         patch("services.memory.store._execute_validator_job", return_value={
             "state": state, "summary": {}, "findings": [], "artifacts": [],
         }):
        result = await store.validator_submit(ValidatorSubmitRequest(workspace="C:/workspace", scope="quick"))
    payload = store._validator_jobs[result.job_id]
    assert payload["state"] == state
    assert payload["lease_owner"] is None
    assert payload["lease_expires_at"] is None


@pytest.mark.asyncio
async def test_old_fencing_token_cannot_overwrite_reclaimed_result():
    store = InMemoryMemoryServiceStore()
    entered, release = threading.Event(), threading.Event()

    def old_worker(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"state": "failed", "summary": {"owner": "old"}, "findings": [], "artifacts": []}

    with patch("services.memory.stores.in_memory._validator_async_enabled", return_value=True), \
         patch("services.memory.store._execute_validator_job", side_effect=old_worker):
        result = await store.validator_submit(ValidatorSubmitRequest(workspace="C:/workspace", scope="quick"))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            payload = store._validator_jobs[result.job_id]
            payload.update(fencing_token=payload.get("fencing_token", 1) + 1, state="completed",
                           summary={"owner": "new"}, lease_owner=None, lease_expires_at=None)
        finally:
            release.set()
        await store._validator_tasks[result.job_id]
    assert payload["state"] == "completed"
    assert payload["summary"] == {"owner": "new"}


@pytest.mark.asyncio
async def test_validator_job_lease_and_fencing_token():
    store = InMemoryMemoryServiceStore()
    
    # Submit job in sync mode to inspect lease lifecycle
    with patch("services.memory.stores.in_memory._validator_async_enabled", return_value=False), \
         patch("services.memory.store._execute_validator_job", return_value={
             "state": "completed", "summary": {"exit_code": 0}, "findings": [], "artifacts": [],
         }):
        res = await store.validator_submit(
            ValidatorSubmitRequest(
                workspace="C:/workspace",
                scope="quick",
                checks=["validate"],
            )
        )

    job_id = res.job_id
    payload = store._validator_jobs[job_id]
    
    # After sync completion, job state is completed and lease is cleared
    assert payload["state"] == "completed"
    assert payload["lease_owner"] is None
    assert payload["lease_expires_at"] is None


@pytest.mark.asyncio
async def test_validator_job_shutdown_leaves_lease_expired_for_recovery():
    store = InMemoryMemoryServiceStore()
    
    # Mock long running execution
    def slow_exec(*args, **kwargs):
        time.sleep(2.0)
        return {"state": "completed", "summary": {}, "findings": [], "artifacts": []}

    with patch("services.memory.stores.in_memory._validator_async_enabled", return_value=True), \
         patch("services.memory.stores.in_memory._execute_validator_job", side_effect=slow_exec):
        
        res = await store.validator_submit(
            ValidatorSubmitRequest(
                workspace="C:/workspace",
                scope="quick",
                checks=["validate"],
            )
        )
        job_id = res.job_id
        
        # Give task a moment to start and enter "running" state with active lease
        await asyncio.sleep(0.1)
        payload = store._validator_jobs[job_id]
        assert payload["state"] == "running"
        assert payload["lease_owner"].startswith("worker_")
        assert payload["lease_expires_at"] > time.time()

        # Trigger graceful shutdown per Nephy Rule 4
        await store.shutdown_validator_jobs()

        # Per Nephy Rule 4: Job must NOT be marked failed/FAILED_PERMANENT!
        # It must be reset to "queued" with expired lease for cold-start recovery.
        updated_payload = store._validator_jobs[job_id]
        assert updated_payload["state"] == "queued"
        assert updated_payload["lease_owner"] is None
        assert updated_payload["lease_expires_at"] < time.time()
