"""Shared type definitions used across LIARA."""

from enum import Enum


class RunState(str, Enum):
    """Lifecycle states for a single LLM conversation run."""

    PENDING = "pending"
    TOOL_SELECTION = "tool_selection"
    TOOL_EXECUTION = "tool_execution"
    LLM_GENERATION = "llm_generation"
    VALIDATION = "validation"
    COMPLETE = "complete"
    FAILED = "failed"


class ProviderType(str, Enum):
    """Supported LLM providers."""

    OLLAMA = "ollama"
    OPENVINO = "openvino"
    HYBRID = "hybrid"


class MemoryTier(str, Enum):
    """Memory storage tier - determines TTL and access pattern."""

    SESSION = "session"
    PERSISTENT = "persistent"
    RETRIEVAL = "retrieval"
    PATTERN = "pattern"


class ToolStatus(str, Enum):
    """Status of tool execution within a run."""

    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


SessionId = str
RunId = str
UserId = str
ToolName = str
