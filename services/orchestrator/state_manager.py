"""
State machine for run lifecycle management.

Tracks: pending -> tool_selection -> tool_execution -> llm_generation -> validation -> complete/failed
"""

from datetime import UTC, datetime
from typing import Any, Dict, List

from services.shared.exceptions import StateError
from services.shared.types import RunId, RunState, ToolStatus


class RunStateManager:
    """Manages state transitions for a single LLM run."""

    def __init__(self, run_id: RunId):
        self.run_id = run_id
        self.current_state = RunState.PENDING
        self.created_at = datetime.now(UTC)
        self.state_transitions: List[Dict[str, Any]] = []
        self.tool_states: Dict[str, ToolStatus] = {}
        self.metadata: Dict[str, Any] = {}

    def transition_to(self, new_state: RunState, reason: str = "", metadata: Dict[str, Any] = None) -> None:
        """Transition to new state with validation."""
        valid_transitions = self._get_valid_transitions(self.current_state)

        if new_state not in valid_transitions:
            raise StateError(
                f"Invalid transition: {self.current_state.value} -> {new_state.value}. "
                f"Valid transitions: {[s.value for s in valid_transitions]}"
            )

        self.state_transitions.append(
            {
                "from": self.current_state.value,
                "to": new_state.value,
                "timestamp": datetime.now(UTC).isoformat(),
                "reason": reason,
                "metadata": metadata or {},
            }
        )
        self.current_state = new_state

    def mark_tool_status(self, tool_name: str, status: ToolStatus) -> None:
        """Update status of a tool execution."""
        self.tool_states[tool_name] = status

    def get_execution_summary(self) -> Dict[str, Any]:
        """Get summary of run execution."""
        return {
            "run_id": self.run_id,
            "start_time": self.created_at.isoformat(),
            "current_state": self.current_state.value,
            "state_trace": self.state_transitions,
            "tools_executed": self.tool_states,
            "metadata": self.metadata,
        }

    @staticmethod
    def _get_valid_transitions(current: RunState) -> List[RunState]:
        """Define valid state machine transitions."""
        transitions = {
            RunState.PENDING: [RunState.TOOL_SELECTION, RunState.FAILED],
            RunState.TOOL_SELECTION: [RunState.TOOL_EXECUTION, RunState.LLM_GENERATION, RunState.FAILED],
            RunState.TOOL_EXECUTION: [RunState.LLM_GENERATION, RunState.FAILED],
            RunState.LLM_GENERATION: [RunState.VALIDATION, RunState.FAILED],
            RunState.VALIDATION: [RunState.LLM_GENERATION, RunState.COMPLETE, RunState.FAILED],
            RunState.COMPLETE: [],
            RunState.FAILED: [],
        }
        return transitions.get(current, [])
