from __future__ import annotations

from services.orchestrator.reasoning_math import (
    calibrate_thresholds_mad,
    calibrate_thresholds_quantile,
    classify_rds_band,
    compute_rds_v2,
    entropy_proxy_from_context_debug,
    estimate_context_entropy,
    ewma_update,
    normalized_shannon_entropy_from_source_counts,
)
from services.config import Settings
from services.orchestrator.orchestrator import Orchestrator


def test_normalized_shannon_entropy_from_source_counts_balanced_high_entropy() -> None:
    value = normalized_shannon_entropy_from_source_counts({"redis": 10, "qdrant": 10, "chroma": 10})
    assert 0.95 <= value <= 1.0


def test_normalized_shannon_entropy_from_source_counts_skewed_low_entropy() -> None:
    value = normalized_shannon_entropy_from_source_counts({"redis": 100, "qdrant": 1})
    assert 0.0 <= value < 0.2


def test_estimate_context_entropy_backwards_compatible_without_proxy_hints() -> None:
    ctx = {"sources": {"redis": 3, "qdrant": 3, "chroma": 3}}
    from_sources = normalized_shannon_entropy_from_source_counts(ctx["sources"])
    assert estimate_context_entropy(ctx) == from_sources


def test_entropy_proxy_from_context_debug_uses_conflict_and_unresolved() -> None:
    ctx = {
        "sources": {"redis": 2, "qdrant": 1, "chroma": 1},
        "conflict_ratio": 0.8,
        "unresolved_ratio": 0.5,
    }
    proxy = entropy_proxy_from_context_debug(ctx)
    assert 0.0 <= proxy <= 1.0
    assert proxy > 0.5


def test_estimate_context_entropy_blends_source_and_proxy_when_hints_present() -> None:
    ctx = {
        "sources": {"redis": 10, "qdrant": 1},
        "conflict_ratio": 0.9,
        "unresolved_ratio": 0.8,
    }
    value = estimate_context_entropy(ctx)
    source_only = normalized_shannon_entropy_from_source_counts(ctx["sources"])
    assert 0.0 <= value <= 1.0
    assert value > source_only


def test_calibrate_thresholds_quantile_respects_min_gap() -> None:
    soft, hard = calibrate_thresholds_quantile([0.2, 0.3, 0.4, 0.5], soft_q=0.75, hard_q=0.76, min_gap=0.5)
    assert hard - soft >= 0.5


def test_calibrate_thresholds_mad_respects_min_gap() -> None:
    soft, hard = calibrate_thresholds_mad([1.0, 1.2, 1.1, 1.3], soft_k=2.0, hard_k=2.1, min_gap=0.4)
    assert hard - soft >= 0.4


def test_ewma_update_blends_theta_and_estimate() -> None:
    value = ewma_update(10.0, 6.0, eta=0.25)
    assert value == 9.0


def test_orchestrator_python_metrics_use_configurable_risk_thresholds(monkeypatch) -> None:
    metrics = Orchestrator._compute_reasoning_metrics_snapshot_python(
        {
            "depth": 6,
            "branching_factor_avg": 2.0,
            "memory_items": 20,
            "tool_calls": 3,
            "token_estimate": 400,
            "context_entropy": 0.9,
            "goal_progress": 0.1,
            "policy_risk": 0.9,
        },
        soft_risk_max=0.2,
        hard_risk_max=0.4,
        fallback_reason="test-fallback",
    )

    assert metrics["compute_backend"] == "python"
    assert metrics["compute_path"] == "fallback"
    assert metrics["reasoning_cost"] == metrics["total_cost"]
    assert metrics["risk_total"] == metrics["total_risk"]
    assert metrics["rds_mode"] == "diagnostic"
    assert metrics["should_soft_limit"] is True
    assert metrics["should_hard_block"] is True


def test_rds_is_diagnostic_only_for_gating(monkeypatch) -> None:
    metrics = Orchestrator._compute_reasoning_metrics_snapshot_python(
        {
            "depth": 500,
            "branching_factor_avg": 5.0,
            "memory_items": 1,
            "tool_calls": 0,
            "token_estimate": 100,
            "context_entropy": 0.0,
            "goal_progress": 0.5,
            "policy_risk": 0.0,
        }
    )

    assert metrics["rds_mode"] == "diagnostic"
    assert metrics["rds_v2"] > 3.0
    assert metrics["total_risk"] > 0.4
    assert metrics["actionable_risk"] == 0.0
    assert metrics["should_soft_limit"] is False
    assert metrics["should_hard_block"] is False


def test_rds_band_low_medium_high_scenarios() -> None:
    low = compute_rds_v2(depth=1, branching_factor_avg=1.0, context_entropy=0.10)
    medium = compute_rds_v2(depth=3, branching_factor_avg=1.5, context_entropy=0.40)
    high = compute_rds_v2(depth=7, branching_factor_avg=2.2, context_entropy=0.90)

    assert low < medium < high
    assert classify_rds_band(low) == "low"
    assert classify_rds_band(medium) == "medium"
    assert classify_rds_band(high) == "high"


def test_rds_band_custom_thresholds() -> None:
    value = compute_rds_v2(depth=4, branching_factor_avg=1.7, context_entropy=0.3)
    assert classify_rds_band(value, low_max=2.0, medium_max=2.7) == "high"
