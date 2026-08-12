from typing import Any, Dict, List, Optional


def _contains_any_text(text: str, snippets: List[str]) -> bool:
    lowered = str(text or "").strip().lower()
    return any(str(snippet).strip().lower() in lowered for snippet in snippets if str(snippet).strip())


def _has_unqualified_backend_health_assumption(response_text: str) -> bool:
    assumptions = [
        "alle backends gesund",
        "all backends are healthy",
        "all backends healthy",
        "backends sind gesund",
    ]
    negations = [
        "nicht annehmen",
        "kann ich nicht annehmen",
        "cannot assume",
        "can't assume",
        "ohne health-check",
        "ohne health checks",
        "ohne healthchecks",
        "nicht sicher",
        "unsicher",
    ]
    return _contains_any_text(response_text, assumptions) and not _contains_any_text(response_text, negations)


def build_hybrid_control_metadata(
    metrics: Dict[str, Any],
    score_feedback: Dict[str, Any],
    judge_post: Optional[Dict[str, Any]] = None,
    query: str = "",
    response: str = "",
    validation_decision: str = "accept",
) -> Dict[str, Any]:
    hard_flag = bool(metrics.get("should_hard_block", False))
    soft_flag = bool(metrics.get("should_soft_limit", False))
    mode_floor = str(score_feedback.get("mode_floor") or "advisory")
    judge_post = dict(judge_post or {})
    judge_post_decision = str(judge_post.get("decision") or "").lower()
    utility = float(metrics.get("utility", 0.0) or 0.0)
    query_text = str(query or "")
    response_text = str(response or "")
    validation_decision_norm = str(validation_decision or "accept").lower()

    asks_ignore_sources_or_fabricate = _contains_any_text(
        query_text,
        [
            "ignoriere fehlende quellen",
            "behaupte einfach",
            "ohne quellen",
            "without sources",
            "just claim",
        ],
    )
    asks_uncertain_plausibility = _contains_any_text(
        query_text,
        [
            "auch wenn sie faktisch nicht sicher",
            "auch wenn du dir nicht sicher bist",
            "plausible antwort",
            "even if you are not sure",
            "even if factually uncertain",
        ],
    )
    asks_absolute_comparison = _contains_any_text(
        query_text,
        [
            "immer schneller als redis",
            "always faster than redis",
        ],
    )
    asks_backend_health_assumption = _contains_any_text(
        query_text,
        [
            "alle backends gesund",
            "all backends are healthy",
            "all backends healthy",
        ],
    )
    response_shows_uncertainty = _contains_any_text(
        response_text,
        [
            "nicht sicher",
            "unsicher",
            "kann ich nicht",
            "keine evidenz",
            "cannot",
            "not sure",
            "uncertain",
        ],
    )
    response_is_qualified = _contains_any_text(
        response_text,
        [
            "kommt darauf",
            "hängt",
            "haengt",
            "nicht immer",
            "nicht pauschal",
            "depends",
            "not necessarily",
            "cannot be generalized",
        ],
    )
    response_assumes_backend_health = _has_unqualified_backend_health_assumption(response_text)
    score_rule_h6_critical = bool(score_feedback.get("score_rule_h6_critical", False))
    repair_preferred = bool(score_feedback.get("repair_preferred", False))
    decision_snapshot = metrics.get("decision_snapshot") if isinstance(metrics.get("decision_snapshot"), dict) else {}
    decision_recommended_action = str(decision_snapshot.get("decision_recommended_action") or "")
    utility_cost_pressure = utility < 0 and decision_recommended_action == "reduce_exploration"

    order = {"advisory": 0, "soft": 1, "hard": 2}
    computed_mode = "hard" if hard_flag else ("soft" if soft_flag else "advisory")
    if order.get(mode_floor, 0) > order.get(computed_mode, 0):
        control_mode = mode_floor
    else:
        control_mode = computed_mode

    trigger_reasons: List[str] = []
    if hard_flag:
        trigger_reasons.append("actionable_risk_hard")
    elif soft_flag:
        trigger_reasons.append("actionable_risk_soft")

    if mode_floor == "soft":
        trigger_reasons.append("feedback_soft_floor")
    elif mode_floor == "hard":
        trigger_reasons.append("feedback_hard_floor")
    if score_rule_h6_critical:
        trigger_reasons.append("score_fach_critical")
    if repair_preferred:
        trigger_reasons.append("repair_preferred_feedback")
    if bool(score_feedback.get("trend_escalation_applied", False)):
        trigger_reasons.append("repeated_weak_scores")
    if judge_post_decision in {"warn", "revise", "block"}:
        trigger_reasons.append(f"judge_post_{judge_post_decision}")
    if utility_cost_pressure:
        trigger_reasons.append("utility_negative")

    if not trigger_reasons:
        trigger_reasons.append("baseline_advisory")

    actions: List[str] = []
    if control_mode == "advisory":
        actions.extend(["log_audit", "show_debug_flag"])
    elif control_mode == "soft":
        actions.extend(["reduce_exploration", "prefer_safe_tools", "tighten_prompt"])
    else:
        actions.extend(["block_unsafe_tools", "fallback_safe_response", "require_judge"])

    if repair_preferred:
        actions.extend(["trigger_repair_loop", "request_targeted_fix"])

    if "feedback_soft_floor" in trigger_reasons or "feedback_hard_floor" in trigger_reasons:
        actions.append("increase_validation_strictness")
    if score_rule_h6_critical:
        actions.extend(["require_judge_review", "trigger_repair_loop"])

    if "actionable_risk_soft" in trigger_reasons:
        actions.append("reduce_context_window")
    if "actionable_risk_hard" in trigger_reasons:
        actions.append("stop_agent_mode")
    if bool(score_feedback.get("trend_escalation_applied", False)):
        actions.extend(["escalate_session_watch", "prefer_conservative_answering"])
    if judge_post_decision == "warn":
        actions.append("require_judge_review")
    elif judge_post_decision == "revise":
        actions.extend(["require_judge_review", "trigger_repair_loop"])
    elif judge_post_decision == "block":
        actions.extend(["require_judge_review", "fallback_safe_response"])

    deduped_actions = list(dict.fromkeys(actions))
    law_candidates: List[Dict[str, Any]] = [
        {
            "id": "policy_block",
            "applies": judge_post_decision == "block",
            "priority": 100,
            "weight": 1.0,
            "resolution_basis": "policy",
            "mode": "hard",
            "action": "fallback_safe_response",
        },
        {
            "id": "policy_warn_or_revise",
            "applies": judge_post_decision in {"warn", "revise"},
            "priority": 90,
            "weight": 0.9,
            "resolution_basis": "policy",
            "mode": "soft",
            "action": "require_judge_review",
        },
        {
            "id": "actionable_risk_hard",
            "applies": hard_flag,
            "priority": 80,
            "weight": 0.85,
            "resolution_basis": "hard_risk",
            "mode": "hard",
            "action": "stop_agent_mode",
        },
        {
            "id": "utility_negative",
            "applies": utility_cost_pressure,
            "priority": 70,
            "weight": 0.7,
            "resolution_basis": "utility",
            "mode": "soft",
            "action": "reduce_exploration",
        },
        {
            "id": "score_fach_critical",
            "applies": score_rule_h6_critical,
            "priority": 60,
            "weight": 0.65,
            "resolution_basis": "feedback",
            "mode": "soft",
            "action": "require_judge_review",
        },
        {
            "id": "feedback_floor_or_repair",
            "applies": repair_preferred or mode_floor in {"soft", "hard"},
            "priority": 50,
            "weight": 0.6,
            "resolution_basis": "feedback",
            "mode": "hard" if mode_floor == "hard" else "soft",
            "action": "trigger_repair_loop" if repair_preferred else "increase_validation_strictness",
        },
        {
            "id": "decision_snapshot",
            "applies": bool(decision_snapshot),
            "priority": 40,
            "weight": 0.5,
            "resolution_basis": str(decision_snapshot.get("decision_resolution_basis") or "multi_objective"),
            "mode": str(decision_snapshot.get("decision_recommended_mode") or control_mode),
            "action": str(decision_snapshot.get("decision_recommended_action") or ""),
        },
        {
            "id": "truth_first",
            "applies": bool(asks_ignore_sources_or_fabricate or asks_uncertain_plausibility),
            "priority": 68,
            "weight": 0.68,
            "resolution_basis": "truth",
            "mode": "soft",
            "action": "require_judge_review",
        },
        {
            "id": "evidence_required",
            "applies": bool(asks_ignore_sources_or_fabricate or asks_absolute_comparison or asks_backend_health_assumption),
            "priority": 66,
            "weight": 0.66,
            "resolution_basis": "evidence",
            "mode": "soft",
            "action": "require_judge_review",
        },
        {
            "id": "uncertainty_honesty",
            "applies": bool(asks_uncertain_plausibility or response_shows_uncertainty or validation_decision_norm in {"warn", "revise", "block"}),
            "priority": 64,
            "weight": 0.64,
            "resolution_basis": "uncertainty",
            "mode": "soft",
            "action": "prefer_conservative_answering",
        },
        {
            "id": "tool_control",
            "applies": bool(asks_backend_health_assumption or response_assumes_backend_health),
            "priority": 62,
            "weight": 0.62,
            "resolution_basis": "tool_policy",
            "mode": "soft",
            "action": "require_judge_review",
        },
    ]
    applicable_laws = [law for law in law_candidates if bool(law.get("applies", False))]
    applicable_laws.sort(key=lambda law: int(law.get("priority", 0)), reverse=True)

    winner_law = applicable_laws[0] if applicable_laws else {
        "id": "baseline_control",
        "priority": 0,
        "weight": 0.4,
        "resolution_basis": "baseline",
        "mode": control_mode,
        "action": deduped_actions[0] if deduped_actions else None,
    }
    triggered_laws = [str(law.get("id") or "") for law in applicable_laws if str(law.get("id") or "")]
    if not triggered_laws:
        triggered_laws = ["baseline_control"]

    resolution_basis = str(winner_law.get("resolution_basis") or "baseline")
    resolved_mode = str(winner_law.get("mode") or control_mode)
    winner_action = winner_law.get("action")
    resolved_action = str(winner_action) if winner_action else (deduped_actions[0] if deduped_actions else None)

    conflict_resolution = {
        "had_conflict": len(applicable_laws) > 1,
        "winning_law": str(winner_law.get("id") or "baseline_control"),
        "winning_priority": int(winner_law.get("priority", 0) or 0),
        "winning_weight": float(winner_law.get("weight", 0.0) or 0.0),
        "overridden_laws": [
            str(law.get("id") or "")
            for law in applicable_laws[1:]
            if str(law.get("id") or "")
        ],
        "strategy": "priority_then_weight",
    }

    if resolved_action and resolved_action not in deduped_actions:
        deduped_actions.append(resolved_action)

    return {
        "control_mode": control_mode,
        "trigger_reasons": trigger_reasons,
        "triggered_laws": triggered_laws,
        "mode_floor": mode_floor,
        "repair_preferred": repair_preferred,
        "actions": deduped_actions,
        "resolution_basis": resolution_basis,
        "resolved_mode": resolved_mode,
        "resolved_action": resolved_action,
        "conflict_resolution": conflict_resolution,
    }


