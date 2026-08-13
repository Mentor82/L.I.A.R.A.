import asyncio
import pytest
from typing import Dict, Any

from services.contracts import OrchestratorRequest
from services.orchestrator.orchestrator import Orchestrator
from services.orchestrator.run_context import get_current_run_context, RunContext


class SlowMockInferenceGateway:
    """Mock gateway with controlled async pause to force true overlap."""

    def __init__(self, step_event: asyncio.Event, proceed_event: asyncio.Event):
        self.step_event = step_event
        self.proceed_event = proceed_event

    async def infer(self, req: Any) -> Any:
        self.step_event.set()
        await self.proceed_event.wait()
        return type(
            "InferenceResult",
            (),
            {
                "content": f"Response for prompt: {req.prompt[:30]}",
                "provider": "mock",
                "metadata": {"test": "overlap"},
                "status": "success",
                "error": None,
            },
        )()


class FastMockInferenceGateway:
    """Fast mock gateway for stress test."""

    async def infer(self, req: Any) -> Any:
        await asyncio.sleep(0.005)
        return type(
            "InferenceResult",
            (),
            {
                "content": "Fast mock response",
                "provider": "mock",
                "metadata": {},
                "status": "success",
                "error": None,
            },
        )()


class DummyToolCoordinator:
    pass


class DummyMemoryLayer:
    def get_conversation_history(self, session_id, limit=4):
        return []

    def commit_session_turn(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_real_concurrent_async_overlap():
    req_a_started = asyncio.Event()
    req_b_proceed = asyncio.Event()

    gateway = SlowMockInferenceGateway(req_a_started, req_b_proceed)
    orchestrator = Orchestrator(
        tool_coordinator=DummyToolCoordinator(),
        inference_gateway=gateway,
        memory_layer=DummyMemoryLayer(),
    )

    context_snapshots: Dict[str, RunContext] = {}

    async def run_request_a():
        req_a = OrchestratorRequest(
            session_id="session_A",
            user_id="user_A",
            run_id="run_A",
            query="Query A",
            request_source="source_A",
            sandbox_root="/sandbox/A",
        )
        res_a = await orchestrator.run(req_a)
        context_snapshots["run_A_after"] = get_current_run_context()
        return res_a

    async def run_request_b():
        await req_a_started.wait()
        assert get_current_run_context() is None

        req_b = OrchestratorRequest(
            session_id="session_B",
            user_id="user_B",
            run_id="run_B",
            query="Query B",
            request_source="source_B",
            sandbox_root="/sandbox/B",
        )
        req_b_proceed.set()
        res_b = await orchestrator.run(req_b)
        context_snapshots["run_B_after"] = get_current_run_context()
        return res_b

    res_a, res_b = await asyncio.gather(run_request_a(), run_request_b())

    assert res_a.session_id == "session_A"
    assert res_a.run_id == "run_A"
    assert res_b.session_id == "session_B"
    assert res_b.run_id == "run_B"

    assert context_snapshots["run_A_after"] is None
    assert context_snapshots["run_B_after"] is None


@pytest.mark.asyncio
async def test_ten_parallel_stress_runs():
    gateway = FastMockInferenceGateway()
    orchestrator = Orchestrator(
        tool_coordinator=DummyToolCoordinator(),
        inference_gateway=gateway,
        memory_layer=DummyMemoryLayer(),
    )

    async def run_worker(idx: int):
        req = OrchestratorRequest(
            session_id=f"stress_session_{idx}",
            user_id=f"stress_user_{idx}",
            run_id=f"stress_run_{idx}",
            query=f"Stress query {idx}",
            request_source=f"source_{idx}",
            sandbox_root=f"/sandbox/{idx}",
        )
        res = await orchestrator.run(req)
        assert get_current_run_context() is None
        return res

    results = await asyncio.gather(*[run_worker(i) for i in range(10)])

    for i, res in enumerate(results):
        assert res.session_id == f"stress_session_{i}"
        assert res.run_id == f"stress_run_{i}"


@pytest.mark.asyncio
async def test_shared_orchestrator_instance_concurrency_no_attribute_leakage():
    """Verify that 10 concurrent runs on a SINGLE Orchestrator instance never leak instance state (session_id, user_id, simulation_mode, etc.)."""
    observed_states: Dict[int, Dict[str, Any]] = {}
    barrier = asyncio.Barrier(10)

    class BarrierInspectingGateway:
        def __init__(self, orch_ref: list):
            self.orch_ref = orch_ref

        async def infer(self, req: Any) -> Any:
            idx = int(req.prompt.split("idx=")[1].split()[0])
            orch = self.orch_ref[0]

            # Pause all 10 tasks mid-pipeline at the exact same moment to force maximum concurrency overlap
            await barrier.wait()

            # Record what orchestrator property getters see for this task
            observed_states[idx] = {
                "active_session": orch._active_session_id,
                "active_user": orch._active_user_id,
                "active_run": orch._active_run_id,
                "active_sandbox": orch._active_sandbox_root,
                "simulation_mode": orch._simulation_mode,
                "current_context": get_current_run_context(),
            }

            return type(
                "InferenceResult",
                (),
                {
                    "content": f"Response for idx={idx}",
                    "provider": "mock",
                    "metadata": {},
                    "status": "success",
                    "error": None,
                },
            )()

    orch_ref = []
    gateway = BarrierInspectingGateway(orch_ref)
    orchestrator = Orchestrator(
        tool_coordinator=DummyToolCoordinator(),
        inference_gateway=gateway,
        memory_layer=DummyMemoryLayer(),
    )
    orch_ref.append(orchestrator)

    async def run_worker(idx: int):
        sim_mode = f"sim_mode_{idx}"
        req = OrchestratorRequest(
            session_id=f"shared_session_{idx}",
            user_id=f"shared_user_{idx}",
            run_id=f"shared_run_{idx}",
            query=f"Query idx={idx} test",
            request_source=f"source_{idx}",
            sandbox_root=f"/sandbox/{idx}",
            simulation_mode=sim_mode,
        )
        res = await orchestrator.run(req)
        assert get_current_run_context() is None
        return res

    results = await asyncio.gather(*[run_worker(i) for i in range(10)])

    # Validate that all 10 parallel runs observed strictly isolated attributes without cross-leakage
    for i in range(10):
        obs = observed_states[i]
        assert obs["active_session"] == f"shared_session_{i}"
        assert obs["active_user"] == f"shared_user_{i}"
        assert obs["active_run"] == f"shared_run_{i}"
        assert obs["active_sandbox"] == f"/sandbox/{i}"
        assert obs["simulation_mode"] == f"sim_mode_{i}"
        assert obs["current_context"].session_id == f"shared_session_{i}"
        assert obs["current_context"].simulation_mode == f"sim_mode_{i}"

        res = results[i]
        assert res.session_id == f"shared_session_{i}"
        assert res.run_id == f"shared_run_{i}"
