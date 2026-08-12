"""Integration tests for Safe Simulation Mode.

Tests the full flow:
1. Orchestrator.run() with simulation_mode=True
2. Judge pre-action gate
3. Tool coordinator mock result generation
4. Full end-to-end simulation without actual execution
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from services.contracts import (
    OrchestratorRequest,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from services.judge.contracts import JudgeContext, JudgeStage, JudgeDecisionType
from services.judge.adapters import evaluate_pre_action_simulation_mode
from services.judge.engine import JudgeEngine
from services.tools.coordinator import ToolCoordinator
from services.simulation.mock_result_generator import MockResultGenerator


class TestSafeSimulationMode:
    """Test Safe Simulation Mode end-to-end."""

    def test_mock_result_generator_sys_time(self):
        """Mock result for sys time lookup."""
        result = MockResultGenerator.generate_sys_result(
            command="date",
            context="agent_time_lookup",
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert "utc_iso" in result.output
        assert result.simulated is True
        assert result.metadata.get("simulation_type") == "time_lookup"

    def test_mock_result_generator_sys_web_search(self):
        """Mock result for sys web search/curl."""
        result = MockResultGenerator.generate_sys_result(
            command="curl",
            args=["https://example.com"],
            context="agent_web_lookup",
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert "results" in result.output
        assert len(result.output["results"]) > 0
        assert result.simulated is True

    def test_mock_result_generator_compute_turbine(self):
        """Mock result for compute simulation (turbine model)."""
        result = MockResultGenerator.generate_compute_result(
            model="turbine_power",
            inputs={"shaft_speed_rpm": 1500.0, "torque_nm": 200.0},
        )
        assert result.tool_name == "compute.run"
        assert result.status == "success"
        assert "power_kw" in result.output["results"]
        assert result.output["results"]["power_kw"] > 0
        assert result.simulated is True

    def test_mock_result_generator_file_list(self):
        """Mock result for file listing."""
        result = MockResultGenerator.generate_file_result(
            tool_name="list_files",
            path="/home/liara/workspace",
        )
        assert result.tool_name == "list_files"
        assert result.status == "success"
        assert "entries" in result.output
        assert len(result.output["entries"]) > 0
        assert result.simulated is True

    def test_judge_simulation_mode_adapter_inactive(self):
        """Test simulation mode adapter when mode is OFF."""
        context = JudgeContext(
            request_id="test_1",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="sys",
            input={"command": "ls"},
            metadata={"simulation_mode": False},
        )
        decision = evaluate_pre_action_simulation_mode(context)
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.constraints.get("simulation_mode") is False
        assert decision.constraints.get("action") == "pass_through"

    def test_judge_simulation_mode_adapter_active_supported_action(self):
        """Test simulation mode adapter when mode is ON with supported action."""
        context = JudgeContext(
            request_id="test_2",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="compute.run",
            input={"model": "turbine_power", "inputs": {}},
            metadata={"simulation_mode": True},
        )
        decision = evaluate_pre_action_simulation_mode(context)
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.simulated is True
        assert decision.reason_code == "simulation_mode.active"
        assert decision.constraints.get("simulation_mode") is True
        assert decision.constraints.get("simulated_execution") is True
        assert decision.constraints.get("skip_actual_execution") is True
        assert decision.confidence == pytest.approx(0.86, abs=1e-9)

    def test_judge_simulation_mode_adapter_active_unsupported_action(self):
        """Test simulation mode adapter when mode is ON with unsupported action."""
        context = JudgeContext(
            request_id="test_3",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="unknown_tool",
            input={},
            metadata={"simulation_mode": True},
        )
        decision = evaluate_pre_action_simulation_mode(context)
        assert decision.decision in {JudgeDecisionType.WARN, JudgeDecisionType.BLOCK}
        assert decision.simulated is True
        assert decision.reason_code == "simulation_mode.active"
        assert decision.confidence == pytest.approx(0.50, abs=1e-9)
        assert len(decision.issues) > 0

    def test_judge_simulation_mode_confidence_sys_simple_command(self):
        """Simple sys commands in simulation mode should have very high confidence."""
        context = JudgeContext(
            request_id="test_3a",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="sys",
            input={"command": "date", "args": []},
            metadata={"simulation_mode": True, "mock_profile": "default"},
        )
        decision = evaluate_pre_action_simulation_mode(context)
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.confidence == pytest.approx(0.99, abs=1e-9)

    def test_judge_simulation_mode_confidence_uses_mock_profile(self):
        """Mock profile should lower confidence for less reliable simulation profiles."""
        context = JudgeContext(
            request_id="test_3b",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="compute.run",
            input={
                "model": "turbine_power",
                "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200},
            },
            metadata={"simulation_mode": True, "mock_profile": "low_fidelity"},
        )
        decision = evaluate_pre_action_simulation_mode(context)
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.confidence == pytest.approx(0.85, abs=1e-9)

    def test_judge_engine_routes_simulation_mode_first(self):
        """Test that JudgeEngine checks simulation mode before other adapters."""
        engine = JudgeEngine()
        
        # Test: simulation_mode=False → should route to standard adapter
        context_normal = JudgeContext(
            request_id="test_4",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="sys",
            input={"command": "ls", "args": ["-la", "/home/liara/workspace"]},
            metadata={"simulation_mode": False},
        )
        decision_normal = engine.evaluate_pre_action(context_normal)
        # Should pass through to sys adapter
        assert decision_normal.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}

        # Test: simulation_mode=True → should gate with simulation adapter
        context_sim = JudgeContext(
            request_id="test_5",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="test",
            action="sys",
            input={"command": "ls", "args": ["-la"]},
            metadata={"simulation_mode": True},
        )
        decision_sim = engine.evaluate_pre_action(context_sim)
        assert decision_sim.decision == JudgeDecisionType.ALLOW
        assert decision_sim.constraints.get("simulated_execution") is True

    @pytest.mark.asyncio
    async def test_tool_coordinator_simulation_mode_mock_result(self):
        """Test that ToolCoordinator generates mock results when simulation_mode=True."""
        coordinator = ToolCoordinator()

        # Test: normal execution (simulation_mode=False)
        request_normal = ToolExecutionRequest(
            tool_name="sys",
            parameters={"command": "date", "args": []},
            timeout_seconds=5,
            simulation_mode=False,
        )
        # This would execute the actual tool - we'll just verify the structure
        # (actual execution would require a real working environment)

        # Test: simulated execution (simulation_mode=True)
        request_sim = ToolExecutionRequest(
            tool_name="sys",
            parameters={"command": "date", "context": "agent_time_lookup"},
            timeout_seconds=5,
            simulation_mode=True,
        )
        result_sim = await coordinator.execute_tool(request_sim)
        assert result_sim.tool_name == "sys"
        assert result_sim.status == "success"
        assert result_sim.output is not None
        assert "utc_iso" in result_sim.output

    @pytest.mark.asyncio
    async def test_tool_coordinator_parallel_simulation(self):
        """Test parallel tool execution in simulation mode."""
        coordinator = ToolCoordinator()

        requests = [
            ToolExecutionRequest(
                tool_name="sys",
                parameters={"command": "date", "context": "agent_time_lookup"},
                timeout_seconds=5,
                simulation_mode=True,
            ),
            ToolExecutionRequest(
                tool_name="compute.run",
                parameters={"model": "turbine_power", "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200}},
                timeout_seconds=5,
                simulation_mode=True,
            ),
        ]

        results = await coordinator.execute_tools_parallel(requests)
        
        assert len(results) == 2
        assert "sys" in results
        assert "compute.run" in results
        assert results["sys"].status == "success"
        assert results["compute.run"].status == "success"

    @pytest.mark.asyncio
    async def test_mock_result_generator_unified_interface(self):
        """Test MockResultGenerator.generate() unified interface."""
        # Test: sys command
        result_sys = MockResultGenerator.generate(
            tool_name="sys",
            parameters={"command": "ls", "args": ["-la"]},
        )
        assert result_sys.tool_name == "sys"
        assert result_sys.status == "success"

        # Test: compute model
        result_compute = MockResultGenerator.generate(
            tool_name="compute.run",
            parameters={"model": "turbine_power", "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200}},
        )
        assert result_compute.tool_name == "compute.run"
        assert result_compute.status == "success"

        # Test: file operations
        result_files = MockResultGenerator.generate(
            tool_name="list_files",
            parameters={"path": "/home/liara/workspace"},
        )
        assert result_files.tool_name == "list_files"
        assert result_files.status == "success"

        # Test: web search
        result_search = MockResultGenerator.generate(
            tool_name="web_search",
            parameters={"query": "Python asyncio"},
        )
        assert result_search.tool_name == "web_search"
        assert result_search.status == "success"

        # Test: unsupported tool
        result_unsupported = MockResultGenerator.generate(
            tool_name="unknown_tool",
            parameters={},
        )
        assert result_unsupported.tool_name == "unknown_tool"
        assert result_unsupported.status == "simulated_error"
        assert result_unsupported.error is not None


class TestSimulationModeWorkflow:
    """Test complete simulation mode workflows."""

    @pytest.mark.asyncio
    async def test_orchestrator_request_with_simulation_flag(self):
        """Test OrchestratorRequest accepts simulation_mode flag."""
        request = OrchestratorRequest(
            session_id="session_1",
            run_id="run_1",
            user_id="user_1",
            query="What is the current time?",
            simulation_mode=True,  # Enable simulation mode
        )
        assert request.simulation_mode is True

    @pytest.mark.asyncio
    async def test_tool_execution_request_with_simulation_flag(self):
        """Test ToolExecutionRequest propagates simulation_mode."""
        request = ToolExecutionRequest(
            tool_name="sys",
            parameters={"command": "date"},
            timeout_seconds=5,
            simulation_mode=True,
        )
        assert request.simulation_mode is True

    def test_simulation_confidence_metadata(self):
        """Test that mock results include simulation confidence."""
        result = MockResultGenerator.generate(
            tool_name="compute.run",
            parameters={"model": "turbine_power", "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200}},
        )
        assert result.simulation_confidence >= 0.8  # High confidence for supported simulations
        assert result.simulated is True
        assert result.metadata is not None
        assert result.metadata.get("simulated") is True

    def test_simulation_benefits_planning(self):
        """Demonstrate safe simulation for planning without execution."""
        # In simulation mode, the Agent can:
        # 1. Plan the next steps
        # 2. Evaluate which tools to use
        # 3. Test tool routing logic
        # 4. Check response quality
        # All WITHOUT executing actual commands or making side effects

        tools_to_plan = ["sys", "compute.run", "list_files"]
        
        for tool_name in tools_to_plan:
            if tool_name == "sys":
                result = MockResultGenerator.generate(
                    tool_name="sys",
                    parameters={"command": "ls"},
                )
            elif tool_name == "compute.run":
                result = MockResultGenerator.generate(
                    tool_name="compute.run",
                    parameters={"model": "turbine_power", "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200}},
                )
            elif tool_name == "list_files":
                result = MockResultGenerator.generate(
                    tool_name="list_files",
                    parameters={"path": "/home/liara/workspace"},
                )

            # Verify that result is safe and simulated
            assert result.simulated is True
            assert result.status in {"success", "simulated_error"}
            print(f"[Planning] Tool {tool_name} simulation OK (confidence={result.simulation_confidence})")
