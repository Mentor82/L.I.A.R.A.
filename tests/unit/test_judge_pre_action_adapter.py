"""Unit tests for sys pre-action judge adapter."""

from __future__ import annotations

from services.judge.adapters.pre_action_sys import evaluate_pre_action_sys
from services.judge.contracts import JudgeContext, JudgeDecisionType, JudgeStage


def _ctx(action: str = "sys", payload: dict | None = None, stage: JudgeStage = JudgeStage.PRE_ACTION) -> JudgeContext:
    return JudgeContext(
        request_id="req-1",
        stage=stage,
        actor="orchestrator",
        intent="sys",
        action=action,
        input=payload or {},
    )


def test_sys_pre_action_allow_with_structured_safe_command():
    context = _ctx(payload={"command": "ls", "args": ["-l", "/home/liara/workspace"]})
    result = evaluate_pre_action_sys(context)
    assert result.decision == JudgeDecisionType.ALLOW


def test_sys_pre_action_warn_on_legacy_shell_string_mode():
    context = _ctx(payload={"command": "ls -la /home/liara/workspace"})
    result = evaluate_pre_action_sys(context)
    assert result.decision == JudgeDecisionType.WARN


def test_sys_pre_action_block_for_policy_violation():
    context = _ctx(payload={"command": "rm", "args": ["-rf", "/home/liara/workspace"]})
    result = evaluate_pre_action_sys(context)
    assert result.decision == JudgeDecisionType.BLOCK


def test_sys_pre_action_block_for_outside_workdir_scope():
    context = _ctx(payload={"command": "ls", "args": ["-l"], "workdir": "/tmp"})
    result = evaluate_pre_action_sys(context)
    assert result.decision == JudgeDecisionType.BLOCK


def test_sys_pre_action_revise_for_invalid_args_type():
    context = _ctx(payload={"command": "ls", "args": "-la"})
    result = evaluate_pre_action_sys(context)
    assert result.decision == JudgeDecisionType.REVISE


def test_sys_pre_action_block_for_wrong_stage():
    context = _ctx(payload={"command": "ls"}, stage=JudgeStage.POST_RESULT)
    result = evaluate_pre_action_sys(context)
    assert result.decision == JudgeDecisionType.BLOCK
