"""Reasoning Control & Metrics Submodule for LIARA Orchestrator.

Handles:
- Belief, Utility, Stability, and Decision Snapshots (Phases 1-4)
- Reasoning metrics derivation & score feedback loop
- Julia & Python metric execution
- Runtime threshold adaptation & profiles
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from services.contracts import ReasoningMetricsSnapshot
from services.config import Settings
from .reasoning_math import estimate_context_entropy, calibrate_thresholds_quantile
from .defs.reasoning_metrics import (
    build_validation_math_signals,
    build_runtime_audit_report,
    derive_reasoning_metric_inputs,
    apply_score_feedback_to_metric_inputs,
    compute_reasoning_metrics_snapshot_python,
)
from .defs.decision_control import build_decision_delta, read_control_mode_before, build_retry_control
from .defs.decision_context import build_decision_context, build_decision_explanation, build_hybrid_control_metadata

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

_LOGGER = logging.getLogger("liara.orchestrator.reasoning_control")


def resolve_reasoning_threshold_profile(orchestrator: Orchestrator, session_id: Optional[str]) -> Dict[str, Any]:
    """Resolve reasoning threshold profile with optional session adaptive overrides."""
    profile = dict(Settings.reasoning_threshold_profile() or {})
    if not session_id:
        return profile

    session_profile = orchestrator._session_adaptive_thresholds.get(session_id)
    if not isinstance(session_profile, dict) or not session_profile:
        return profile

    merged = dict(profile)
    for key in (
        "soft_risk_max",
        "hard_risk_max",
        "weak_score_escalation_count",
        "score_feedback_canary_soft_only",
    ):
        if key in session_profile:
            merged[key] = session_profile[key]

    base_source = str(profile.get("source") or "env")
    session_source = str(session_profile.get("source") or "session")
    merged["source"] = f"{base_source}+{session_source}"
    merged["session_override"] = True
    merged["session_override_version"] = str(session_profile.get("version") or "session")
    return merged


def maybe_apply_runtime_threshold_adaptation(
    orchestrator: Orchestrator,
    *,
    session_id: str,
    runtime_audit_report: Dict[str, Any],
    threshold_profile: Dict[str, Any],
    feedback_entry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Adaptively adjust session reasoning thresholds based on runtime audit findings."""
    decision_rank = {
        "accept": 0,
        "allow": 0,
        "warn": 1,
        "revise": 2,
        "block": 3,
    }
    current_outcome = {
        "decision": str((feedback_entry or {}).get("decision") or "accept").lower(),
        "confidence_score": float((feedback_entry or {}).get("confidence_score", 0.0) or 0.0),
        "actionable_risk": float((feedback_entry or {}).get("actionable_risk", 0.0) or 0.0),
    }
    adaptive_state = dict(orchestrator._session_adaptive_state.get(session_id) or {})
    baseline_profile = dict(adaptive_state.get("baseline_profile") or threshold_profile or {})
    last_outcome = dict(adaptive_state.get("last_outcome") or {})

    if adaptive_state.get("active", False) and last_outcome:
        previous_decision_rank = int(decision_rank.get(str(last_outcome.get("decision") or "accept"), 0))
        current_decision_rank = int(decision_rank.get(str(current_outcome.get("decision") or "accept"), 0))
        previous_confidence = float(last_outcome.get("confidence_score", 0.0) or 0.0)
        previous_risk = float(last_outcome.get("actionable_risk", 0.0) or 0.0)

        confidence_drop = previous_confidence - float(current_outcome.get("confidence_score", 0.0) or 0.0)
        risk_increase = float(current_outcome.get("actionable_risk", 0.0) or 0.0) - previous_risk
        rollback_due_to_worse_outcome = (
            current_decision_rank > previous_decision_rank
            or confidence_drop >= 0.12
            or risk_increase >= 0.25
        )

        if rollback_due_to_worse_outcome:
            rollback_profile = {
                "soft_risk_max": round(float(baseline_profile.get("soft_risk_max", 5.0) or 5.0), 6),
                "hard_risk_max": round(float(baseline_profile.get("hard_risk_max", 8.0) or 8.0), 6),
                "weak_score_escalation_count": int(
                    baseline_profile.get("weak_score_escalation_count", threshold_profile.get("weak_score_escalation_count", 2)) or 2
                ),
                "score_feedback_canary_soft_only": bool(
                    baseline_profile.get(
                        "score_feedback_canary_soft_only",
                        threshold_profile.get("score_feedback_canary_soft_only", False),
                    )
                ),
                "version": f"rollback-{str((feedback_entry or {}).get('run_id') or 'runtime')}",
                "source": "runtime_audit_rollback",
            }
            orchestrator._session_adaptive_thresholds[session_id] = rollback_profile
            orchestrator._session_adaptive_state[session_id] = {
                "active": False,
                "baseline_profile": baseline_profile,
                "last_outcome": current_outcome,
                "last_applied_profile": dict(rollback_profile),
                "last_action": "rollback",
            }
            return {
                "applied": True,
                "action": "rollback",
                "reason": "worse_audit_outcome_after_adaptation",
                "profile": rollback_profile,
            }

    sample_size = int(runtime_audit_report.get("sample_size") or 0)
    risk_scores = [float(item) for item in (runtime_audit_report.get("risk_scores") or []) if isinstance(item, (int, float))]
    weak_score_count = int(runtime_audit_report.get("weak_score_count") or 0)

    if sample_size < 3 or not risk_scores:
        return {
            "applied": False,
            "action": "none",
            "reason": "insufficient_audit_samples",
            "sample_size": sample_size,
        }

    escalation = int(threshold_profile.get("weak_score_escalation_count", 2) or 2)
    soft_quantile = 0.70 if weak_score_count >= escalation else 0.80
    hard_quantile = 0.90 if weak_score_count >= escalation else 0.95

    new_soft = float(calibrate_thresholds_quantile(risk_scores, quantile=soft_quantile))
    new_hard = float(calibrate_thresholds_quantile(risk_scores, quantile=hard_quantile))

    curr_soft = float(threshold_profile.get("soft_risk_max", 5.0) or 5.0)
    curr_hard = float(threshold_profile.get("hard_risk_max", 8.0) or 8.0)

    soft_shift = new_soft - curr_soft
    hard_shift = new_hard - curr_hard
    significant_shift = abs(soft_shift) >= 0.25 or abs(hard_shift) >= 0.35

    if not significant_shift:
        return {
            "applied": False,
            "action": "none",
            "reason": "shift_below_adaptation_delta",
            "soft_shift": round(soft_shift, 4),
            "hard_shift": round(hard_shift, 4),
        }

    clamped_soft = max(1.5, min(7.5, new_soft))
    clamped_hard = max(clamped_soft + 1.0, min(12.0, new_hard))
    canary_soft_only = bool(threshold_profile.get("score_feedback_canary_soft_only", False))
    final_hard = curr_hard if canary_soft_only else clamped_hard

    adapted_profile = {
        "soft_risk_max": round(clamped_soft, 6),
        "hard_risk_max": round(final_hard, 6),
        "weak_score_escalation_count": escalation,
        "score_feedback_canary_soft_only": canary_soft_only,
        "version": f"adaptive-{str((feedback_entry or {}).get('run_id') or 'runtime')}",
        "source": "runtime_audit_quantile_calibration",
    }
    orchestrator._session_adaptive_thresholds[session_id] = adapted_profile
    orchestrator._session_adaptive_state[session_id] = {
        "active": True,
        "baseline_profile": baseline_profile,
        "last_outcome": current_outcome,
        "last_applied_profile": dict(adapted_profile),
        "last_action": "adapted",
    }

    return {
        "applied": True,
        "action": "adapted",
        "reason": "quantile_recalibration",
        "soft_shift": round(soft_shift, 4),
        "hard_shift": round(hard_shift, 4),
        "profile": adapted_profile,
    }


