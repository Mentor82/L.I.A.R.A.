from __future__ import annotations

from services.orchestrator.reasoning_math_ext import (
    compute_decision_snapshot,
    multi_objective_decision,
)


def test_multi_objective_decision_advisory_when_all_signals_clean() -> None:
    result = multi_objective_decision(
        total_cost=0.5,
        actionable_risk=0.1,
        context_entropy=0.1,
        utility_discounted=2.0,
        stability_score=0.9,
        regularization_total=0.1,
        path_pressure=0.2,
    )
    assert result["recommended_mode"] == "advisory"
    assert result["pareto_status"] in {"efficient", "tradeoff"}


def test_multi_objective_decision_soft_on_negative_utility() -> None:
    result = multi_objective_decision(
        total_cost=8.0,
        actionable_risk=0.1,
        context_entropy=0.2,
        utility_discounted=-1.0,
        stability_score=0.9,
        regularization_total=0.1,
        path_pressure=0.2,
    )
    assert result["recommended_mode"] == "soft"
    assert result["resolution_basis"] == "utility"


def test_multi_objective_decision_hard_on_high_risk() -> None:
    result = multi_objective_decision(
        total_cost=1.0,
        actionable_risk=9.0,
        context_entropy=0.3,
        utility_discounted=1.0,
        stability_score=0.9,
        regularization_total=0.1,
        path_pressure=0.1,
        soft_risk_max=5.0,
        hard_risk_max=8.0,
    )
    assert result["recommended_mode"] == "hard"
    assert result["dominant_objective"] == "risk"


def test_multi_objective_decision_uses_score_mode_floor() -> None:
    result = multi_objective_decision(
        total_cost=1.0,
        actionable_risk=0.1,
        context_entropy=0.1,
        utility_discounted=1.0,
        stability_score=0.9,
        regularization_total=0.1,
        path_pressure=0.1,
        mode_floor="soft",
    )
    assert result["recommended_mode"] == "soft"
    assert result["resolution_basis"] == "score"


def test_multi_objective_decision_multi_objective_on_stability_pressure() -> None:
    result = multi_objective_decision(
        total_cost=2.0,
        actionable_risk=0.2,
        context_entropy=0.3,
        utility_discounted=0.5,
        stability_score=0.1,
        regularization_total=1.5,
        path_pressure=0.9,
    )
    assert result["resolution_basis"] == "multi_objective"
    assert result["dominant_objective"] in {"stability", "regularization", "structure"}


def test_compute_decision_snapshot_fields_present() -> None:
    result = compute_decision_snapshot(
        total_cost=2.0,
        actionable_risk=0.2,
        context_entropy=0.3,
        utility_discounted=0.5,
        stability_score=0.7,
        regularization_total=0.4,
        path_pressure=0.3,
    )
    expected = [
        "decision_pareto_status",
        "decision_dominant_objective",
        "decision_recommended_mode",
        "decision_recommended_action",
        "decision_resolution_basis",
        "decision_objectives",
        "decision_weak_objectives",
        "decision_compute_backend",
        "decision_compute_path",
    ]
    for key in expected:
        assert key in result


def test_compute_decision_snapshot_backend_python() -> None:
    result = compute_decision_snapshot(
        total_cost=0.0,
        actionable_risk=0.0,
        context_entropy=0.0,
        utility_discounted=0.0,
        stability_score=1.0,
        regularization_total=0.0,
        path_pressure=0.0,
    )
    assert result["decision_compute_backend"] == "python"
    assert result["decision_compute_path"] == "primary"
