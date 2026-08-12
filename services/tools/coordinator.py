"""Tool coordinator - executes tools and aggregates results."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from services.contracts import ToolExecutionRequest, ToolExecutionResult
from services.tools.registry import get_tool_registry
from services.simulation.mock_result_generator import MockResultGenerator
from services.tools.governance import classify_sys_governance, sys_governance_block_reason, sys_governance_mode


class ToolCoordinator:
    """Executes tools and manages results."""

    def __init__(self):
        self.registry = get_tool_registry()

    async def execute_tool(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """Execute a single tool with timeout.
        
        If simulation_mode=True, generates a mock result instead of executing the actual tool.
        This enables safe planning, debugging, and testing without side effects.
        """
        t0 = time.perf_counter()
        parameters = dict(request.parameters)

        if request.tool_name == "sys" and not request.simulation_mode:
            block_reason = sys_governance_block_reason(parameters)
            if block_reason:
                classification = classify_sys_governance(parameters)
                return ToolExecutionResult(
                    tool_name=request.tool_name,
                    status="failed",
                    output=None,
                    error=block_reason,
                    execution_ms=(time.perf_counter() - t0) * 1000,
                    metadata={
                        "governance_required": True,
                        "governance_mode": sys_governance_mode(),
                        "governance_classification": classification,
                    },
                )

        parameters.pop("_governance_authorized", None)
        
        # === Safe Simulation Mode: Generate Mock Result ===
        if request.simulation_mode:
            mock_result = MockResultGenerator.generate(
                tool_name=request.tool_name,
                parameters=parameters,
            )
            return ToolExecutionResult(
                tool_name=request.tool_name,
                status=mock_result.status,
                output=mock_result.output,
                error=mock_result.error,
                execution_ms=mock_result.metadata.get("latency_ms", 0) if mock_result.metadata else 0,
                metadata=dict(mock_result.metadata or {}),
            )
        
        # === Normal Execution ===
        try:
            tool_class = self.registry.get_tool(request.tool_name)
            tool_instance = tool_class()

            # Central contract validation for all tools.
            tool_instance._validate_parameters(**parameters)

            raw = await asyncio.wait_for(
                tool_instance.execute(**parameters),
                timeout=request.timeout_seconds,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if isinstance(raw, dict):
                raw_metadata = dict(raw.get("metadata") or {})
                return ToolExecutionResult(
                    tool_name=request.tool_name,
                    status=raw.get("status", "success"),
                    output=raw.get("output", raw),
                    error=raw.get("error"),
                    execution_ms=raw_metadata.get("latency_ms") or elapsed_ms,
                    metadata=raw_metadata,
                )
            return ToolExecutionResult(
                tool_name=request.tool_name,
                status="success",
                output=raw,
                execution_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            return ToolExecutionResult(
                tool_name=request.tool_name,
                status="failed",
                output=None,
                error=f"Tool execution timed out after {request.timeout_seconds}s",
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_name=request.tool_name,
                status="failed",
                output=None,
                error=str(e),
            )

    async def execute_tools_parallel(
        self,
        requests: list[ToolExecutionRequest],
    ) -> dict[str, ToolExecutionResult]:
        """Execute multiple tools in parallel."""
        if not requests:
            return {}

        tasks = [self.execute_tool(req) for req in requests]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        return {result.tool_name: result for result in results}
