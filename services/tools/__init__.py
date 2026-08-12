"""Tools service package."""

from .base import Tool

__all__ = ["Tool", "ToolCoordinator", "ToolRegistry", "get_tool_registry"]


def __getattr__(name: str):
    if name == "ToolCoordinator":
        from .coordinator import ToolCoordinator

        return ToolCoordinator
    if name == "ToolRegistry":
        from .registry import ToolRegistry

        return ToolRegistry
    if name == "get_tool_registry":
        from .registry import get_tool_registry

        return get_tool_registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
