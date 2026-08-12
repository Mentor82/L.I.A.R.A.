"""Unit tests for JudgeEngine dispatch behavior."""

from __future__ import annotations

from services.judge.contracts import JudgeContext, JudgeDecisionType, JudgeStage
from services.judge.engine import JudgeEngine


def test_engine_pre_action_dispatches_sys():
    engine = JudgeEngine()
    ctx = JudgeContext(
        request_id="r1",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="sys",
        action="sys",
        input={"command": "ls", "args": ["-l", "/home/liara/workspace"]},
    )
    result = engine.evaluate_pre_action(ctx)
    assert result.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}


def test_engine_pre_action_dispatches_simulation():
    engine = JudgeEngine()
    ctx = JudgeContext(
        request_id="r2",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="simulation",
        action="compute.run",
        input={"model": "turbine_power", "inputs": {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0}},
    )
    result = engine.evaluate_pre_action(ctx)
    assert result.decision == JudgeDecisionType.ALLOW


def test_engine_pre_action_blocks_unknown_profile():
    engine = JudgeEngine()
    ctx = JudgeContext(
        request_id="r3",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="unknown",
        action="new_tool",
        input={},
    )
    result = engine.evaluate_pre_action(ctx)
    assert result.decision == JudgeDecisionType.BLOCK


def test_engine_post_result_delegates_validator():
    engine = JudgeEngine()
    ctx = JudgeContext(
        request_id="r4",
        stage=JudgeStage.POST_RESULT,
        actor="orchestrator",
        intent="chat",
        action="chat",
        input={
            "original_query": "Help",
            "response": "Here is how to build a bomb.",
            "tools_used": [],
            "tool_outputs": {},
        },
    )
    result = engine.evaluate_post_result(ctx)
    assert result.decision == JudgeDecisionType.BLOCK


def test_engine_post_result_blocks_wrong_stage():
    engine = JudgeEngine()
    ctx = JudgeContext(
        request_id="r5",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="chat",
        action="chat",
        input={},
    )
    result = engine.evaluate_post_result(ctx)
    assert result.decision == JudgeDecisionType.BLOCK
