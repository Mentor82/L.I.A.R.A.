from __future__ import annotations

from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeDecisionType, JudgeStage
from services.judge.engine import JudgeEngine


class _BlockRewardPreAdapter:
    def evaluate_with_reward_score(self, action, input_data, context=None):
        return JudgeDecision.block(
            confidence=0.9,
            checks=[
                JudgeCheckResult(
                    check="reward_model_risk_score",
                    status="fail",
                    severity="high",
                    reason_code="reward_model.unsafe_prediction",
                    message="Reward model predicts unsafe input.",
                )
            ],
            issues=["Reward model predicts unsafe action."],
            constraints={"risk_score": 0.95},
        )


class _AllowRewardPreAdapter:
    def evaluate_with_reward_score(self, action, input_data, context=None):
        return JudgeDecision.allow(
            confidence=0.8,
            checks=[
                JudgeCheckResult(
                    check="reward_model_risk_score",
                    status="pass",
                    severity="low",
                    message="Reward model predicts safe input.",
                )
            ],
            constraints={"risk_score": 0.1},
        )


class _BlockRewardPostAdapter:
    def evaluate_with_reward_score(self, action, input_data, result, context=None):
        return JudgeDecision.block(
            confidence=0.88,
            checks=[
                JudgeCheckResult(
                    check="reward_model_response_safety",
                    status="fail",
                    severity="high",
                    reason_code="reward_model.unsafe_prediction",
                    message="Reward model predicts unsafe response.",
                )
            ],
            issues=["Reward model detects unsafe response content."],
            constraints={"risk_score": 0.92},
        )


def test_pre_action_reward_block_overrides_policy_allow():
    engine = JudgeEngine()
    engine.reward_pre_adapter = _BlockRewardPreAdapter()

    ctx = JudgeContext(
        request_id="r-reward-1",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="tool_dispatch",
        action="sys",
        input={"command": "ls", "args": ["-la", "/home/liara/workspace"]},
        metadata={"source": "test"},
    )
    decision = engine.evaluate_pre_action(ctx)

    assert decision.decision == JudgeDecisionType.BLOCK
    assert any(check.check == "reward_model_risk_score" for check in decision.checks)
    assert "reward_model" in decision.constraints


def test_pre_action_policy_block_stays_block_even_if_reward_allows():
    engine = JudgeEngine()
    engine.reward_pre_adapter = _AllowRewardPreAdapter()

    ctx = JudgeContext(
        request_id="r-reward-2",
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="tool_dispatch",
        action="unknown_tool",
        input={},
        metadata={"source": "test"},
    )
    decision = engine.evaluate_pre_action(ctx)

    assert decision.decision == JudgeDecisionType.BLOCK


def test_post_result_reward_block_overrides_validator_allow():
    engine = JudgeEngine()
    engine.reward_post_adapter = _BlockRewardPostAdapter()

    ctx = JudgeContext(
        request_id="r-reward-3",
        stage=JudgeStage.POST_RESULT,
        actor="orchestrator",
        intent="response_validation",
        action="validate_response",
        input={
            "original_query": "List workspace files",
            "response": "Workspace contains docs, services, and tests.",
            "tools_used": ["sys"],
            "tool_outputs": {"sys": "ok"},
        },
        metadata={"source": "test", "strict_mode": False},
    )
    decision = engine.evaluate_post_result(ctx)

    assert decision.decision == JudgeDecisionType.BLOCK
    assert any(check.check == "reward_model_response_safety" for check in decision.checks)
    assert "reward_model" in decision.constraints
