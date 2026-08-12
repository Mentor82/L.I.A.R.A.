"""
Unit tests for ToolCoordinator.
"""

import pytest
from services.tools.coordinator import ToolCoordinator
from services.contracts import ToolExecutionRequest, ToolExecutionResult
from services.tools.base import Tool
from services.tools.registry import get_tool_registry
from services.tools.governance import classify_sys_governance, sys_governance_block_reason


# ---------------------------------------------------------------------------
# Minimal always-succeeding tool used as test fixture
# ---------------------------------------------------------------------------

class _EchoTool(Tool):
    """Lightweight test fixture: returns its input as output."""

    @property
    def name(self) -> str:
        return "_echo"

    @property
    def description(self) -> str:
        return "Test echo tool"

    @property
    def required_parameters(self) -> list[str]:
        return []

    async def execute(self, **kwargs):
        return self.success("echo", metadata={"kwargs": repr(kwargs)})


class _GovernanceSysTool(Tool):
    @property
    def name(self) -> str:
        return "sys"

    @property
    def description(self) -> str:
        return "Isolated SYS governance fixture"

    @property
    def required_parameters(self) -> list[str]:
        return ["command"]

    @property
    def optional_parameters(self) -> list[str]:
        return ["args", "proposal_id", "target_path", "write_mode"]

    async def execute(self, **kwargs):
        return self.success({"parameters": kwargs})


@pytest.fixture(autouse=True)
def register_echo_tool():
    """Register _echo for coordinator tests, remove after."""
    registry = get_tool_registry()
    registry.register(_EchoTool)
    yield
    registry._tools.pop("_echo", None)


@pytest.mark.asyncio
class TestToolCoordinator:
    """Test tool execution coordination."""

    async def test_execute_single_tool_success(self):
        """Execute single tool successfully."""
        coordinator = ToolCoordinator()

        request = ToolExecutionRequest(
            tool_name="_echo",
            parameters={},
            timeout_seconds=5,
        )

        result = await coordinator.execute_tool(request)

        assert result.tool_name == "_echo"
        assert result.status == "success"
        assert result.output is not None
        assert "kwargs" in result.metadata

    async def test_execute_tool_not_found(self):
        """Tool not in registry → error."""
        coordinator = ToolCoordinator()

        request = ToolExecutionRequest(
            tool_name="nonexistent_tool",
            parameters={},
            timeout_seconds=5,
        )

        result = await coordinator.execute_tool(request)

        assert result.status == "failed"
        assert result.error is not None

    async def test_execute_tools_parallel_multiple(self):
        """Execute multiple tools in parallel."""
        coordinator = ToolCoordinator()

        requests = [
            ToolExecutionRequest(
                tool_name="_echo",
                parameters={},
                timeout_seconds=5,
            ),
            ToolExecutionRequest(
                tool_name="_echo",
                parameters={},
                timeout_seconds=5,
            ),
        ]

        results = await coordinator.execute_tools_parallel(requests)

        assert len(results) >= 1
        assert "_echo" in results
        assert results["_echo"].status == "success"

    async def test_execute_tools_parallel_empty_list(self):
        """Empty tool list → empty result."""
        coordinator = ToolCoordinator()

        results = await coordinator.execute_tools_parallel([])

        assert results == {}

    async def test_execute_tool_timeout(self):
        """Tool timeout is caught gracefully."""
        coordinator = ToolCoordinator()

        request = ToolExecutionRequest(
            tool_name="_echo",
            parameters={},
            timeout_seconds=30,
        )

        result = await coordinator.execute_tool(request)
        assert result.tool_name == "_echo"

    async def test_execute_tool_rejects_unexpected_parameters(self):
        """Coordinator enforces shared parameter validation before tool execute."""
        coordinator = ToolCoordinator()

        request = ToolExecutionRequest(
            tool_name="_echo",
            parameters={"not_allowed": 1},
            timeout_seconds=5,
        )

        result = await coordinator.execute_tool(request)
        assert result.status == "failed"
        assert result.error is not None
        assert "Unexpected parameters" in result.error

    async def test_execute_tools_parallel_mixed_success_failure(self):
        """Some tools succeed, some fail → all returned."""
        coordinator = ToolCoordinator()

        requests = [
            ToolExecutionRequest(
                tool_name="_echo",
                parameters={},
                timeout_seconds=5,
            ),
            ToolExecutionRequest(
                tool_name="nonexistent_tool",
                parameters={},
                timeout_seconds=5,
            ),
        ]

        results = await coordinator.execute_tools_parallel(requests)

        assert len(results) >= 1
        # At least one succeeded
        success_count = sum(1 for r in results.values() if r.status == "success")
        assert success_count >= 1


