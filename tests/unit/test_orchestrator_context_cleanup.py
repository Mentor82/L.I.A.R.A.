import asyncio
import pytest
from typing import Any

from services.contracts import OrchestratorRequest
from services.orchestrator.orchestrator import Orchestrator
from services.orchestrator.run_context import (
    RunContext,
    set_current_run_context,
    reset_current_run_context,
    get_current_run_context,
)


class FailingInferenceGateway:
    """Gateway that raises an exception during inference."""

    async def infer(self, req: Any) -> Any:
        raise RuntimeError("Simulated inference failure")


class CancellingInferenceGateway:
    """Gateway that cancels the calling task during inference."""

    async def infer(self, req: Any) -> Any:
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError()


class DummyToolCoordinator:
    pass


class DummyMemoryLayer:
    def get_conversation_history(self, session_id, limit=4):
        return []

    def commit_session_turn(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_lifo_nested_run_context_restoration():
    assert get_current_run_context() is None

    ctx_outer = RunContext(session_id="s_outer", user_id="u_outer", run_id="r_outer")
    token_outer = set_current_run_context(ctx_outer)
    try:
        assert get_current_run_context().session_id == "s_outer"

        ctx_inner = RunContext(session_id="s_inner", user_id="u_inner", run_id="r_inner")
        token_inner = set_current_run_context(ctx_inner)
        try:
            assert get_current_run_context().session_id == "s_inner"
        finally:
            reset_current_run_context(token_inner)

        # LIFO check: outer context must be restored
        assert get_current_run_context().session_id == "s_outer"
    finally:
        reset_current_run_context(token_outer)

    assert get_current_run_context() is None


@pytest.mark.asyncio
async def test_context_cleaned_up_on_inference_exception():
    gateway = FailingInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=DummyToolCoordinator(),
        inference_gateway=gateway,
        memory_layer=DummyMemoryLayer(),
    )

    req = OrchestratorRequest(
        session_id="s_fail",
        user_id="u_fail",
        run_id="r_fail",
        query="Failing query",
    )

    # Orchestrator handles inference failure via fallback response
    res = await orchestrator.run(req)
    assert res is not None

    # Context must be cleaned up despite inference failure
    assert get_current_run_context() is None


@pytest.mark.asyncio
async def test_context_cleaned_up_on_task_cancellation():
    gateway = CancellingInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=DummyToolCoordinator(),
        inference_gateway=gateway,
        memory_layer=DummyMemoryLayer(),
    )

    req = OrchestratorRequest(
        session_id="s_cancel",
        user_id="u_cancel",
        run_id="r_cancel",
        query="Cancelling query",
    )

    task = asyncio.create_task(orchestrator.run(req))
    await asyncio.sleep(0.005)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert get_current_run_context() is None
