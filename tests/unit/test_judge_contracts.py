"""Unit tests for unified judge contracts."""

from __future__ import annotations

import pytest

from services.judge.contracts import (
    JudgeCheckResult,
    JudgeContext,
    JudgeDecision,
    JudgeDecisionType,
    JudgeStage,
)


def test_judge_context_fields_roundtrip():
    ctx = JudgeContext(
        request_id="r-1",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="simulation",
        action="compute.run",
        input={"model": "turbine_power"},
        metadata={"source": "api"},
    )

    assert ctx.request_id == "r-1"
    assert ctx.stage == JudgeStage.PRE_ACTION
    assert ctx.action == "compute.run"
    assert ctx.input["model"] == "turbine_power"


def test_judge_decision_confidence_bounds_enforced():
    with pytest.raises(ValueError):
        JudgeDecision(
            decision=JudgeDecisionType.ALLOW,
            passed=True,
            confidence=1.1,
        )


def test_judge_decision_helpers_map_correctly():
    allow = JudgeDecision.allow(confidence=0.9)
    warn = JudgeDecision.warn(confidence=0.7)
    revise = JudgeDecision.revise(confidence=0.4)
    block = JudgeDecision.block(confidence=0.0)

    assert allow.decision == JudgeDecisionType.ALLOW and allow.passed
    assert warn.decision == JudgeDecisionType.WARN and warn.passed
    assert revise.decision == JudgeDecisionType.REVISE and not revise.passed
    assert block.decision == JudgeDecisionType.BLOCK and not block.passed


def test_judge_check_result_defaults():
    check = JudgeCheckResult(check="policy", status="pass")
    assert check.severity == "low"
    assert check.reason_code is None
