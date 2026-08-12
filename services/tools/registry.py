"""Tool registry and discovery."""

from __future__ import annotations

from typing import Any

from .base import Tool
from .builtin.wsl_executor import WslExecutorTool
from .builtin.orientation import OrientationTool
from .builtin.simulation import ComputeTool
from .builtin.compute_generate import ComputeGenerateTool
from .builtin.plot_chart import PlotChartTool
from .builtin.wsl_session import WslSessionTool


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self):
        self._tools: dict[str, type[Tool]] = {}

    def register(self, tool_class: type[Tool]) -> None:
        """Register a tool class."""
        instance = tool_class()
        self._tools[instance.name] = tool_class

    def get_tool(self, name: str) -> type[Tool]:
        """Retrieve tool by name."""
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_tools(self) -> list[str]:
        """List available tool names."""
        return list(self._tools.keys())

    def get_metadata(self, name: str) -> dict[str, Any]:
        """Get tool metadata (description, params, etc)."""
        tool_class = self.get_tool(name)
        instance = tool_class()
        return {
            "name": instance.name,
            "description": instance.description,
            "required_parameters": instance.required_parameters,
            "optional_parameters": instance.optional_parameters,
        }


_global_registry = ToolRegistry()
_global_registry.register(WslExecutorTool)
_global_registry.register(OrientationTool)
_global_registry.register(ComputeTool)
_global_registry.register(ComputeGenerateTool)
_global_registry.register(PlotChartTool)
_global_registry.register(WslSessionTool)


def get_tool_registry() -> ToolRegistry:
    """Get global tool registry."""
    return _global_registry
