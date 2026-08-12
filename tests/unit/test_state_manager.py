"""
Unit tests for StateManager.
"""

import pytest
from services.orchestrator.state_manager import RunStateManager
from services.shared.types import RunState
from services.shared.exceptions import StateError


class TestRunStateManager:
    """Test run state machine."""

    def test_initial_state_is_pending(self):
        """New run should start in PENDING state."""
        manager = RunStateManager("run-123")
        assert manager.current_state == RunState.PENDING

    def test_valid_transition(self):
        """Should allow valid state transitions."""
        manager = RunStateManager("run-123")
        manager.transition_to(RunState.TOOL_SELECTION, reason="User query processed")
        assert manager.current_state == RunState.TOOL_SELECTION

    def test_invalid_transition_raises_error(self):
        """Should reject invalid state transitions."""
        manager = RunStateManager("run-123")
        manager.transition_to(RunState.TOOL_SELECTION)

        with pytest.raises(StateError):
            manager.transition_to(RunState.PENDING)  # Invalid: can't go backward

    def test_traces_transitions(self):
        """Should record all state transitions."""
        manager = RunStateManager("run-123")
        manager.transition_to(RunState.TOOL_SELECTION)
        manager.transition_to(RunState.LLM_GENERATION)

        summary = manager.get_execution_summary()
        assert len(summary["state_trace"]) == 2
        assert summary["state_trace"][0]["to"] == "tool_selection"
        assert summary["state_trace"][1]["to"] == "llm_generation"
