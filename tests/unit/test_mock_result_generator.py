"""Unit tests for MockResultGenerator.

Tests the generation of realistic mock results for various tool types.
"""

import pytest
from services.simulation.mock_result_generator import (
    MockResultGenerator,
    MockResult,
)


class TestMockResultGenerator:
    """Unit tests for mock result generation."""

    def test_mock_result_dataclass(self):
        """Test MockResult dataclass structure."""
        result = MockResult(
            tool_name="sys",
            status="success",
            output={"test": "data"},
            simulated=True,
            simulation_confidence=0.95,
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert result.simulated is True
        assert result.output == {"test": "data"}

    def test_generate_sys_date_result(self):
        """Test sys date/time mock result."""
        result = MockResultGenerator.generate_sys_result(
            command="date",
            context="agent_time_lookup",
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert "utc_iso" in result.output
        assert "summary_text" in result.output
        assert result.simulated is True

    def test_generate_sys_ubuntu_release_result(self):
        """Test sys ubuntu release lookup mock result."""
        result = MockResultGenerator.generate_sys_result(
            command="curl",
            context="agent_ubuntu_release_lookup",
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert result.output["kind"] == "release_lookup"
        assert "version" in result.output
        assert "codename" in result.output

    def test_generate_sys_web_search_result(self):
        """Test sys web search mock result."""
        result = MockResultGenerator.generate_sys_result(
            command="curl",
            args=["https://example.com"],
            context="agent_web_lookup",
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert result.output["kind"] == "web_lookup"
        assert "results" in result.output
        assert len(result.output["results"]) > 0
        for item in result.output["results"]:
            assert "title" in item
            assert "url" in item
            assert "snippet" in item

    def test_generate_sys_generic_command_result(self):
        """Test generic sys command mock result."""
        result = MockResultGenerator.generate_sys_result(
            command="ls",
            args=["-la"],
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert "[SIMULATED]" in result.output
        assert result.metadata["command"] == "ls"
        assert result.metadata["args"] == ["-la"]

    def test_generate_compute_turbine_power_result(self):
        """Test compute turbine power model mock result."""
        inputs = {
            "shaft_speed_rpm": 1500.0,
            "torque_nm": 200.0,
        }
        result = MockResultGenerator.generate_compute_result(
            model="turbine_power",
            inputs=inputs,
        )
        assert result.tool_name == "compute.run"
        assert result.status == "success"
        assert result.output["model"] == "turbine_power"
        assert result.output["inputs"] == inputs
        assert "results" in result.output
        assert "power_kw" in result.output["results"]
        assert result.output["results"]["power_kw"] > 0

    def test_generate_compute_realistic_physics(self):
        """Test that compute mock results are physically realistic."""
        # Using physics formula: Power = Torque * Angular_Velocity
        # Angular velocity (rad/s) = RPM * 2π / 60
        shaft_speed = 3000.0
        torque = 500.0
        
        result = MockResultGenerator.generate_compute_result(
            model="turbine_power",
            inputs={"shaft_speed_rpm": shaft_speed, "torque_nm": torque},
        )
        
        # Calculate expected power
        angular_velocity = shaft_speed * 2 * 3.14159 / 60
        expected_power_kw = (angular_velocity * torque) / 1000
        
        actual_power = result.output["results"]["power_kw"]
        
        # Should be very close to calculated value
        assert abs(actual_power - expected_power_kw) < 1.0  # Within 1 kW

    def test_generate_compute_generic_model_result(self):
        """Test generic compute model mock result."""
        result = MockResultGenerator.generate_compute_result(
            model="my_custom_model",
            inputs={"param1": 1.0, "param2": 2.0},
        )
        assert result.tool_name == "compute.run"
        assert result.status == "success"
        assert result.output["model"] == "my_custom_model"
        assert "simulated_output" in result.output["results"]

    def test_generate_file_list_result(self):
        """Test file listing mock result."""
        result = MockResultGenerator.generate_file_result(
            tool_name="list_files",
            path="/home/liara/workspace",
        )
        assert result.tool_name == "list_files"
        assert result.status == "success"
        assert result.output["path"] == "/home/liara/workspace"
        assert "entries" in result.output
        assert result.output["total"] > 0
        for entry in result.output["entries"]:
            assert "name" in entry
            assert "type" in entry

    def test_generate_file_read_result(self):
        """Test file read mock result."""
        result = MockResultGenerator.generate_file_result(
            tool_name="read_file",
            path="/home/liara/workspace/README.md",
        )
        assert result.tool_name == "read_file"
        assert result.status == "success"
        assert "content" in result.output
        assert "[SIMULATED FILE CONTENT]" in result.output["content"]
        assert result.output["encoding"] == "utf-8"

    def test_generate_web_search_result(self):
        """Test web search mock result."""
        result = MockResultGenerator.generate_web_search_result(
            query="Python asyncio tutorial",
        )
        assert result.tool_name == "web_search"
        assert result.status == "success"
        assert result.output["query"] == "Python asyncio tutorial"
        assert "results" in result.output
        assert result.output["total_results"] > 0
        for item in result.output["results"]:
            assert "title" in item
            assert "url" in item
            assert "snippet" in item
            assert "relevance" in item

    def test_generate_error_result_for_unsupported_tool(self):
        """Test error result for unsupported tool."""
        result = MockResultGenerator.generate_error_result(
            tool_name="unknown_tool",
            error_code="NOT_SUPPORTED",
            message="Tool not supported",
        )
        assert result.tool_name == "unknown_tool"
        assert result.status == "simulated_error"
        assert result.error is not None
        assert "NOT_SUPPORTED" in result.error
        assert result.output is None

    def test_unified_generate_interface_sys(self):
        """Test unified generate() interface for sys."""
        result = MockResultGenerator.generate(
            tool_name="sys",
            parameters={"command": "date", "context": "agent_time_lookup"},
        )
        assert result.tool_name == "sys"
        assert result.status == "success"
        assert result.simulated is True

    def test_unified_generate_interface_compute(self):
        """Test unified generate() interface for compute."""
        result = MockResultGenerator.generate(
            tool_name="compute.run",
            parameters={
                "model": "turbine_power",
                "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200},
            },
        )
        assert result.tool_name == "compute.run"
        assert result.status == "success"
        assert result.simulated is True

    def test_generate_compute_generate_result(self):
        """Test dedicated compute.generate mock profile."""
        result = MockResultGenerator.generate_compute_generate_result(
            model_name="demo_sim",
            description="demo generation",
            inputs={"x": "float"},
            outputs={"y": "float"},
            llm_provider="ll_ol_fallback",
        )
        assert result.tool_name == "compute.generate"
        assert result.status == "success"
        assert result.simulated is True
        assert result.output["status"] == "success"
        assert result.output["model_name"] == "demo_sim"
        assert result.output["metadata"]["simulation_type"] == "compute_generate"

    def test_unified_generate_interface_compute_generate(self):
        """Test unified generate() interface for compute.generate."""
        result = MockResultGenerator.generate(
            tool_name="compute.generate",
            parameters={
                "model_name": "demo_sim",
                "description": "demo",
                "inputs": {"x": "float"},
                "outputs": {"y": "float"},
            },
        )
        assert result.tool_name == "compute.generate"
        assert result.status == "success"
        assert result.simulated is True
        assert result.output["status"] == "success"
        assert result.output["metadata"]["simulated"] is True

    def test_unified_generate_interface_file(self):
        """Test unified generate() interface for file operations."""
        result = MockResultGenerator.generate(
            tool_name="list_files",
            parameters={"path": "/home/liara/workspace"},
        )
        assert result.tool_name == "list_files"
        assert result.status == "success"
        assert result.simulated is True

    def test_unified_generate_interface_web_search(self):
        """Test unified generate() interface for web search."""
        result = MockResultGenerator.generate(
            tool_name="web_search",
            parameters={"query": "machine learning"},
        )
        assert result.tool_name == "web_search"
        assert result.status == "success"
        assert result.simulated is True

    def test_unified_generate_interface_unknown_tool(self):
        """Test unified generate() interface for unknown tool."""
        result = MockResultGenerator.generate(
            tool_name="unknown_tool",
            parameters={},
        )
        assert result.tool_name == "unknown_tool"
        assert result.status == "simulated_error"
        assert result.simulated is True

    def test_mock_results_have_metadata(self):
        """Test that mock results include simulation metadata."""
        tools_to_test = [
            ("sys", {"command": "ls"}),
            ("compute.run", {"model": "turbine_power", "inputs": {"shaft_speed_rpm": 1500, "torque_nm": 200}}),
            ("list_files", {"path": "/home/liara"}),
            ("web_search", {"query": "test"}),
        ]

        for tool_name, params in tools_to_test:
            result = MockResultGenerator.generate(tool_name=tool_name, parameters=params)
            assert result.metadata is not None
            assert "simulated" in result.metadata
            assert result.metadata["simulated"] is True
            assert "latency_ms" in result.metadata
            print(f"[✓] {tool_name} includes simulation metadata")

    def test_mock_results_latency_realistic(self):
        """Test that mock results have realistic latency values."""
        result = MockResultGenerator.generate(
            tool_name="sys",
            parameters={"command": "date"},
        )
        latency = result.metadata.get("latency_ms", 0)
        # Mock latency should be between 0 and 100ms (realistic)
        assert 0 <= latency <= 100

    def test_mock_compute_input_validation(self):
        """Test compute mock generation with various input types."""
        # Integer inputs
        result1 = MockResultGenerator.generate_compute_result(
            model="turbine_power",
            inputs={"shaft_speed_rpm": 1500, "torque_nm": 200},
        )
        assert result1.status == "success"

        # Float inputs
        result2 = MockResultGenerator.generate_compute_result(
            model="turbine_power",
            inputs={"shaft_speed_rpm": 1500.5, "torque_nm": 200.3},
        )
        assert result2.status == "success"

        # Missing inputs
        result3 = MockResultGenerator.generate_compute_result(
            model="turbine_power",
            inputs={},
        )
        assert result3.status == "success"  # Still succeeds with defaults
