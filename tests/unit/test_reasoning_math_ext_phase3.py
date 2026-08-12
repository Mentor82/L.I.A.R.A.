from __future__ import annotations

import math

from services.orchestrator.reasoning_math_ext import (
    compute_structure_stability_snapshot,
    graph_structure_metrics,
    regularization_penalty,
    stability_heuristic,
)


def test_graph_structure_metrics_basic_ranges() -> None:
    result = graph_structure_metrics(
        node_count=5,
        edge_count=6,
        community_count=2,
        shortest_path_to_goal=3.0,
    )
    assert 0.0 <= result["clustering_proxy"] <= 1.0
    assert 0.0 <= result["modularity_proxy"] <= 1.0
    assert 0.0 <= result["path_pressure"] <= 1.0
    assert result["shortest_path_to_goal"] == 3.0


def test_graph_structure_metrics_single_node_zero_clustering() -> None:
    result = graph_structure_metrics(
        node_count=1,
        edge_count=0,
        community_count=1,
        shortest_path_to_goal=0.0,
    )
    assert result["clustering_proxy"] == 0.0


def test_stability_heuristic_stable_when_small_delta() -> None:
    result = stability_heuristic([0.2, 0.25])
    assert result["stable"] is True
    assert result["stability_score"] > 0.0


def test_stability_heuristic_unstable_when_large_delta() -> None:
    result = stability_heuristic([0.1, 1.5])
    assert result["stable"] is False
    assert result["stability_score"] == 0.0


def test_regularization_penalty_grows_with_usage() -> None:
    low = regularization_penalty(memory_items=2, tool_calls=1)
    high = regularization_penalty(memory_items=20, tool_calls=10)
    assert high["penalty_total"] > low["penalty_total"]


def test_regularization_penalty_has_l1_l2_parts() -> None:
    result = regularization_penalty(memory_items=5, tool_calls=3, lambda_l1=0.1, lambda_l2=0.01)
    assert math.isclose(result["penalty_total"], result["penalty_l1"] + result["penalty_l2"], rel_tol=1e-6)


def test_compute_structure_stability_snapshot_fields_present() -> None:
    result = compute_structure_stability_snapshot(
        context_debug={
            "graph_nodes": 6,
            "graph_edges": 8,
            "graph_communities": 2,
            "shortest_path_to_goal": 2.0,
        },
        memory_items=12,
        tool_calls=4,
        risk_series=[0.2, 0.3, 0.35],
    )
    expected = [
        "structure_clustering",
        "structure_modularity",
        "structure_shortest_path",
        "structure_path_pressure",
        "stability_derivative",
        "stability_is_stable",
        "stability_score",
        "regularization_l1",
        "regularization_l2",
        "regularization_total",
        "structure_compute_backend",
        "structure_compute_path",
    ]
    for key in expected:
        assert key in result


def test_compute_structure_stability_snapshot_backend_python() -> None:
    result = compute_structure_stability_snapshot(
        context_debug={},
        memory_items=0,
        tool_calls=0,
        risk_series=[],
    )
    assert result["structure_compute_backend"] == "python"
    assert result["structure_compute_path"] == "primary"
