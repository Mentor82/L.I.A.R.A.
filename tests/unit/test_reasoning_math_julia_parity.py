from __future__ import annotations

import os

import pytest

from services.orchestrator.reasoning_math_ext import (
    compute_belief_snapshot,
    compute_decision_snapshot,
    compute_structure_stability_snapshot,
    compute_utility_snapshot,
)
from services.simulation.bridge import JuliaBridge, JuliaBridgeError


def _require_julia_parity_opt_in() -> None:
    if os.getenv("RUN_JULIA_PARITY_TESTS", "0") != "1":
        pytest.skip("Set RUN_JULIA_PARITY_TESTS=1 to run Julia/Python parity tests.")


@pytest.mark.asyncio
async def test_chat_math_accepts_german_natural_language_operator() -> None:
    _require_julia_parity_opt_in()
    bridge = JuliaBridge(allowlist=["chat_math"], timeout_seconds=8.0)

    try:
        result = await bridge.run(
            "chat_math",
            {"query": "Berechne 17 mal 23 und antworte kurz mit dem Ergebnis."},
        )
    except JuliaBridgeError as exc:
        pytest.skip(f"Julia bridge unavailable: {exc}")

    assert result == {"output": "391"}


@pytest.mark.asyncio
async def test_belief_snapshot_julia_python_parity() -> None:
    _require_julia_parity_opt_in()
    bridge = JuliaBridge(allowlist=["belief_snapshot"], timeout_seconds=8.0)

    payload = {
        "belief": {"prior": 0.5, "entropy": 0.2, "variance": 0.01},
        "observation": {"likelihood": 0.7, "signal": 0.8, "entropy": 0.2},
        "signal_window": [0.45, 0.5, 0.55, 0.62],
        "config": {"kalman_gain": 0.3, "min_variance": 1e-4},
    }

    try:
        julia_raw = await bridge.run("belief_snapshot", payload)
    except JuliaBridgeError as exc:
        pytest.skip(f"Julia bridge unavailable: {exc}")

    julia = julia_raw["belief_snapshot"]
    py = compute_belief_snapshot(
        payload["belief"],
        payload["observation"],
        payload["signal_window"],
        kalman_gain=0.3,
        min_variance=1e-4,
    )

    assert abs(julia["belief_posterior"] - py["belief_posterior"]) < 1e-4
    assert abs(julia["belief_estimate"] - py["belief_estimate"]) < 1e-4
    assert abs(julia["signal_confidence"] - py["signal_confidence"]) < 1e-4


@pytest.mark.asyncio
async def test_utility_snapshot_julia_python_parity() -> None:
    _require_julia_parity_opt_in()
    bridge = JuliaBridge(allowlist=["utility_snapshot"], timeout_seconds=8.0)

    payload = {
        "utility": 1.75,
        "entropy_before": 0.6,
        "entropy_after": 0.4,
        "step": 2,
        "gamma": 0.95,
    }

    try:
        julia_raw = await bridge.run("utility_snapshot", payload)
    except JuliaBridgeError as exc:
        pytest.skip(f"Julia bridge unavailable: {exc}")

    julia = julia_raw["utility_snapshot"]
    py = compute_utility_snapshot(
        payload["utility"],
        payload["entropy_before"],
        payload["entropy_after"],
        payload["step"],
        gamma=payload["gamma"],
    )

    assert abs(julia["utility_ig"] - py["utility_ig"]) < 1e-4
    assert abs(julia["utility_weighted"] - py["utility_weighted"]) < 1e-4
    assert abs(julia["utility_discounted"] - py["utility_discounted"]) < 1e-4


@pytest.mark.asyncio
async def test_structure_snapshot_julia_python_parity() -> None:
    _require_julia_parity_opt_in()
    bridge = JuliaBridge(allowlist=["structure_stability_snapshot"], timeout_seconds=8.0)

    payload = {
        "context_debug": {
            "graph_nodes": 7,
            "graph_edges": 9,
            "graph_communities": 2,
            "shortest_path_to_goal": 3.0,
        },
        "memory_items": 12,
        "tool_calls": 4,
        "risk_series": [0.3, 0.45, 0.5],
        "lambda_l1": 0.05,
        "lambda_l2": 0.01,
    }

    try:
        julia_raw = await bridge.run("structure_stability_snapshot", payload)
    except JuliaBridgeError as exc:
        pytest.skip(f"Julia bridge unavailable: {exc}")

    julia = julia_raw["structure_stability_snapshot"]
    py = compute_structure_stability_snapshot(
        context_debug=payload["context_debug"],
        memory_items=payload["memory_items"],
        tool_calls=payload["tool_calls"],
        risk_series=payload["risk_series"],
        lambda_l1=payload["lambda_l1"],
        lambda_l2=payload["lambda_l2"],
    )

    assert abs(julia["structure_clustering"] - py["structure_clustering"]) < 1e-4
    assert abs(julia["stability_score"] - py["stability_score"]) < 1e-4
    assert abs(julia["regularization_total"] - py["regularization_total"]) < 1e-4


@pytest.mark.asyncio
async def test_decision_snapshot_julia_python_parity() -> None:
    _require_julia_parity_opt_in()
    bridge = JuliaBridge(allowlist=["decision_snapshot"], timeout_seconds=8.0)

    payload = {
        "total_cost": 2.5,
        "actionable_risk": 0.8,
        "context_entropy": 0.4,
        "utility_discounted": 0.6,
        "stability_score": 0.7,
        "regularization_total": 0.3,
        "path_pressure": 0.4,
        "mode_floor": "advisory",
        "repair_preferred": False,
        "soft_risk_max": 5.0,
        "hard_risk_max": 8.0,
    }

    try:
        julia_raw = await bridge.run("decision_snapshot", payload)
    except JuliaBridgeError as exc:
        pytest.skip(f"Julia bridge unavailable: {exc}")

    julia = julia_raw["decision_snapshot"]
    py = compute_decision_snapshot(**payload)

    assert julia["decision_recommended_mode"] == py["decision_recommended_mode"]
    assert julia["decision_dominant_objective"] == py["decision_dominant_objective"]
    assert julia["decision_resolution_basis"] == py["decision_resolution_basis"]
