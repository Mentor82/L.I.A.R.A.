"""Unit tests for simulation pre-action judge adapter."""

from __future__ import annotations

import pytest

from services.config import Settings
from services.judge.adapters.pre_action_simulation import evaluate_pre_action_simulation
from services.judge.contracts import JudgeContext, JudgeDecisionType, JudgeStage


@pytest.fixture
def simulation_context() -> JudgeContext:
    return JudgeContext(
        request_id="req-1",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="simulation",
        action="compute.run",
        input={
            "model": "turbine_power",
            "inputs": {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0},
        },
    )


def test_pre_action_simulation_allows_valid_request(monkeypatch, simulation_context):
    monkeypatch.setattr(Settings, "JULIA_ALLOWLIST", "turbine_power")

    decision = evaluate_pre_action_simulation(simulation_context)

    assert decision.decision == JudgeDecisionType.ALLOW
    assert decision.passed is True
    assert decision.constraints["timeout_seconds"] == Settings.JULIA_TIMEOUT_SECONDS


def test_pre_action_simulation_blocks_non_profile_action(monkeypatch, simulation_context):
    monkeypatch.setattr(Settings, "JULIA_ALLOWLIST", "turbine_power")
    simulation_context.action = "web_search"

    decision = evaluate_pre_action_simulation(simulation_context)

    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("profile" in issue.lower() for issue in decision.issues)


def test_pre_action_simulation_blocks_non_allowlisted_model(monkeypatch, simulation_context):
    monkeypatch.setattr(Settings, "JULIA_ALLOWLIST", "some_other_model")

    decision = evaluate_pre_action_simulation(simulation_context)

    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("allowlist" in issue.lower() for issue in decision.issues)


def test_pre_action_simulation_revise_for_missing_required_inputs(monkeypatch, simulation_context):
    monkeypatch.setattr(Settings, "JULIA_ALLOWLIST", "turbine_power")
    simulation_context.input["inputs"] = {"shaft_speed_rpm": 1500.0}

    decision = evaluate_pre_action_simulation(simulation_context)

    assert decision.decision == JudgeDecisionType.REVISE
    assert any("missing" in issue.lower() for issue in decision.issues)


def test_pre_action_simulation_revise_for_invalid_inputs_type(monkeypatch, simulation_context):
    monkeypatch.setattr(Settings, "JULIA_ALLOWLIST", "turbine_power")
    simulation_context.input["inputs"] = "not-an-object"

    decision = evaluate_pre_action_simulation(simulation_context)

    assert decision.decision == JudgeDecisionType.REVISE
    assert any("payload" in issue.lower() for issue in decision.issues)


def test_pre_action_simulation_blocks_invalid_stage(monkeypatch, simulation_context):
    monkeypatch.setattr(Settings, "JULIA_ALLOWLIST", "turbine_power")
    simulation_context.stage = JudgeStage.POST_RESULT

    decision = evaluate_pre_action_simulation(simulation_context)

    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("stage" in issue.lower() for issue in decision.issues)