@pytest.mark.asyncio
class TestSysGovernanceCoordinator:
    @staticmethod
    def _coordinator(monkeypatch) -> ToolCoordinator:
        coordinator = ToolCoordinator()
        original_get_tool = coordinator.registry.get_tool
        monkeypatch.setattr(
            coordinator.registry,
            "get_tool",
            lambda name: _GovernanceSysTool if name == "sys" else original_get_tool(name),
        )
        return coordinator

    async def test_risk_based_allows_read_only_sys(self, monkeypatch):
        monkeypatch.setenv("LIARA_SYS_GOVERNANCE_MODE", "risk_based")
        coordinator = self._coordinator(monkeypatch)

        result = await coordinator.execute_tool(
            ToolExecutionRequest(tool_name="sys", parameters={"command": "health"}, timeout_seconds=5)
        )

        assert result.status == "success"
        assert result.output["parameters"]["command"] == "health"

    @pytest.mark.parametrize(
        "parameters,reason",
        [
            ({"command": "tee", "args": ["file.txt"], "target_path": "file.txt"}, "mutation"),
            ({"command": "python", "args": ["-c", "print(1)"]}, "code_execution"),
        ],
    )
    async def test_risk_based_blocks_sensitive_sys_without_authorization(self, monkeypatch, parameters, reason):
        monkeypatch.setenv("LIARA_SYS_GOVERNANCE_MODE", "risk_based")
        coordinator = self._coordinator(monkeypatch)

        result = await coordinator.execute_tool(
            ToolExecutionRequest(tool_name="sys", parameters=parameters, timeout_seconds=5)
        )

        assert result.status == "failed"
        assert result.metadata["governance_required"] is True
        assert reason in result.metadata["governance_classification"]["reasons"]

    async def test_risk_based_allows_policy_validated_read_only_curl(self, monkeypatch):
        monkeypatch.setenv("LIARA_SYS_GOVERNANCE_MODE", "risk_based")
        coordinator = self._coordinator(monkeypatch)
        parameters = {
            "command": "curl",
            "args": ["-s", "-L", "-m", "15", "-A", "Mozilla/5.0", "https://example.com/data.json"],
        }

        result = await coordinator.execute_tool(
            ToolExecutionRequest(tool_name="sys", parameters=parameters, timeout_seconds=5)
        )

        assert result.status == "success"
        assert result.output["parameters"] == parameters

    async def test_risk_based_does_not_turn_blocked_curl_into_approvable_request(self, monkeypatch):
        monkeypatch.setenv("LIARA_SYS_GOVERNANCE_MODE", "risk_based")

        classification = classify_sys_governance(
            {"command": "curl", "args": ["-X", "POST", "https://example.com"]}
        )

        assert classification["policy_allowed"] is False
        assert classification["policy_error_type"] == "blocked_flag"
        assert classification["policy_validated_read"] is False
        assert sys_governance_block_reason(
            {"command": "curl", "args": ["-X", "POST", "https://example.com"]}
        ) is None

    async def test_authorized_sensitive_sys_reaches_tool_without_internal_marker(self, monkeypatch):
        monkeypatch.setenv("LIARA_SYS_GOVERNANCE_MODE", "risk_based")
        coordinator = self._coordinator(monkeypatch)

        result = await coordinator.execute_tool(
            ToolExecutionRequest(
                tool_name="sys",
                parameters={
                    "command": "tee",
                    "args": ["file.txt"],
                    "target_path": "file.txt",
                    "proposal_id": "sys-prop-test",
                    "_governance_authorized": True,
                },
                timeout_seconds=5,
            )
        )

        assert result.status == "success"
        assert result.output["parameters"]["proposal_id"] == "sys-prop-test"
        assert "_governance_authorized" not in result.output["parameters"]

    async def test_legacy_enforce_switch_maps_to_all(self, monkeypatch):
        monkeypatch.delenv("LIARA_SYS_GOVERNANCE_MODE", raising=False)
        monkeypatch.setenv("LIARA_SYS_GOVERNANCE_ENFORCE", "1")
        coordinator = self._coordinator(monkeypatch)

        result = await coordinator.execute_tool(
            ToolExecutionRequest(tool_name="sys", parameters={"command": "health"}, timeout_seconds=5)
        )

        assert result.status == "failed"
        assert result.metadata["governance_mode"] == "all"


