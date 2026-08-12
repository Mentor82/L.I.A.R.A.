"""Extended math helpers for LIARA reasoning pipeline — Phase 1.

Implements belief-update and uncertainty primitives described in MIRKO_MATHE_2.md.

Architecture rule (MIRKO_MATHE_2.md, Phase 0):
- Complex computations run first in Julia (via JuliaBridge).
- Python provides a deterministic, functionally-equivalent fallback.
- Both paths share the same IO contract defined here.

IO CONTRACT — inputs expected by all Phase-1 functions
------------------------------------------------------
Belief-state vector (passed between turns):
    belief: Dict with keys
        prior: float in [0, 1]       — confidence estimate from previous turn
        entropy: float in [0, 1]     — context entropy at last turn
        variance: float >= 0         — running variance of signal

Observation (per turn):
    observation: Dict with keys
        likelihood: float in [0, 1]  — P(evidence | hypothesis)
        signal: float                — raw signal value (e.g. tool confidence)
        entropy: float in [0, 1]     — current context entropy

Config (optional overrides):
    kalman_gain: float in (0, 1]     — blending speed (default 0.3)
    min_variance: float >= 0         — variance floor (default 0.0001)

AUDIT FIELDS — all return dicts include:
    compute_backend: "python" | "julia"
    compute_path: "primary" | "fallback"
"""

from __future__ import annotations

from typing import Any, Dict

_CLIP = lambda v, lo, hi: max(lo, min(hi, float(v)))  # noqa: E731

# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _belief_from_dict(d: Dict[str, Any]) -> tuple[float, float, float]:
    """Extract (prior, entropy, variance) from a belief dict."""
    prior = _CLIP(_safe_float(d.get("prior", 0.5), 0.5), 0.0, 1.0)
    entropy = _CLIP(_safe_float(d.get("entropy", 0.0), 0.0), 0.0, 1.0)
    variance = max(0.0, _safe_float(d.get("variance", 0.0), 0.0))
    return prior, entropy, variance


def _observation_from_dict(d: Dict[str, Any]) -> tuple[float, float, float]:
    """Extract (likelihood, signal, entropy) from an observation dict."""
    likelihood = _CLIP(_safe_float(d.get("likelihood", 0.5), 0.5), 1e-9, 1.0)
    signal = _safe_float(d.get("signal", 0.5), 0.5)
    entropy = _CLIP(_safe_float(d.get("entropy", 0.0), 0.0), 0.0, 1.0)
    return likelihood, signal, entropy


# ---------------------------------------------------------------------------
# Phase 1a — Bayes-Update
# ---------------------------------------------------------------------------

