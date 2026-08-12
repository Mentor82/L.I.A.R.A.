"""JudgeEngine: routes judge contexts to the appropriate adapters."""

from __future__ import annotations

import os

from services.judge.adapters import (
    evaluate_post_result_validator,
    evaluate_pre_action_simulation,
    evaluate_pre_action_sys,
    evaluate_pre_action_compute_generate,
    evaluate_pre_action_simulation_mode,
)
from services.config import Settings
from services.judge.adapters.reward_model_post_action_adapter import RewardModelPostActionAdapter
from services.judge.adapters.reward_model_pre_action_adapter import RewardModelPreActionAdapter
from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeDecisionType, JudgeStage
from services.reward_model.scorer import RewardModelScorer


_DECISION_RANK = {
    JudgeDecisionType.ALLOW: 0,
    JudgeDecisionType.WARN: 1,
    JudgeDecisionType.REVISE: 2,
    JudgeDecisionType.BLOCK: 3,
}


class JudgeEngine:
    """Minimal judge pipeline dispatcher for v1 adapters."""

    def __init__(self) -> None:
        self.reward_judge_enabled = bool(getattr(Settings, "REWARD_JUDGE_ENABLED", True))
        self.reward_pre_adapter: RewardModelPreActionAdapter | None = None
        self.reward_post_adapter: RewardModelPostActionAdapter | None = None
        self._init_reward_adapters()

    def _init_reward_adapters(self) -> None:
        if not self.reward_judge_enabled:
            return

        configured_path = (getattr(Settings, "REWARD_JUDGE_MODEL_PATH", "") or "").strip()
        fallback_path = (getattr(Settings, "REWARD_MODEL_PATH", "") or "").strip()
        model_path = configured_path or fallback_path
        if not model_path:
            return
        if not os.path.exists(model_path):
            return

        try:
            from services.reward_model.reward_model import RewardModel

            model = RewardModel.load(model_path)
            scorer = RewardModelScorer(model=model)
            if scorer.is_ready:
                self.reward_pre_adapter = RewardModelPreActionAdapter(scorer=scorer)
                self.reward_post_adapter = RewardModelPostActionAdapter(scorer=scorer)
        except Exception:
            # Graceful fallback to policy-only judge if model loading fails.
            self.reward_pre_adapter = None
            self.reward_post_adapter = None

    @staticmethod
    def _merge_decisions(primary: JudgeDecision, secondary: JudgeDecision | None) -> JudgeDecision:
        if secondary is None:
            return primary

        if _DECISION_RANK[secondary.decision] > _DECISION_RANK[primary.decision]:
            winner = secondary
        else:
            winner = primary

        checks = [*primary.checks, *secondary.checks]
        issues = list(dict.fromkeys([*primary.issues, *secondary.issues]))
        constraints = dict(primary.constraints or {})
        constraints["reward_model"] = dict(secondary.constraints or {})

        return JudgeDecision(
            decision=winner.decision,
            passed=winner.passed,
            confidence=min(float(primary.confidence), float(secondary.confidence)),
            checks=checks,
            issues=issues,
            constraints=constraints,
            reason_code=winner.reason_code,
            simulated=primary.simulated,
            next_action=winner.next_action,
        )

    def evaluate_pre_action(self, context: JudgeContext) -> JudgeDecision:
        if context.stage != JudgeStage.PRE_ACTION:
            return JudgeDecision.block(
                confidence=0.0,
                checks=[
                    JudgeCheckResult(
                        check="stage",
                        status="fail",
                        severity="high",
                        reason_code="judge.stage.invalid",
                        message="evaluate_pre_action requires stage=pre_action",
                    )
                ],
                issues=["Invalid stage for pre-action evaluation."],
            )

        # === Safe Simulation Mode Gate ===
        # Check if we're in simulation mode (no actual execution)
        simulation_mode_decision = evaluate_pre_action_simulation_mode(context)
        
        # If in simulation mode, return immediately with simulation constraints
        # This bypasses standard safety checks (but still validates action)
        if context.metadata.get("simulation_mode", False):
            # If simulation mode reports the action is not supported for simulation,
            # return the warning; otherwise allow simulated execution
            if simulation_mode_decision.decision.value in {"block", "revise"}:
                return simulation_mode_decision
            # Otherwise, continue with simulation constraints
            return simulation_mode_decision

        # === Standard Pre-Action Profiles ===
        # Only run if NOT in simulation mode
        if context.action in {"sys", "/sys"}:
            base_decision = evaluate_pre_action_sys(context)
            reward_decision = None
            if self.reward_pre_adapter is not None:
                reward_decision = self.reward_pre_adapter.evaluate_with_reward_score(
                    action=context.action,
                    input_data=context.input or {},
                    context=context.metadata or {},
                )
            return self._merge_decisions(base_decision, reward_decision)
        if context.action in {"compute.run", "compute/run"}:
            base_decision = evaluate_pre_action_simulation(context)
            reward_decision = None
            if self.reward_pre_adapter is not None:
                reward_decision = self.reward_pre_adapter.evaluate_with_reward_score(
                    action=context.action,
                    input_data=context.input or {},
                    context=context.metadata or {},
                )
            return self._merge_decisions(base_decision, reward_decision)
        if context.action in {"compute.generate", "compute/generate"}:
            base_decision = evaluate_pre_action_compute_generate(context)
            reward_decision = None
            if self.reward_pre_adapter is not None:
                reward_decision = self.reward_pre_adapter.evaluate_with_reward_score(
                    action=context.action,
                    input_data=context.input or {},
                    context=context.metadata or {},
                )
            return self._merge_decisions(base_decision, reward_decision)

        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="profile_resolution",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No pre-action judge profile for action '{context.action}'.",
                )
            ],
            issues=["No pre-action judge profile found for action."],
        )

    def evaluate_post_result(self, context: JudgeContext) -> JudgeDecision:
        if context.stage != JudgeStage.POST_RESULT:
            return JudgeDecision.block(
                confidence=0.0,
                checks=[
                    JudgeCheckResult(
                        check="stage",
                        status="fail",
                        severity="high",
                        reason_code="judge.stage.invalid",
                        message="evaluate_post_result requires stage=post_result",
                    )
                ],
                issues=["Invalid stage for post-result evaluation."],
            )

        base_decision = evaluate_post_result_validator(context)
        reward_decision = None
        if self.reward_post_adapter is not None:
            payload = context.input or {}
            reward_decision = self.reward_post_adapter.evaluate_with_reward_score(
                action=str(context.action),
                input_data=payload,
                result=payload,
                context=context.metadata or {},
            )

        return self._merge_decisions(base_decision, reward_decision)