@pytest.mark.asyncio
class TestToolCoordinatorContractEdgeCases:
    """Contract tests: malformed/edge-case tool outputs."""

    async def test_tool_raising_exception_returns_failed_status(self, monkeypatch):
        """A tool that raises an unhandled exception is wrapped as failed."""
        coordinator = ToolCoordinator()
        registry = get_tool_registry()

        class BrokenTool(Tool):
            @property
            def name(self):
                return "broken_tool"

            @property
            def description(self):
                return "Always explodes"

            @property
            def required_parameters(self):
                return []

            async def execute(self, **kwargs):
                raise RuntimeError("unexpected internal failure")

        registry.register(BrokenTool)
        try:
            result = await coordinator.execute_tool(
                ToolExecutionRequest(tool_name="broken_tool", parameters={}, timeout_seconds=5)
            )
            assert result.status == "failed"
            assert result.output is None
            assert "unexpected internal failure" in (result.error or "")
        finally:
            registry._tools.pop("broken_tool", None)

    async def test_tool_returning_non_dict_output(self, monkeypatch):
        """A tool returning a non-dict (e.g. plain string) is still delivered as-is."""
        coordinator = ToolCoordinator()
        registry = get_tool_registry()

        class StringOutputTool(Tool):
            @property
            def name(self):
                return "string_out_tool"

            @property
            def description(self):
                return "Returns a plain string"

            @property
            def required_parameters(self):
                return []

            async def execute(self, **kwargs):
                return "just a string"  # not a dict

        registry.register(StringOutputTool)
        try:
            result = await coordinator.execute_tool(
                ToolExecutionRequest(tool_name="string_out_tool", parameters={}, timeout_seconds=5)
            )
            assert result.status == "success"
            assert result.output == "just a string"
        finally:
            registry._tools.pop("string_out_tool", None)

    async def test_tool_returning_none_output(self):
        """A tool returning None is delivered without error."""
        coordinator = ToolCoordinator()
        registry = get_tool_registry()

        class NoneOutputTool(Tool):
            @property
            def name(self):
                return "none_out_tool"

            @property
            def description(self):
                return "Returns None"

            @property
            def required_parameters(self):
                return []

            async def execute(self, **kwargs):
                return None

        registry.register(NoneOutputTool)
        try:
            result = await coordinator.execute_tool(
                ToolExecutionRequest(tool_name="none_out_tool", parameters={}, timeout_seconds=5)
            )
            assert result.status == "success"
            assert result.output is None
        finally:
            registry._tools.pop("none_out_tool", None)

    async def test_execution_result_always_has_tool_name(self):
        """ToolExecutionResult.tool_name is always populated, even on failure."""
        coordinator = ToolCoordinator()
        result = await coordinator.execute_tool(
            ToolExecutionRequest(tool_name="totally_missing", parameters={}, timeout_seconds=5)
        )
        assert result.tool_name == "totally_missing"
        assert result.status == "failed"

    async def test_execution_result_records_latency_on_success(self):
        """execution_ms is a non-negative float for successful tools."""
        coordinator = ToolCoordinator()
        result = await coordinator.execute_tool(
            ToolExecutionRequest(tool_name="_echo", parameters={}, timeout_seconds=5)
        )
        assert result.status == "success"
        assert result.execution_ms is not None
        assert result.execution_ms >= 0
