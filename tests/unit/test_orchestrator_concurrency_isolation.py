"""
Unit tests for Orchestrator Concurrency Isolation & Attribute Alignment (Issue #2).
Verifies that 10 concurrent requests executing on a single Orchestrator singleton maintain 100% isolated session/user/run state.
"""

import asyncio
import pytest
from services.orchestrator.orchestrator import Orchestrator
from services.orchestrator.run_context import RunContext, set_current_run_context, get_current_run_context
from services.contracts import OrchestratorRequest


def test_orchestrator_canonical_attributes_initialized():
    """Verify that memory_service and judge_engine canonical attributes are initialized."""
    orch = Orchestrator()
    assert orch.memory_service is not None
    assert orch.memory_service == orch.memory
    assert orch.judge_engine is not None
    assert orch.judge_engine == orch.judge


@pytest.mark.asyncio
async def test_orchestrator_concurrent_requests_are_isolated():
    """Verify that concurrent requests on a single Orchestrator singleton never bleed state."""
    orch = Orchestrator()

    async def simulate_concurrent_run(task_index: int):
        session_id = f"session_test_{task_index}"
        user_id = f"user_test_{task_index}"
        run_id = f"run_test_{task_index}"

        # Construct and set isolated RunContext
        ctx = RunContext(
            session_id=session_id,
            user_id=user_id,
            run_id=run_id,
            request_source="pytest",
            sandbox_root=f"/tmp/sandbox_{task_index}",
        )
        set_current_run_context(ctx)

        # Simulate async work (yielding execution control)
        await asyncio.sleep(0.02 * (task_index % 3 + 1))

        # Assert property getters pull from task-isolated context
        assert orch._active_session_id == session_id
        assert orch._active_user_id == user_id
        assert orch._active_run_id == run_id
        assert orch._active_request_source == "pytest"
        assert orch._active_sandbox_root == f"/tmp/sandbox_{task_index}"

        curr_ctx = get_current_run_context()
        assert curr_ctx is not None
        assert curr_ctx.session_id == session_id
        assert curr_ctx.user_id == user_id
        assert curr_ctx.run_id == run_id

        return session_id

    tasks = [simulate_concurrent_run(i) for i in range(10)]
    results = await asyncio.gather(*tasks)
    assert len(results) == 10
    assert len(set(results)) == 10  # 10 distinct session IDs processed concurrently without state bleed
