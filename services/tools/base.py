"""Base class for all tools in LIARA."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from services.shared.exceptions import ToolExecutionError


class Tool(ABC):
    """Abstract base class for all LIARA tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what tool does."""

    @property
    @abstractmethod
    def required_parameters(self) -> list[str]:
        """List of required parameter names."""

    @property
    def optional_parameters(self) -> list[str]:
        """List of optional parameter names (default: empty)."""
        return []

    @abstractmethod
    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute tool with given parameters."""

    def _validate_parameters(self, **kwargs: Any) -> None:
        """Validate provided parameters."""
        provided = set(kwargs.keys())
        required = set(self.required_parameters)
        optional = set(self.optional_parameters)
        allowed = required | optional

        missing = required - provided
        if missing:
            raise ToolExecutionError(f"Missing required parameters: {missing}")

        extra = provided - allowed
        if extra:
            raise ToolExecutionError(f"Unexpected parameters: {extra}")

    @staticmethod
    def success(output: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Helper to create success result."""
        return {
            "status": "success",
            "output": output,
            "metadata": metadata or {},
        }

    @staticmethod
    def failure(error: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Helper to create failure result."""
        return {
            "status": "failed",
            "error": error,
            "metadata": metadata or {},
        }