def compute_belief_snapshot(
    orchestrator: Orchestrator,
    *,
    metric_inputs: Dict[str, Any],
    math_signals: Dict[str, Any],
    belief_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute Phase-1 belief update (Bayes + Kalman + Variance)."""
    prior_mean = float(belief_params.get("prior_mean", 0.70))
    prior_var = float(belief_params.get("prior_var", 0.05))
    obs_var = float(belief_params.get("obs_var", 0.08))

    raw_ev = float(metric_inputs.get("raw_evidence", 0.5))
    validation_conf = float(math_signals.get("validation_confidence", 0.5))
    obs_val = 0.6 * raw_ev + 0.4 * validation_conf

    kalman_gain = prior_var / (prior_var + obs_var)
    posterior_mean = prior_mean + kalman_gain * (obs_val - prior_mean)
    posterior_var = (1.0 - kalman_gain) * prior_var

    return {
        "prior_mean": round(prior_mean, 6),
        "prior_var": round(prior_var, 6),
        "observation": round(obs_val, 6),
        "kalman_gain": round(kalman_gain, 6),
        "posterior_mean": round(posterior_mean, 6),
        "posterior_var": round(posterior_var, 6),
        "belief_confidence": round(max(0.0, min(1.0, posterior_mean)), 6),
    }


def compute_utility_snapshot(
    orchestrator: Orchestrator,
    *,
    metric_inputs: Dict[str, Any],
    belief_snapshot: Dict[str, Any],
    utility_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute Phase-2 utility metrics (IG, CWU, Temporal Discount)."""
    alpha = float(utility_params.get("alpha", 0.4))
    beta = float(utility_params.get("beta", 0.35))
    gamma = float(utility_params.get("gamma", 0.25))
    discount = float(utility_params.get("discount_factor", 0.95))

    p_mean = float(belief_snapshot.get("posterior_mean", 0.5))
    ev_weight = float(metric_inputs.get("evidence_weight", 0.5))
    cost = float(metric_inputs.get("resource_cost", 0.1))

    ig = ev_weight * (1.0 - p_mean)
    cwu = alpha * p_mean + beta * ig - gamma * cost
    disc_utility = cwu * discount

    return {
        "information_gain": round(ig, 6),
        "cwu_raw": round(cwu, 6),
        "discounted_utility": round(disc_utility, 6),
        "weights": {"alpha": alpha, "beta": beta, "gamma": gamma, "discount": discount},
    }


def compute_structure_stability_snapshot(
    orchestrator: Orchestrator,
    *,
    metric_inputs: Dict[str, Any],
    math_signals: Dict[str, Any],
    stability_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute Phase-3 structure/stability/regularization metrics."""
    lambda_reg = float(stability_params.get("lambda_reg", 0.15))
    max_drift = float(stability_params.get("max_drift", 0.30))

    drift = float(math_signals.get("concept_drift", 0.0))
    complexity = float(metric_inputs.get("complexity", 0.2))

    penalty = lambda_reg * complexity + max(0.0, drift - max_drift)
    stability = max(0.0, 1.0 - penalty)

    return {
        "concept_drift": round(drift, 6),
        "complexity": round(complexity, 6),
        "regularization_penalty": round(penalty, 6),
        "stability_score": round(stability, 6),
    }


def compute_decision_snapshot(
    orchestrator: Orchestrator,
    *,
    belief_snapshot: Dict[str, Any],
    utility_snapshot: Dict[str, Any],
    stability_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute Phase-4 multi-objective decision snapshot."""
    b_conf = float(belief_snapshot.get("belief_confidence", 0.5))
    u_disc = float(utility_snapshot.get("discounted_utility", 0.5))
    s_score = float(stability_snapshot.get("stability_score", 0.5))

    composite_score = 0.40 * b_conf + 0.35 * u_disc + 0.25 * s_score

    if composite_score >= 0.70:
        action = "proceed"
    elif composite_score >= 0.45:
        action = "refine"
    else:
        action = "fallback"

    return {
        "composite_score": round(composite_score, 6),
        "recommended_action": action,
        "components": {
            "belief": round(b_conf, 4),
            "utility": round(u_disc, 4),
            "stability": round(s_score, 4),
        },
    }


def compute_reasoning_metrics_snapshot_julia(
    orchestrator: Orchestrator,
    *,
    metric_inputs: Dict[str, Any],
    math_signals: Dict[str, Any],
    julia_params: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Execute Julia-based reasoning metrics simulation if available."""
    try:
        from services.simulation.bridge import run_julia_simulation
        sim_input = {
            "metric_inputs": metric_inputs,
            "math_signals": math_signals,
            "julia_params": julia_params,
        }
        res = run_julia_simulation("reasoning_metrics_snapshot", sim_input)
        if isinstance(res, dict) and res.get("status") == "success":
            return res.get("result")
    except Exception as exc:
        _LOGGER.debug("Julia reasoning metrics snapshot skipped: %s", exc)
    return None


def compute_reasoning_metrics_snapshot(
    orchestrator: Orchestrator,
    *,
    inputs: Dict[str, Any],
    session_id: Optional[str] = None,
) -> ReasoningMetricsSnapshot:
    """Derive reasoning metrics snapshot using python or julia execution."""
    threshold_profile = resolve_reasoning_threshold_profile(orchestrator, session_id)
    prev_feedback = orchestrator._session_score_feedback.get(session_id) if session_id and hasattr(orchestrator, "_session_score_feedback") else None
    prev_history = orchestrator._session_score_history.get(session_id) if session_id and hasattr(orchestrator, "_session_score_history") else None

    base_metric_inputs = derive_reasoning_metric_inputs(inputs)
    metric_inputs, score_feedback_meta = orchestrator._apply_score_feedback_to_metric_inputs(
        inputs=base_metric_inputs,
        previous_score_feedback=prev_feedback,
        previous_score_history=prev_history,
    )

    py_snapshot = compute_reasoning_metrics_snapshot_python(
        metric_inputs,
        soft_risk_max=float(threshold_profile.get("soft_risk_max", 5.0) or 5.0),
        hard_risk_max=float(threshold_profile.get("hard_risk_max", 8.0) or 8.0),
    )
    res_dict = py_snapshot.dict() if hasattr(py_snapshot, "dict") else (py_snapshot.model_dump() if hasattr(py_snapshot, "model_dump") else dict(py_snapshot or {}))
    if isinstance(score_feedback_meta, dict):
        res_dict["score_feedback"] = score_feedback_meta
        res_dict.update(score_feedback_meta)

    if isinstance(inputs, dict) and "judge_post" in inputs and inputs["judge_post"]:
        res_dict["judge_post"] = inputs["judge_post"]

    hybrid_control = build_hybrid_control_metadata(
        metrics=res_dict,
        score_feedback=score_feedback_meta if isinstance(score_feedback_meta, dict) else {},
        judge_post=inputs.get("judge_post") if isinstance(inputs, dict) else None,
        query=str((inputs.get("query") if isinstance(inputs, dict) else "") or ""),
        response=str((inputs.get("response") if isinstance(inputs, dict) else "") or ""),
    )
    if isinstance(hybrid_control, dict):
        res_dict["hybrid_control"] = hybrid_control
        res_dict.update(hybrid_control)

    return ReasoningMetricsSnapshot(**res_dict)
