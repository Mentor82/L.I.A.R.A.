"""Shared exception hierarchy for LIARA services."""


class LiaraError(Exception):
    """Base exception for all LIARA errors."""


class ContractError(LiaraError):
    """Raised when API contract is violated."""


class ValidationError(LiaraError):
    """Raised when output validation fails."""


class ToolExecutionError(LiaraError):
    """Raised when tool execution fails."""


class InferenceError(LiaraError):
    """Raised when LLM inference fails."""


class MemoryError(LiaraError):
    """Raised when memory layer operation fails."""


class StateError(LiaraError):
    """Raised when state management operation fails."""