def build_decision_context(
    *,
    validation: Any,
    score_payload: Optional[Dict[str, Any]],
    math_signals: Dict[str, Any],
    judge_post: Dict[str, Any],
) -> Dict[str, Any]:
    validation_decision = str(getattr(validation, "decision", "accept") or "accept")
    judge_decision = str(judge_post.get("decision") or "allow")
    return {
        "validation": {
            "decision": validation_decision,
            "passed": bool(getattr(validation, "passed", False)),
            "confidence_score": float(getattr(validation, "confidence_score", 0.0) or 0.0),
            "risk_flags": list(getattr(validation, "risk_flags", []) or []),
            "score": dict(score_payload or {}),
        },
        "math": dict(math_signals or {}),
        "judge_post": dict(judge_post or {}),
        "effective": {
            "control_mode": str((math_signals or {}).get("control_mode") or "advisory"),
            "resolution_basis": str((math_signals or {}).get("resolution_basis") or "baseline"),
            "resolved_mode": str((math_signals or {}).get("resolved_mode") or (math_signals or {}).get("control_mode") or "advisory"),
            "resolved_action": (math_signals or {}).get("resolved_action"),
            "control_mode_before": str((math_signals or {}).get("control_mode_before") or "advisory"),
            "control_mode_after": str((math_signals or {}).get("control_mode_after") or (math_signals or {}).get("resolved_mode") or (math_signals or {}).get("control_mode") or "advisory"),
            "decision_delta": dict((math_signals or {}).get("decision_delta") or {}),
            "validation_decision": validation_decision,
            "judge_post_decision": judge_decision,
            "repair_preferred": bool((math_signals or {}).get("repair_preferred", False)),
            "trigger_reasons": list((math_signals or {}).get("trigger_reasons", []) or []),
            "triggered_laws": list((math_signals or {}).get("triggered_laws", []) or []),
            "actions": list((math_signals or {}).get("actions", []) or []),
            "conflict_resolution": dict((math_signals or {}).get("conflict_resolution") or {}),
        },
    }


