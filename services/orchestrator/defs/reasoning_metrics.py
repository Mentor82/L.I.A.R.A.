import time
from math import log2
from typing import Any, Dict, List, Optional

from services.contracts import ReasoningMetricsSnapshot
from services.orchestrator.reasoning_math import calibrate_thresholds_quantile, estimate_context_entropy


def derive_reasoning_metric_inputs(
    inputs: Dict[str, Any] = None,
    *,
    query: str = "",
    response: str = "",
    tools_used: List[str] = None,
    context_debug: Dict[str, Any] = None,
    validation_decision: str = "accept",
    retry_count: int = 0,
    failed_tools: List[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    inp = dict(inputs or {})
    q = query or str(inp.get("query") or "")
    resp = response or str(inp.get("response") or "")
    tools = tools_used if tools_used is not None else inp.get("tools_used", [])
    ctx_dbg = context_debug if context_debug is not None else inp.get("context_debug", {})
    val_dec = validation_decision or str(inp.get("validation_decision") or "accept")
    retries = retry_count if retry_count is not None else int(inp.get("retry_count", 0))
    failed = failed_tools if failed_tools is not None else inp.get("failed_tools", [])

    source_counts = dict((ctx_dbg or {}).get("sources") or {})
    counts = [max(0, int(v)) for v in source_counts.values() if isinstance(v, (int, float))]
    memory_items = int(sum(counts))
    context_entropy = estimate_context_entropy(context_debug or {})

    depth = max(1, int(retry_count) + 1)
    branching_factor_avg = 1.0 + (0.25 if depth > 1 else 0.0)
    tool_calls = len(tools_used or [])
    token_estimate = max(1, int((len(query or "") + len(response or "")) / 4))

    decision_risk = {
        "accept": 0.05,
        "warn": 0.35,
        "revise": 0.65,
        "block": 0.95,
    }
    failed_ratio = (len(failed_tools) / max(1, tool_calls)) if tool_calls else 0.0
    policy_risk = min(1.0, decision_risk.get((validation_decision or "accept").lower(), 0.2) + 0.25 * failed_ratio)

    depth_cost = 1.0 * log2(1 + max(0, depth))
    memory_cost = 1.0 * log2(1 + max(0, memory_items))
    tool_cost = 2.0 * max(0, tool_calls)
    entropy_cost = 1.5 * max(0.0, context_entropy)
    total_cost = depth_cost + memory_cost + tool_cost + entropy_cost

    rds_v2 = log2(1 + max(0.0, depth * branching_factor_avg)) + (0.8 * max(0.0, context_entropy))
    uncertainty_risk = max(0.0, context_entropy)
    complexity_risk = max(0.0, rds_v2)
    total_risk = (0.5 * policy_risk) + (0.2 * uncertainty_risk) + (0.3 * complexity_risk)

    validation_bonus = {
        "accept": 1.0,
        "warn": 0.5,
        "revise": 0.2,
        "block": 0.0,
    }
    goal_progress = validation_bonus.get((validation_decision or "accept").lower(), 0.3)

    return {
        "depth": depth,
        "branching_factor_avg": branching_factor_avg,
        "memory_items": memory_items,
        "tool_calls": tool_calls,
        "token_estimate": token_estimate,
        "context_entropy": round(context_entropy, 6),
        "goal_progress": goal_progress,
        "policy_risk": round(policy_risk, 6),
    }


def build_runtime_audit_report(
    *,
    threshold_profile: Dict[str, Any],
    math_signals: Dict[str, Any],
    session_score_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    history = list(session_score_history or [])
    actionable_risks = [
        float(entry.get("actionable_risk", 0.0) or 0.0)
        for entry in history
        if isinstance(entry, dict) and entry.get("actionable_risk") is not None
    ]

    current_soft = float((threshold_profile or {}).get("soft_risk_max", 5.0) or 5.0)
    current_hard = float((threshold_profile or {}).get("hard_risk_max", 8.0) or 8.0)

    recommendation_status = "insufficient_data"
    recommended_soft = current_soft
    recommended_hard = current_hard
    if len(actionable_risks) >= 3:
        rec_soft, rec_hard = calibrate_thresholds_quantile(
            actionable_risks,
            soft_q=0.90,
            hard_q=0.99,
            min_gap=0.25,
        )
        recommended_soft = round(float(rec_soft), 6)
        recommended_hard = round(float(rec_hard), 6)
        recommendation_status = "recommended"

    version_tag = time.strftime("calib-%Y%m%d-%H%M%S", time.gmtime())
    return {
        "snapshot": {
            "rds_v2": math_signals.get("rds_v2"),
            "actionable_risk": math_signals.get("actionable_risk"),
            "utility_ig": math_signals.get("utility_ig"),
            "stability_score": math_signals.get("stability_score"),
            "decision_pareto_status": math_signals.get("decision_pareto_status"),
            "decision_dominant_objective": math_signals.get("decision_dominant_objective"),
            "compute_backends": {
                "reasoning": math_signals.get("compute_backend"),
                "belief": math_signals.get("belief_compute_backend"),
                "utility": math_signals.get("utility_compute_backend"),
                "structure": math_signals.get("structure_compute_backend"),
                "decision": math_signals.get("decision_compute_backend"),
            },
        },
        "thresholds": {
            "current": {
                "soft_risk_max": round(current_soft, 6),
                "hard_risk_max": round(current_hard, 6),
                "version": (threshold_profile or {}).get("version"),
                "source": (threshold_profile or {}).get("source"),
            },
            "recommended": {
                "soft_risk_max": round(recommended_soft, 6),
                "hard_risk_max": round(recommended_hard, 6),
                "status": recommendation_status,
                "sample_count": len(actionable_risks),
                "version": version_tag,
            },
        },
        "julia_live_verification": {
            "status": "pending_live_chat_stream_check",
            "note": "Use live stream regression script to compare Julia primary vs Python fallback traces.",
        },
    }


def build_validation_math_signals(reasoning_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Expose compact math risk signals for validator/judge-facing metadata."""
    metrics = dict(reasoning_metrics or {})
    score_feedback = metrics.get("score_feedback") if isinstance(metrics.get("score_feedback"), dict) else metrics
    hybrid_control = metrics.get("hybrid_control") if isinstance(metrics.get("hybrid_control"), dict) else metrics
    judge_post = metrics.get("judge_post") if isinstance(metrics.get("judge_post"), dict) else metrics
    threshold_profile = metrics.get("threshold_profile") if isinstance(metrics.get("threshold_profile"), dict) else metrics
    belief_snapshot = metrics.get("belief_snapshot") if isinstance(metrics.get("belief_snapshot"), dict) else metrics
    utility_snapshot = metrics.get("utility_snapshot") if isinstance(metrics.get("utility_snapshot"), dict) else metrics
    structure_snapshot = metrics.get("structure_snapshot") if isinstance(metrics.get("structure_snapshot"), dict) else metrics
    decision_snapshot = metrics.get("decision_snapshot") if isinstance(metrics.get("decision_snapshot"), dict) else metrics
    return {
        "reasoning_cost": metrics.get("total_cost"),
        "utility": metrics.get("utility"),
        "context_entropy": metrics.get("context_entropy"),
        "rds_v2": metrics.get("rds_v2"),
        "risk_total": metrics.get("total_risk"),
        "actionable_risk": metrics.get("actionable_risk"),
        "soft_max": threshold_profile.get("soft_risk_max"),
        "hard_max": threshold_profile.get("hard_risk_max"),
        "should_soft_limit": metrics.get("should_soft_limit"),
        "should_hard_block": metrics.get("should_hard_block"),
        "rds_mode": metrics.get("rds_mode"),
        "mode": metrics.get("mode"),
        "compute_backend": metrics.get("compute_backend"),
        "compute_path": metrics.get("compute_path"),
        "fallback_reason": metrics.get("fallback_reason"),
        "score_feedback_applied": bool(score_feedback.get("applied", False)),
        "score_feedback_delta": score_feedback.get("delta", {}),
        "score_feedback_source_run": score_feedback.get("source_run_id"),
        "weak_score_escalation_count": int(score_feedback.get("weak_score_escalation_count", 0) or 0),
        "score_feedback_canary_soft_only": bool(score_feedback.get("score_feedback_canary_soft_only", False)),
        "trend_weak_score_count": int(score_feedback.get("trend_weak_score_count", 0) or 0),
        "trend_escalation_applied": bool(score_feedback.get("trend_escalation_applied", False)),
        "threshold_version": threshold_profile.get("version"),
        "threshold_source": threshold_profile.get("source"),
        "judge_post_decision": judge_post.get("decision") or metrics.get("judge_post_decision"),
        "judge_post_confidence": judge_post.get("confidence") or metrics.get("judge_post_confidence"),
        "judge_post_reason_code": judge_post.get("reason_code") or metrics.get("judge_post_reason_code"),
        "control_mode": hybrid_control.get("control_mode", "advisory"),
        "resolution_basis": hybrid_control.get("resolution_basis", "baseline"),
        "resolved_mode": hybrid_control.get("resolved_mode", hybrid_control.get("control_mode", "advisory")),
        "resolved_action": hybrid_control.get("resolved_action"),
        "trigger_reasons": list(hybrid_control.get("trigger_reasons", []) or []),
        "triggered_laws": list(
            hybrid_control.get("triggered_laws", hybrid_control.get("trigger_reasons", [])) or []
        ),
        "repair_preferred": bool(hybrid_control.get("repair_preferred", False)),
        "actions": list(hybrid_control.get("actions", []) or []),
        "conflict_resolution": dict(hybrid_control.get("conflict_resolution") or {}),
        "belief_posterior": belief_snapshot.get("belief_posterior"),
        "belief_estimate": belief_snapshot.get("belief_estimate"),
        "belief_residual": belief_snapshot.get("belief_residual"),
        "belief_variance": belief_snapshot.get("belief_variance"),
        "signal_confidence": belief_snapshot.get("signal_confidence"),
        "signal_mean": belief_snapshot.get("signal_mean"),
        "belief_compute_backend": belief_snapshot.get("belief_compute_backend"),
        "utility_ig": utility_snapshot.get("utility_ig"),
        "utility_ig_direction": utility_snapshot.get("utility_ig_direction"),
        "utility_weighted": utility_snapshot.get("utility_weighted"),
        "utility_discounted": utility_snapshot.get("utility_discounted"),
        "utility_discount_weight": utility_snapshot.get("utility_discount_weight"),
        "utility_compute_backend": utility_snapshot.get("utility_compute_backend"),
        "structure_clustering": structure_snapshot.get("structure_clustering"),
        "structure_modularity": structure_snapshot.get("structure_modularity"),
        "structure_shortest_path": structure_snapshot.get("structure_shortest_path"),
        "stability_derivative": structure_snapshot.get("stability_derivative"),
        "stability_is_stable": structure_snapshot.get("stability_is_stable"),
        "stability_score": structure_snapshot.get("stability_score"),
        "regularization_total": structure_snapshot.get("regularization_total"),
        "structure_compute_backend": structure_snapshot.get("structure_compute_backend"),
        "decision_pareto_status": decision_snapshot.get("decision_pareto_status"),
        "decision_dominant_objective": decision_snapshot.get("decision_dominant_objective"),
        "decision_recommended_mode": decision_snapshot.get("decision_recommended_mode"),
        "decision_recommended_action": decision_snapshot.get("decision_recommended_action"),
        "decision_resolution_basis": decision_snapshot.get("decision_resolution_basis"),
        "decision_weak_objectives": list(decision_snapshot.get("decision_weak_objectives", []) or []),
        "decision_compute_backend": decision_snapshot.get("decision_compute_backend"),
    }


def apply_score_feedback_to_metric_inputs(
    inputs: Dict[str, Any],
    previous_score_feedback: Dict[str, Any],
    previous_score_history: Optional[List[Dict[str, Any]]] = None,
    *,
    weak_score_escalation_count: int = 2,
    score_feedback_canary_soft_only: bool = False,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    adjusted = dict(inputs or {})
    feedback = dict(previous_score_feedback or {})
    history = list(previous_score_history or [])
    if not feedback:
        return adjusted, {
            "applied": False,
            "delta": {},
            "source_run_id": None,
            "mode_floor": "advisory",
            "score_rule_h5_repair": False,
            "score_rule_h6_critical": False,
            "trend_weak_score_count": 0,
            "trend_escalation_applied": False,
        }

    decision = str(feedback.get("decision") or "accept").lower()
    previous_confidence = float(feedback.get("confidence_score", 0.0) or 0.0)
    risk_flags = set(str(flag) for flag in (feedback.get("risk_flags") or []))
    score_payload = dict(feedback.get("score") or {})

    def _score_value(payload: Dict[str, Any], *keys: str) -> Optional[float]:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    score_fach = _score_value(score_payload, "score_fach", "fach")
    score_code = _score_value(score_payload, "score_code", "code")
    score_robustheit = _score_value(score_payload, "score_robustheit", "robustheit")

    score_rule_h5_repair = bool(
        score_fach is not None
        and score_fach <= 3.0
        and (
            (score_code is not None and score_code >= 4.0)
            or (score_robustheit is not None and score_robustheit >= 4.0)
        )
    )
    score_rule_h6_critical = bool(score_fach is not None and score_fach >= 5.0)

    repair_preferred = bool(
        0.55 <= previous_confidence <= 0.85
        and bool(risk_flags & {"logic_branch_dead", "crash_without_try_except", "command_response_mismatch"})
    )
    repair_preferred = bool(repair_preferred or score_rule_h5_repair or score_rule_h6_critical)
    trend_weak_score_count = 0
    for entry in history:
        conf_entry = float(entry.get("confidence_score", 0.0) or 0.0)
        flags_entry = set(str(flag) for flag in (entry.get("risk_flags") or []))
        weak_entry = conf_entry <= 0.60 or bool(
            flags_entry & {"formula_mismatch", "policy_safety_violation", "consistency_issue"}
        )
        if weak_entry:
            trend_weak_score_count += 1

    escalation_count = max(1, int(weak_score_escalation_count or 2))
    trend_escalation_applied = trend_weak_score_count >= escalation_count

    policy_delta = 0.0
    entropy_delta = 0.0

    if previous_confidence < 0.45:
        policy_delta += 0.20
    elif previous_confidence < 0.60:
        policy_delta += 0.10
    elif previous_confidence >= 0.90 and not risk_flags:
        policy_delta -= 0.05

    if bool(risk_flags & {"crash_without_try_except", "command_response_mismatch"}):
        entropy_delta += 0.08
    elif bool(risk_flags & {"numeric_input_without_guard", "logic_branch_dead"}):
        entropy_delta += 0.04

    if decision == "block":
        policy_delta += 0.15
    elif decision == "revise":
        policy_delta += 0.08

    if repair_preferred and decision in {"block", "revise"}:
        policy_delta = min(policy_delta, 0.05)

    if trend_escalation_applied:
        policy_delta += 0.10
        entropy_delta += 0.03

    mode_floor = "advisory"
    if previous_confidence < 0.35 and decision in {"block", "revise"}:
        mode_floor = "hard"
    elif previous_confidence < 0.60:
        mode_floor = "soft"
    elif repair_preferred:
        mode_floor = "soft"
    if trend_escalation_applied and mode_floor == "advisory":
        mode_floor = "soft"
    elif trend_escalation_applied and mode_floor == "soft" and previous_confidence < 0.45:
        mode_floor = "hard"

    if (score_rule_h5_repair or score_rule_h6_critical) and mode_floor == "hard":
        mode_floor = "soft"
    elif (score_rule_h5_repair or score_rule_h6_critical) and mode_floor == "advisory":
        mode_floor = "soft"

    if score_feedback_canary_soft_only and mode_floor == "hard":
        mode_floor = "soft"

    original_policy = float(adjusted.get("policy_risk", 0.0) or 0.0)
    original_entropy = float(adjusted.get("context_entropy", 0.0) or 0.0)

    adjusted["policy_risk"] = round(max(0.0, min(1.0, original_policy + policy_delta)), 6)
    adjusted["context_entropy"] = round(max(0.0, min(1.0, original_entropy + entropy_delta)), 6)

    return adjusted, {
        "applied": (policy_delta != 0.0 or entropy_delta != 0.0),
        "delta": {
            "policy_risk": round(policy_delta, 6),
            "context_entropy": round(entropy_delta, 6),
        },
        "source_run_id": feedback.get("run_id"),
        "source_decision": decision,
        "mode_floor": mode_floor,
        "repair_preferred": repair_preferred,
        "score_rule_h5_repair": score_rule_h5_repair,
        "score_rule_h6_critical": score_rule_h6_critical,
        "trend_weak_score_count": trend_weak_score_count,
        "trend_escalation_applied": trend_escalation_applied,
        "weak_score_escalation_count": escalation_count,
        "score_feedback_canary_soft_only": bool(score_feedback_canary_soft_only),
        "source_feedback": {
            "confidence_score": round(previous_confidence, 6),
            "risk_flags": sorted(risk_flags),
            "score": score_payload,
        },
    }


def compute_reasoning_metrics_snapshot_python(
    inputs: Dict[str, Any],
    *,
    soft_risk_max: float = 5.0,
    hard_risk_max: float = 8.0,
    fallback_reason: Optional[str] = None,
) -> Dict[str, Any]:
    depth = int(inputs.get("depth", 1))
    branching_factor_avg = float(inputs.get("branching_factor_avg", 1.0))
    memory_items = int(inputs.get("memory_items", 0))
    tool_calls = int(inputs.get("tool_calls", 0))
    context_entropy = float(inputs.get("context_entropy", 0.0))
    goal_progress = float(inputs.get("goal_progress", 0.0))
    policy_risk = float(inputs.get("policy_risk", 0.0))

    depth_cost = 1.0 * log2(1 + max(0, depth))
    memory_cost = 1.0 * log2(1 + max(0, memory_items))
    tool_cost = 2.0 * max(0, tool_calls)
    entropy_cost = 1.5 * max(0.0, context_entropy)
    total_cost = depth_cost + memory_cost + tool_cost + entropy_cost

    rds_v2 = log2(1 + max(0.0, depth * branching_factor_avg)) + (0.8 * max(0.0, context_entropy))
    uncertainty_risk = max(0.0, context_entropy)
    complexity_risk = max(0.0, rds_v2)
    total_risk = (0.5 * policy_risk) + (0.2 * uncertainty_risk) + (0.3 * complexity_risk)
    actionable_risk = (0.5 * policy_risk) + (0.2 * uncertainty_risk)
    utility = goal_progress - total_cost

    metrics = ReasoningMetricsSnapshot(
        depth=depth,
        branching_factor_avg=branching_factor_avg,
        memory_items=memory_items,
        tool_calls=tool_calls,
        token_estimate=int(inputs.get("token_estimate", 0)),
        context_entropy=round(context_entropy, 6),
        goal_progress=goal_progress,
        policy_risk=round(policy_risk, 6),
        depth_cost=round(depth_cost, 6),
        memory_cost=round(memory_cost, 6),
        tool_cost=round(tool_cost, 6),
        entropy_cost=round(entropy_cost, 6),
        total_cost=round(total_cost, 6),
        reasoning_cost=round(total_cost, 6),
        rds_v2=round(rds_v2, 6),
        uncertainty_risk=round(uncertainty_risk, 6),
        complexity_risk=round(complexity_risk, 6),
        total_risk=round(total_risk, 6),
        risk_total=round(total_risk, 6),
        actionable_risk=round(actionable_risk, 6),
        utility=round(utility, 6),
        should_soft_limit=bool(actionable_risk > soft_risk_max),
        should_hard_block=bool(actionable_risk > hard_risk_max),
        rds_mode="diagnostic",
        compute_backend="python",
        compute_path="fallback" if fallback_reason else "primary",
        fallback_reason=fallback_reason,
    )
    return metrics.model_dump()