def bayes_update(
    belief: Dict[str, Any],
    observation: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute P(H|E) via Bayes' theorem.

    P(H|E) = P(E|H) * P(H) / P(E)

    P(E) is marginalized as:
        P(E) = P(E|H)*P(H) + P(E|¬H)*(1-P(H))

    P(E|¬H) is approximated as (1 - likelihood) to keep the contract simple.

    Returns:
        posterior: float            updated belief
        prior: float                original prior
        likelihood: float           P(E|H) as passed in
        marginal: float             P(E)
        compute_backend: str
        compute_path: str
    """
    prior, _, _ = _belief_from_dict(belief)
    likelihood, _, _ = _observation_from_dict(observation)

    # P(E|¬H) as complement — simple but correct for binary hypotheses.
    likelihood_neg = _CLIP(1.0 - likelihood, 1e-9, 1.0)
    marginal = likelihood * prior + likelihood_neg * (1.0 - prior)
    if marginal < 1e-12:
        marginal = 1e-12

    posterior = _CLIP((likelihood * prior) / marginal, 0.0, 1.0)

    return {
        "posterior": round(posterior, 6),
        "prior": round(prior, 6),
        "likelihood": round(likelihood, 6),
        "marginal": round(marginal, 6),
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 1b — Kalman-like Belief Tracking
# ---------------------------------------------------------------------------

def kalman_belief_update(
    belief: Dict[str, Any],
    observation: Dict[str, Any],
    *,
    kalman_gain: float = 0.3,
    min_variance: float = 1e-4,
) -> Dict[str, Any]:
    """Turn-to-turn belief smoothing via a scalar Kalman-like filter.

    x_{t+1} = x_t + K * (z_t - x_t)

    where x_t = prior confidence, z_t = observed signal, K = kalman_gain.

    Updated variance follows the Joseph form:
        P_{t+1} = (1 - K)^2 * P_t + K^2 * noise_variance

    noise_variance is derived from context entropy (higher entropy → noisier).

    Returns:
        estimate: float         smoothed confidence
        residual: float         z_t - x_t (innovation)
        variance: float         updated running variance
        kalman_gain: float      gain used
        compute_backend: str
        compute_path: str
    """
    prior, prior_entropy, prior_variance = _belief_from_dict(belief)
    _, signal, obs_entropy = _observation_from_dict(observation)

    K = _CLIP(_safe_float(kalman_gain, 0.3), 0.0, 1.0)
    noise_variance = max(min_variance, float(obs_entropy))

    residual = signal - prior
    estimate = _CLIP(prior + K * residual, 0.0, 1.0)

    # Joseph form for variance update
    updated_variance = (1.0 - K) ** 2 * max(min_variance, prior_variance) + K**2 * noise_variance
    updated_variance = max(min_variance, round(updated_variance, 8))

    return {
        "estimate": round(estimate, 6),
        "residual": round(residual, 6),
        "variance": round(updated_variance, 8),
        "kalman_gain": round(K, 6),
        "prior": round(prior, 6),
        "signal": round(signal, 6),
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 1c — Variance / Confidence
# ---------------------------------------------------------------------------

def compute_signal_variance(
    signals: list[float],
    *,
    ddof: int = 1,
) -> Dict[str, Any]:
    """Compute mean, variance, and normalized confidence over a signal window.

    confidence = 1 / (1 + variance)   — maps [0, ∞) → (0, 1]

    Args:
        signals:  list of float values (tool confidence scores, memory scores…)
        ddof:     delta degrees of freedom (1 for sample variance)

    Returns:
        mean: float
        variance: float
        std: float
        confidence: float    in (0, 1]
        n: int               number of samples used
        compute_backend: str
        compute_path: str
    """
    n = len(signals)
    if n == 0:
        return {
            "mean": 0.0,
            "variance": 0.0,
            "std": 0.0,
            "confidence": 1.0,
            "n": 0,
            "compute_backend": "python",
            "compute_path": "primary",
        }

    clean = [float(s) for s in signals]
    mean = sum(clean) / n
    if n <= ddof:
        variance = 0.0
    else:
        variance = sum((x - mean) ** 2 for x in clean) / (n - ddof)
    std = variance ** 0.5
    confidence = _CLIP(1.0 / (1.0 + variance), 0.0, 1.0)

    return {
        "mean": round(mean, 6),
        "variance": round(variance, 8),
        "std": round(std, 8),
        "confidence": round(confidence, 6),
        "n": n,
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 1 — Combined snapshot (single call for orchestrator integration)
# ---------------------------------------------------------------------------

def compute_belief_snapshot(
    belief: Dict[str, Any],
    observation: Dict[str, Any],
    signal_window: list[float] | None = None,
    *,
    kalman_gain: float = 0.3,
    min_variance: float = 1e-4,
) -> Dict[str, Any]:
    """Compute all Phase-1 belief metrics in a single call.

    Returns a flat dict combining Bayes posterior, Kalman estimate, and
    signal variance — suitable for direct inclusion in reasoning_metrics.

    Audit fields:
        belief_compute_backend: "python" | "julia"
        belief_compute_path: "primary" | "fallback"
    """
    bayes = bayes_update(belief, observation)
    kalman = kalman_belief_update(
        belief,
        observation,
        kalman_gain=kalman_gain,
        min_variance=min_variance,
    )
    variance_stats = compute_signal_variance(signal_window or [])

    return {
        # Bayes
        "belief_posterior": bayes["posterior"],
        "belief_prior": bayes["prior"],
        "belief_likelihood": bayes["likelihood"],
        "belief_marginal": bayes["marginal"],
        # Kalman
        "belief_estimate": kalman["estimate"],
        "belief_residual": kalman["residual"],
        "belief_variance": kalman["variance"],
        "belief_kalman_gain": kalman["kalman_gain"],
        # Signal window stats
        "signal_mean": variance_stats["mean"],
        "signal_variance": variance_stats["variance"],
        "signal_std": variance_stats["std"],
        "signal_confidence": variance_stats["confidence"],
        "signal_n": variance_stats["n"],
        # Audit
        "belief_compute_backend": "python",
        "belief_compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 2a — Information Gain
# ---------------------------------------------------------------------------

def information_gain(
    entropy_before: float,
    entropy_after: float,
) -> Dict[str, Any]:
    """Compute information gain for a single tool / reasoning step.

    IG = H_before - H_after

    Both entropies must be in [0, 1] (normalized Shannon entropy).
    Negative IG (entropy increased) is allowed — indicates the step added
    confusion rather than clarity.

    Returns:
        ig: float                   information gain (can be negative)
        entropy_before: float
        entropy_after: float
        direction: str              "gain" | "neutral" | "loss"
        compute_backend: str
        compute_path: str
    """
    h_before = _CLIP(_safe_float(entropy_before, 0.0), 0.0, 1.0)
    h_after = _CLIP(_safe_float(entropy_after, 0.0), 0.0, 1.0)

    ig = round(h_before - h_after, 6)
    if ig > 1e-6:
        direction = "gain"
    elif ig < -1e-6:
        direction = "loss"
    else:
        direction = "neutral"

    return {
        "ig": ig,
        "entropy_before": round(h_before, 6),
        "entropy_after": round(h_after, 6),
        "direction": direction,
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 2b — Confidence-Weighted Utility
# ---------------------------------------------------------------------------

def confidence_weighted_utility(
    utility: float,
    entropy: float,
) -> Dict[str, Any]:
    """Penalize raw utility by current context entropy.

    U' = U * (1 - H)

    When entropy is high (uncertain context), useful work is discounted.
    When entropy is low (clear context), utility passes through nearly intact.

    Args:
        utility:  raw utility value — can be any float (e.g. [-10, 10])
        entropy:  current context entropy in [0, 1]

    Returns:
        weighted_utility: float     U * (1 - H)
        utility: float              original utility
        entropy: float              clipped to [0, 1]
        discount_factor: float      (1 - H)
        compute_backend: str
        compute_path: str
    """
    u = _safe_float(utility, 0.0)
    h = _CLIP(_safe_float(entropy, 0.0), 0.0, 1.0)
    discount = round(1.0 - h, 6)
    weighted = round(u * discount, 6)

    return {
        "weighted_utility": weighted,
        "utility": round(u, 6),
        "entropy": round(h, 6),
        "discount_factor": discount,
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 2c — Temporal Discounting
# ---------------------------------------------------------------------------

def temporal_discount(
    value: float,
    step: int,
    *,
    gamma: float = 0.95,
) -> Dict[str, Any]:
    """Apply exponential temporal discounting over a reasoning chain.

    discounted_value = value * gamma^step

    Args:
        value:  value to discount (e.g. utility or information gain)
        step:   0-based turn/step index in the reasoning chain
        gamma:  discount factor in (0, 1] (default 0.95)

    Returns:
        discounted_value: float
        value: float
        step: int
        gamma: float
        discount_weight: float   gamma^step
        compute_backend: str
        compute_path: str
    """
    v = _safe_float(value, 0.0)
    s = max(0, int(step))
    g = _CLIP(_safe_float(gamma, 0.95), 1e-6, 1.0)

    weight = round(g ** s, 8)
    discounted = round(v * weight, 6)

    return {
        "discounted_value": discounted,
        "value": round(v, 6),
        "step": s,
        "gamma": round(g, 6),
        "discount_weight": weight,
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 2 — Combined snapshot (single call for orchestrator integration)
# ---------------------------------------------------------------------------

def compute_utility_snapshot(
    utility: float,
    entropy_before: float,
    entropy_after: float,
    step: int,
    *,
    gamma: float = 0.95,
) -> Dict[str, Any]:
    """Compute all Phase-2 utility metrics in a single call.

    Combines:
    - information_gain (H_before - H_after)
    - confidence_weighted_utility (U * (1 - H_after))
    - temporal_discount applied to weighted utility

    Returns a flat dict for direct inclusion in reasoning_metrics.

    Audit fields:
        utility_compute_backend: "python" | "julia"
        utility_compute_path: "primary" | "fallback"
    """
    ig = information_gain(entropy_before, entropy_after)
    cwu = confidence_weighted_utility(utility, entropy_after)
    td = temporal_discount(cwu["weighted_utility"], step, gamma=gamma)

    return {
        # Information gain
        "utility_ig": ig["ig"],
        "utility_entropy_before": ig["entropy_before"],
        "utility_entropy_after": ig["entropy_after"],
        "utility_ig_direction": ig["direction"],
        # Confidence-weighted utility
        "utility_weighted": cwu["weighted_utility"],
        "utility_raw": cwu["utility"],
        "utility_discount_factor": cwu["discount_factor"],
        # Temporal discounting
        "utility_discounted": td["discounted_value"],
        "utility_step": td["step"],
        "utility_gamma": td["gamma"],
        "utility_discount_weight": td["discount_weight"],
        # Audit
        "utility_compute_backend": "python",
        "utility_compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 3a — Graph structure metrics
# ---------------------------------------------------------------------------

def graph_structure_metrics(
    *,
    node_count: int,
    edge_count: int,
    community_count: int,
    shortest_path_to_goal: float,
) -> Dict[str, Any]:
    """Compute lightweight graph-structure diagnostics.

    Metrics:
    - clustering_proxy: 2E / (N*(N-1))
    - modularity_proxy: 1 - 1/C   (C = number of communities)
    - path_pressure: d / (d + 1)
    """
    n = max(0, int(node_count))
    e = max(0, int(edge_count))
    c = max(1, int(community_count))
    d = max(0.0, float(shortest_path_to_goal))

    if n <= 1:
        clustering = 0.0
    else:
        clustering = _CLIP((2.0 * e) / (n * (n - 1)), 0.0, 1.0)

    modularity = _CLIP(1.0 - (1.0 / c), 0.0, 1.0)
    path_pressure = _CLIP(d / (d + 1.0), 0.0, 1.0)

    return {
        "clustering_proxy": round(clustering, 6),
        "modularity_proxy": round(modularity, 6),
        "shortest_path_to_goal": round(d, 6),
        "path_pressure": round(path_pressure, 6),
        "node_count": n,
        "edge_count": e,
        "community_count": c,
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 3b — Dynamic stability heuristic
# ---------------------------------------------------------------------------

def stability_heuristic(series: list[float]) -> Dict[str, Any]:
    """Estimate local stability via a discrete derivative proxy.

    For x_t sequence, use f'(x) proxy as delta of last two points.
    Stable if |f'(x)| < 1.
    """
    clean = [float(v) for v in series]
    if len(clean) < 2:
        return {
            "derivative_proxy": 0.0,
            "stable": True,
            "stability_score": 1.0,
            "series_len": len(clean),
            "compute_backend": "python",
            "compute_path": "primary",
        }

    derivative = clean[-1] - clean[-2]
    abs_d = abs(derivative)
    stable = abs_d < 1.0
    # 1.0 means very stable, 0.0 means highly unstable.
    stability_score = _CLIP(1.0 - min(1.0, abs_d), 0.0, 1.0)

    return {
        "derivative_proxy": round(derivative, 6),
        "stable": bool(stable),
        "stability_score": round(stability_score, 6),
        "series_len": len(clean),
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 3c — Regularization penalties
# ---------------------------------------------------------------------------

def regularization_penalty(
    *,
    memory_items: int,
    tool_calls: int,
    lambda_l1: float = 0.05,
    lambda_l2: float = 0.01,
) -> Dict[str, Any]:
    """Compute L1/L2 penalties to discourage memory-bloat and tool-spam."""
    m = max(0.0, float(memory_items))
    t = max(0.0, float(tool_calls))
    l1 = max(0.0, float(lambda_l1))
    l2 = max(0.0, float(lambda_l2))

    penalty_l1 = l1 * (abs(m) + abs(t))
    penalty_l2 = l2 * (m * m + t * t)
    penalty_total = penalty_l1 + penalty_l2

    return {
        "penalty_l1": round(penalty_l1, 6),
        "penalty_l2": round(penalty_l2, 6),
        "penalty_total": round(penalty_total, 6),
        "lambda_l1": round(l1, 6),
        "lambda_l2": round(l2, 6),
        "memory_items": int(m),
        "tool_calls": int(t),
        "compute_backend": "python",
        "compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 3 — Combined snapshot (single call for orchestrator integration)
# ---------------------------------------------------------------------------

def compute_structure_stability_snapshot(
    *,
    context_debug: Dict[str, Any],
    memory_items: int,
    tool_calls: int,
    risk_series: list[float],
    lambda_l1: float = 0.05,
    lambda_l2: float = 0.01,
) -> Dict[str, Any]:
    """Compute Phase-3 graph/stability/regularization metrics in one call."""
    ctx = dict(context_debug or {})
    node_count = int(ctx.get("graph_nodes", ctx.get("node_count", 0)) or 0)
    edge_count = int(ctx.get("graph_edges", ctx.get("edge_count", 0)) or 0)
    community_count = int(ctx.get("graph_communities", ctx.get("community_count", 1)) or 1)
    shortest_path_to_goal = float(ctx.get("shortest_path_to_goal", ctx.get("path_to_goal", 0.0)) or 0.0)

    graph = graph_structure_metrics(
        node_count=node_count,
        edge_count=edge_count,
        community_count=community_count,
        shortest_path_to_goal=shortest_path_to_goal,
    )
    stability = stability_heuristic(risk_series)
    penalty = regularization_penalty(
        memory_items=memory_items,
        tool_calls=tool_calls,
        lambda_l1=lambda_l1,
        lambda_l2=lambda_l2,
    )

    return {
        # Graph
        "structure_clustering": graph["clustering_proxy"],
        "structure_modularity": graph["modularity_proxy"],
        "structure_shortest_path": graph["shortest_path_to_goal"],
        "structure_path_pressure": graph["path_pressure"],
        # Stability
        "stability_derivative": stability["derivative_proxy"],
        "stability_is_stable": stability["stable"],
        "stability_score": stability["stability_score"],
        # Regularization
        "regularization_l1": penalty["penalty_l1"],
        "regularization_l2": penalty["penalty_l2"],
        "regularization_total": penalty["penalty_total"],
        # Audit
        "structure_compute_backend": "python",
        "structure_compute_path": "primary",
    }


# ---------------------------------------------------------------------------
# Phase 4 — Multi-objective decision support
# ---------------------------------------------------------------------------

def multi_objective_decision(
    *,
    total_cost: float,
    actionable_risk: float,
    context_entropy: float,
    utility_discounted: float,
    stability_score: float,
    regularization_total: float,
    path_pressure: float,
    mode_floor: str = "advisory",
    repair_preferred: bool = False,
    soft_risk_max: float = 5.0,
    hard_risk_max: float = 8.0,
) -> Dict[str, Any]:
    """Select a recommendation from multiple competing objectives.

    Objectives:
    - minimize cost
    - minimize risk
    - minimize uncertainty
    - maximize discounted utility
    - maximize stability
    - minimize regularization burden
    - minimize path pressure to goal
    """
    total_cost_f = max(0.0, float(total_cost))
    actionable_risk_f = max(0.0, float(actionable_risk))
    context_entropy_f = _CLIP(_safe_float(context_entropy, 0.0), 0.0, 1.0)
    utility_discounted_f = _safe_float(utility_discounted, 0.0)
    stability_score_f = _CLIP(_safe_float(stability_score, 1.0), 0.0, 1.0)
    regularization_total_f = max(0.0, float(regularization_total))
    path_pressure_f = _CLIP(_safe_float(path_pressure, 0.0), 0.0, 1.0)
    soft_risk_max_f = max(1e-6, float(soft_risk_max))
    hard_risk_max_f = max(soft_risk_max_f, float(hard_risk_max))

    objective_scores = {
        "cost": round(1.0 / (1.0 + total_cost_f), 6),
        "risk": round(1.0 - _CLIP(actionable_risk_f / hard_risk_max_f, 0.0, 1.0), 6),
        "uncertainty": round(1.0 - context_entropy_f, 6),
        "utility": round(_CLIP((utility_discounted_f + 10.0) / 20.0, 0.0, 1.0), 6),
        "stability": round(stability_score_f, 6),
        "regularization": round(1.0 / (1.0 + regularization_total_f), 6),
        "structure": round(1.0 - path_pressure_f, 6),
        "score": 0.0 if (repair_preferred or mode_floor in {"soft", "hard"}) else 1.0,
    }

    weak_flags = {
        "risk": actionable_risk_f > soft_risk_max_f,
        "utility": utility_discounted_f < 0.0,
        "uncertainty": context_entropy_f > 0.7,
        "stability": stability_score_f < 0.5,
        "regularization": regularization_total_f > 1.0,
        "structure": path_pressure_f > 0.75,
        "score": bool(repair_preferred or mode_floor in {"soft", "hard"}),
    }
    weak_count = sum(1 for value in weak_flags.values() if value)

    deficits = {
        "risk": 2.0 if actionable_risk_f > hard_risk_max_f else (1.0 - objective_scores["risk"]),
        "utility": (1.0 - objective_scores["utility"]) + (0.25 if utility_discounted_f < 0.0 else 0.0),
        "uncertainty": 1.0 - objective_scores["uncertainty"],
        "stability": 1.0 - objective_scores["stability"],
        "regularization": 1.0 - objective_scores["regularization"],
        "structure": 1.0 - objective_scores["structure"],
        "score": 1.2 if repair_preferred else (1.0 if mode_floor == "hard" else (0.75 if mode_floor == "soft" else 0.0)),
        "cost": 1.0 - objective_scores["cost"],
    }
    dominant_objective = max(deficits.items(), key=lambda item: item[1])[0]

    if weak_count == 0:
        pareto_status = "efficient"
    elif weak_count >= 3:
        pareto_status = "dominated"
    else:
        pareto_status = "tradeoff"

    if actionable_risk_f > hard_risk_max_f or mode_floor == "hard":
        recommended_mode = "hard"
    elif (
        actionable_risk_f > soft_risk_max_f
        or utility_discounted_f < 0.0
        or stability_score_f < 0.5
        or repair_preferred
        or mode_floor == "soft"
        or weak_count >= 2
    ):
        recommended_mode = "soft"
    else:
        recommended_mode = "advisory"

    recommended_action_map = {
        "risk": "stop_agent_mode" if recommended_mode == "hard" else "reduce_context_window",
        "utility": "reduce_exploration",
        "uncertainty": "increase_validation_strictness",
        "stability": "stabilize_reasoning_chain",
        "regularization": "reduce_memory_pressure",
        "structure": "narrow_goal_path",
        "score": "trigger_repair_loop" if repair_preferred else "increase_validation_strictness",
        "cost": "reduce_exploration",
    }
    recommended_action = recommended_action_map.get(dominant_objective, "reduce_exploration")

    if actionable_risk_f > soft_risk_max_f:
        resolution_basis = "risk"
    elif utility_discounted_f < 0.0:
        resolution_basis = "utility"
    elif repair_preferred or mode_floor in {"soft", "hard"}:
        resolution_basis = "score"
    elif dominant_objective in {"risk", "utility", "score"}:
        resolution_basis = dominant_objective
    else:
        resolution_basis = "multi_objective"

    return {
        "decision_objectives": objective_scores,
        "pareto_status": pareto_status,
        "weak_objectives": [name for name, value in weak_flags.items() if value],
        "dominant_objective": dominant_objective,
        "recommended_mode": recommended_mode,
        "recommended_action": recommended_action,
        "resolution_basis": resolution_basis,
        "compute_backend": "python",
        "compute_path": "primary",
    }


def compute_decision_snapshot(
    *,
    total_cost: float,
    actionable_risk: float,
    context_entropy: float,
    utility_discounted: float,
    stability_score: float,
    regularization_total: float,
    path_pressure: float,
    mode_floor: str = "advisory",
    repair_preferred: bool = False,
    soft_risk_max: float = 5.0,
    hard_risk_max: float = 8.0,
) -> Dict[str, Any]:
    """Compute a flat Phase-4 decision snapshot for orchestrator integration."""
    decision = multi_objective_decision(
        total_cost=total_cost,
        actionable_risk=actionable_risk,
        context_entropy=context_entropy,
        utility_discounted=utility_discounted,
        stability_score=stability_score,
        regularization_total=regularization_total,
        path_pressure=path_pressure,
        mode_floor=mode_floor,
        repair_preferred=repair_preferred,
        soft_risk_max=soft_risk_max,
        hard_risk_max=hard_risk_max,
    )
    return {
        "decision_pareto_status": decision["pareto_status"],
        "decision_dominant_objective": decision["dominant_objective"],
        "decision_recommended_mode": decision["recommended_mode"],
        "decision_recommended_action": decision["recommended_action"],
        "decision_resolution_basis": decision["resolution_basis"],
        "decision_objectives": decision["decision_objectives"],
        "decision_weak_objectives": decision["weak_objectives"],
        "decision_compute_backend": "python",
        "decision_compute_path": "primary",
    }
