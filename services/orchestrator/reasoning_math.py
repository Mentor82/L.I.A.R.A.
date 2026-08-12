"""Math helpers for reasoning metrics.

These helpers keep entropy estimation and threshold calibration deterministic,
testable, and reusable across orchestrator runtime and offline audits.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, Iterable, List, Tuple


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    q = _clip(q, 0.0, 1.0)
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def normalized_shannon_entropy_from_source_counts(source_counts: Dict[str, Any]) -> float:
    """Estimate normalized entropy from source-count buckets in [0,1]."""
    from math import log2

    counts = [max(0, int(v)) for v in source_counts.values() if isinstance(v, (int, float))]
    total = sum(counts)
    if total <= 0:
        return 0.0

    probabilities = [c / total for c in counts if c > 0]
    if not probabilities:
        return 0.0

    raw_entropy = -sum(p * log2(p) for p in probabilities)
    max_entropy = log2(len(probabilities)) if len(probabilities) > 1 else 1.0
    if max_entropy <= 0:
        return 0.0
    return _clip(raw_entropy / max_entropy, 0.0, 1.0)


def entropy_proxy_from_context_debug(context_debug: Dict[str, Any]) -> float:
    """Pragmatic entropy proxy based on diversity/conflict/unresolved signals.

    The model follows the math design doc while remaining robust to missing keys.
    """
    source_counts = dict((context_debug or {}).get("sources") or {})
    active_sources = sum(
        1
        for value in source_counts.values()
        if isinstance(value, (int, float)) and float(value) > 0.0
    )
    total_sources = max(1, len(source_counts))
    source_diversity = _clip(active_sources / total_sources, 0.0, 1.0)

    conflict_ratio = context_debug.get("conflict_ratio", context_debug.get("contradiction_ratio", 0.0))
    unresolved_ratio = context_debug.get("unresolved_ratio", context_debug.get("open_questions_ratio", 0.0))
    try:
        conflict_ratio_f = _clip(float(conflict_ratio), 0.0, 1.0)
    except (TypeError, ValueError):
        conflict_ratio_f = 0.0
    try:
        unresolved_ratio_f = _clip(float(unresolved_ratio), 0.0, 1.0)
    except (TypeError, ValueError):
        unresolved_ratio_f = 0.0

    w_source = 0.55
    w_conflict = 0.25
    w_unresolved = 0.20
    proxy = (
        (w_source * source_diversity)
        + (w_conflict * conflict_ratio_f)
        + (w_unresolved * unresolved_ratio_f)
    )
    return _clip(proxy, 0.0, 1.0)


def estimate_context_entropy(context_debug: Dict[str, Any]) -> float:
    """Estimate context entropy in [0,1] with optional proxy blending.

    Behavior is backward compatible: if conflict/unresolved hints are missing,
    source-distribution entropy is used directly.
    """
    explicit = (context_debug or {}).get("context_entropy")
    if isinstance(explicit, (int, float)):
        return _clip(float(explicit), 0.0, 1.0)

    source_counts = dict((context_debug or {}).get("sources") or {})
    source_entropy = normalized_shannon_entropy_from_source_counts(source_counts)

    has_proxy_hints = any(
        key in (context_debug or {})
        for key in ("conflict_ratio", "contradiction_ratio", "unresolved_ratio", "open_questions_ratio")
    )
    if not has_proxy_hints:
        return source_entropy

    proxy = entropy_proxy_from_context_debug(context_debug or {})
    # Keep source entropy dominant to avoid surprising runtime behavior drift.
    return _clip((0.7 * source_entropy) + (0.3 * proxy), 0.0, 1.0)


def calibrate_thresholds_quantile(
    values: Iterable[float],
    *,
    soft_q: float = 0.90,
    hard_q: float = 0.99,
    min_gap: float = 0.25,
) -> Tuple[float, float]:
    """Calibrate soft/hard thresholds using empirical quantiles."""
    data = [float(v) for v in values]
    if not data:
        return (0.0, float(min_gap))

    soft = _quantile(data, soft_q)
    hard = _quantile(data, hard_q)
    if hard <= soft + min_gap:
        hard = soft + float(min_gap)
    return (soft, hard)


def calibrate_thresholds_mad(
    values: Iterable[float],
    *,
    soft_k: float = 2.0,
    hard_k: float = 4.0,
    min_gap: float = 0.25,
) -> Tuple[float, float]:
    """Calibrate soft/hard thresholds using median and MAD."""
    data = [float(v) for v in values]
    if not data:
        return (0.0, float(min_gap))

    center = median(data)
    mad = median([abs(v - center) for v in data])
    soft = center + (float(soft_k) * mad)
    hard = center + (float(hard_k) * mad)
    if hard <= soft + min_gap:
        hard = soft + float(min_gap)
    return (soft, hard)


def ewma_update(theta: float, estimate: float, *, eta: float = 0.05) -> float:
    """One-step exponentially weighted moving average update."""
    eta_clipped = _clip(float(eta), 0.0, 1.0)
    return ((1.0 - eta_clipped) * float(theta)) + (eta_clipped * float(estimate))


def compute_rds_v2(
    *,
    depth: int,
    branching_factor_avg: float,
    context_entropy: float,
    lambda_entropy: float = 0.8,
) -> float:
    """Compute RDS v2 using the project formula.

    RDS = log2(1 + depth * branching_factor_avg) + lambda * entropy
    """
    from math import log2

    depth_safe = max(0, int(depth))
    branch_safe = max(0.0, float(branching_factor_avg))
    entropy_safe = max(0.0, float(context_entropy))
    lambda_safe = max(0.0, float(lambda_entropy))
    return float(log2(1 + max(0.0, depth_safe * branch_safe)) + (lambda_safe * entropy_safe))


def classify_rds_band(
    rds_v2: float,
    *,
    low_max: float = 2.0,
    medium_max: float = 3.5,
) -> str:
    """Classify RDS value into low/medium/high bands."""
    value = float(rds_v2)
    if value <= float(low_max):
        return "low"
    if value <= float(medium_max):
        return "medium"
    return "high"
