from typing import Any, Dict


def merge_transition_metadata(state_mgr: Any, target_state: Any, metadata: Dict[str, Any]) -> None:
    for transition in reversed(state_mgr.state_transitions):
        if transition.get("to") == target_state.value:
            transition["metadata"] = {
                **dict(transition.get("metadata") or {}),
                **metadata,
            }
            return
