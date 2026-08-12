from typing import Any, Dict


def read_control_mode_before(session_control_state: Dict[str, Any], session_id: str) -> str:
    state = session_control_state.get(session_id)
    if isinstance(state, dict):
        return str(state.get("control_mode") or "advisory")
    return "advisory"


def build_decision_delta(*, control_before: str, control_after: str) -> Dict[str, Any]:
    order = {"advisory": 0, "soft": 1, "hard": 2}
    before = str(control_before or "advisory")
    after = str(control_after or "advisory")
    direction = "unchanged"
    if order.get(after, 0) > order.get(before, 0):
        direction = "escalated"
    elif order.get(after, 0) < order.get(before, 0):
        direction = "deescalated"

    return {
        "from": before,
        "to": after,
        "changed": before != after,
        "direction": direction,
    }


def build_retry_control(
    *,
    validation_decision: str,
    judge_post: Dict[str, Any],
    retry_count: int,
    retry_limit: int,
    compression_meta: Dict[str, Any],
    gap_decision: Dict[str, Any] = None,
    math_signals: Dict[str, Any] = None,
    gap_stop_value: str = "STOP",
) -> Dict[str, Any]:
    decision = str(validation_decision or "accept").lower()
    judge_decision = str((judge_post or {}).get("decision") or "allow").lower()
    compression_meta = dict(compression_meta or {})
    gap_decision = dict(gap_decision or {})
    math_signals = dict(math_signals or {})

    utility_ig = float(math_signals.get("utility_ig", 0.0) or 0.0)
    stability_score = float(math_signals.get("stability_score", 1.0) or 1.0)
    stability_is_stable = bool(math_signals.get("stability_is_stable", True))
    decision_action = str(math_signals.get("decision_recommended_action") or "")

    strategy = "stop"
    attempt_allowed = False
    stop_reason = "accepted_no_retry"

    if judge_decision == "block":
        stop_reason = "judge_post_block"
    elif compression_meta.get("no_new_information", False):
        stop_reason = "compression_no_new_information"
    elif compression_meta.get("meaningful_reduction") is False:
        stop_reason = "compression_no_meaningful_reduction"
    elif retry_count >= retry_limit and decision in {"block", "revise"}:
        stop_reason = "retry_limit_reached"
    elif gap_decision and (
        not bool(gap_decision.get("gap_detected", False))
        or str(gap_decision.get("action") or "") == gap_stop_value
    ):
        stop_reason = str(gap_decision.get("trigger") or "gap_detector_stop")
    elif decision in {"block", "revise"} and retry_count > 0 and utility_ig <= 0.0:
        stop_reason = "low_information_gain"
    elif decision in {"block", "revise"} and (not stability_is_stable or stability_score < 0.35):
        strategy = "repair"
        attempt_allowed = retry_count < retry_limit
        stop_reason = "retry_allowed"
    elif decision == "block":
        strategy = "repair"
        attempt_allowed = retry_count < retry_limit
        stop_reason = "retry_allowed"
    elif decision == "revise":
        strategy = "repair" if decision_action == "trigger_repair_loop" else "retry"
        attempt_allowed = retry_count < retry_limit
        stop_reason = "retry_allowed"

    return {
        "strategy": strategy,
        "attempt_allowed": attempt_allowed,
        "stop_reason": stop_reason,
        "validation_decision": decision,
        "judge_post_decision": judge_decision,
        "retry_count": int(retry_count),
        "retry_limit": int(retry_limit),
        "utility_ig": round(utility_ig, 6),
        "stability_score": round(stability_score, 6),
    }