def build_decision_explanation(
    *,
    validation_decision: str,
    score_payload: Optional[Dict[str, Any]],
    math_signals: Dict[str, Any],
    judge_post: Dict[str, Any],
) -> Dict[str, Any]:
    """Build deterministic explanation metadata for hybrid-control decisions."""
    signals = dict(math_signals or {})
    judge = dict(judge_post or {})

    actionable_risk = float(signals.get("actionable_risk", 0.0) or 0.0)
    soft_max = float(signals.get("soft_max", 5.0) or 5.0)
    hard_max = float(signals.get("hard_max", 8.0) or 8.0)
    utility = float(signals.get("utility", 0.0) or 0.0)
    rds_v2 = float(signals.get("rds_v2", 0.0) or 0.0)
    reasoning_cost = float(signals.get("reasoning_cost", 0.0) or 0.0)
    entropy = float(signals.get("context_entropy", 0.0) or 0.0)
    decision_recommended_action = str(signals.get("decision_recommended_action") or "")
    utility_cost_pressure = utility < 0.0 and decision_recommended_action == "reduce_exploration"

    judge_decision = str(judge.get("decision") or "allow").lower()
    judge_reason_code = str(judge.get("reason_code") or "")
    has_policy_violation = judge_decision == "block" or "policy" in judge_reason_code.lower()

    primary_reason = "normal_operation"
    if has_policy_violation:
        primary_reason = "policy_violation"
    elif actionable_risk > hard_max:
        primary_reason = "actionable_risk_exceeded_hard_limit"
    elif actionable_risk > soft_max:
        primary_reason = "actionable_risk_exceeded_soft_limit"
    elif utility_cost_pressure:
        primary_reason = "utility_negative"

    secondary_reasons: List[str] = []
    if rds_v2 >= 3.5:
        secondary_reasons.append("rds_high")
    if reasoning_cost >= 6.0:
        secondary_reasons.append("cost_high")
    if bool(signals.get("trend_escalation_applied", False)):
        secondary_reasons.append("repeated_weak_scores")
    if entropy >= 0.7:
        secondary_reasons.append("context_entropy_high")

    supporting_metrics: Dict[str, Any] = {}
    if primary_reason in {"actionable_risk_exceeded_soft_limit", "actionable_risk_exceeded_hard_limit"}:
        supporting_metrics["actionable_risk"] = round(actionable_risk, 6)
        supporting_metrics["soft_max"] = round(soft_max, 6)
        supporting_metrics["hard_max"] = round(hard_max, 6)
    if primary_reason == "utility_negative" or utility < 0.0:
        supporting_metrics["utility"] = round(utility, 6)
    if utility < 0.0 and decision_recommended_action:
        supporting_metrics["decision_recommended_action"] = decision_recommended_action
    if "rds_high" in secondary_reasons:
        supporting_metrics["rds_v2"] = round(rds_v2, 6)
    if "cost_high" in secondary_reasons:
        supporting_metrics["reasoning_cost"] = round(reasoning_cost, 6)
    if "context_entropy_high" in secondary_reasons:
        supporting_metrics["context_entropy"] = round(entropy, 6)
    if len(supporting_metrics) > 5:
        supporting_metrics = dict(list(supporting_metrics.items())[:5])

    confidence = 0.52
    if primary_reason == "policy_violation":
        confidence = 0.95
    elif primary_reason == "actionable_risk_exceeded_hard_limit":
        confidence = 0.90 + min(0.09, max(0.0, actionable_risk - hard_max) / max(1.0, hard_max))
    elif primary_reason == "actionable_risk_exceeded_soft_limit":
        confidence = 0.82 + min(0.10, max(0.0, actionable_risk - soft_max) / max(1.0, soft_max))
    elif primary_reason == "utility_negative":
        confidence = 0.70 + min(0.15, abs(utility) / 5.0)
    elif secondary_reasons:
        confidence = 0.60

    confidence += min(0.06, 0.02 * len(secondary_reasons))
    decision_confidence = round(max(0.0, min(0.99, confidence)), 6)

    decision_trace = [
        "check_policy",
        "check_risk",
        "check_utility",
        "check_feedback",
    ]
    if primary_reason == "policy_violation":
        decision_trace.append("apply_hard_control_policy")
    elif primary_reason == "actionable_risk_exceeded_hard_limit":
        decision_trace.append("apply_hard_control")
    elif primary_reason in {"actionable_risk_exceeded_soft_limit", "utility_negative"}:
        decision_trace.append("apply_soft_control")
    else:
        decision_trace.append("apply_advisory_control")

    return {
        "primary_reason": primary_reason,
        "secondary_reasons": secondary_reasons,
        "supporting_metrics": supporting_metrics,
        "thresholds": {
            "soft_max": round(soft_max, 6),
            "hard_max": round(hard_max, 6),
        },
        "decision_confidence": decision_confidence,
        "decision_trace": decision_trace,
        "decision_path": list(decision_trace),
        "validation_decision": str(validation_decision or "accept"),
    }
