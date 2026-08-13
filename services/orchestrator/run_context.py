"""
RunContext: Async task-isolated request context for Orchestrator runs.
Uses contextvars.ContextVar to guarantee zero cross-session contamination under concurrent execution.
"""

from __future__ import annotations
from dataclasses import dataclass
from contextvars import ContextVar
from typing import Optional, Any


@dataclass(frozen=True)
class RunContext:
    session_id: str
    user_id: str
    run_id: str
    request_source: str = ""
    sandbox_root: str = ""
    simulation_mode: Optional[str] = None
    input_profile: Optional[Any] = None


_current_run_context: ContextVar[Optional[RunContext]] = ContextVar("current_run_context", default=None)


def set_current_run_context(ctx: Optional[RunContext]) -> None:
    """Set the current async task's isolated RunContext."""
    _current_run_context.set(ctx)


def get_current_run_context() -> Optional[RunContext]:
    """Retrieve the current async task's isolated RunContext."""
    return _current_run_context.get()
