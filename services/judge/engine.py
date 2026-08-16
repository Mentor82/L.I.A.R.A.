"""JudgeEngine: routes judge contexts to the appropriate adapters."""

from __future__ import annotations

import os

from dataclasses import replace

from services.judge.adapters import (
    evaluate_post_result_validator,
    evaluate_pre_action_simulation,
    evaluate_pre_action_sys,
    evaluate_pre_action_compute_generate,
    evaluate_pre_action_simulation_mode,
    evaluate_pre_action_orientation,
    evaluate_pre_action_plot_chart,
    evaluate_pre_action_wsl_session,
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
        # Only run if NOT in simulation mode.
        #
        # context.action is a comma-joined list of tool names when more than
        # one tool is selected in a turn (create_judge_context_for_pre_action
        # builds it as ",".join(tool_names)). Evaluating that combined string
        # against single-action profiles below would never match any of
        # them, so every multi-tool turn fell through to the default
        # "no profile found" block regardless of which real, individually-
        # profiled tools were actually involved. Splitting and evaluating
        # each tool name against its own profile fixes that while keeping
        # the fail-closed default for any genuinely unrecognized tool name.
        sub_actions = [part.strip() for part in context.action.split(",") if part.strip()]
        if not sub_actions:
            sub_actions = [context.action]

        decisions = [self._evaluate_single_action(context, action) for action in sub_actions]
        return self._merge_all(decisions)

    def _evaluate_single_action(self, context: JudgeContext, action: str) -> JudgeDecision:
        """Route one tool name to its pre-action profile, with that tool's
        own input.

        `context` carries the shared metadata for the whole turn, but a
        multi-tool turn's `context.input` is only ever one flat payload
        (create_judge_context_for_pre_action sets it from a single prepared
        request). Every sub-action used to see that same shared input
        regardless of which tool it actually belonged to -- e.g. `sys`
        could see `wsl_session`'s parameters or vice versa, order-dependent
        on which tool happened to be prepared first. `metadata.per_tool_
        parameters` (set by tool_discovery.execute_tools) maps each real
        tool name to the parameters actually prepared for it; resolve the
        matching entry here so each profile evaluates its own tool's input,
        falling back to the shared context.input when no per-tool entry
        exists (single-tool turns, or callers not populating the map).
        """
        per_tool = context.metadata.get("per_tool_parameters") if isinstance(context.metadata, dict) else None
        resolved_input = per_tool.get(action) if isinstance(per_tool, dict) and action in per_tool else context.input

        single_context = replace(context, action=action, input=resolved_input)

        if action in {"sys", "/sys"}:
            base_decision = evaluate_pre_action_sys(single_context)
            reward_decision = None
            if self.reward_pre_adapter is not None:
                reward_decision = self.reward_pre_adapter.evaluate_with_reward_score(
                    action=action,
                    input_data=resolved_input or {},
                    context=context.metadata or {},
                )
            return self._merge_decisions(base_decision, reward_decision)
        if action in {"compute.run", "compute/run"}:
            base_decision = evaluate_pre_action_simulation(single_context)
            reward_decision = None
            if self.reward_pre_adapter is not None:
                reward_decision = self.reward_pre_adapter.evaluate_with_reward_score(
                    action=action,
                    input_data=resolved_input or {},
                    context=context.metadata or {},
                )
            return self._merge_decisions(base_decision, reward_decision)
        if action in {"compute.generate", "compute/generate"}:
            base_decision = evaluate_pre_action_compute_generate(single_context)
            reward_decision = None
            if self.reward_pre_adapter is not None:
                reward_decision = self.reward_pre_adapter.evaluate_with_reward_score(
                    action=action,
                    input_data=resolved_input or {},
                    context=context.metadata or {},
                )
            return self._merge_decisions(base_decision, reward_decision)
        if action == "orientation":
            return evaluate_pre_action_orientation(single_context)
        if action == "plot_chart":
            return evaluate_pre_action_plot_chart(single_context)
        if action == "wsl_session":
            return evaluate_pre_action_wsl_session(single_context)

        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="profile_resolution",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No pre-action judge profile for action '{action}'.",
                )
            ],
            issues=["No pre-action judge profile found for action."],
        )

    @staticmethod
    def _merge_all(decisions: list[JudgeDecision]) -> JudgeDecision:
        """Combine per-tool pre-action decisions: the most restrictive wins.

        Mirrors _merge_decisions' rank-based reduction, generalized from one
        optional secondary decision to N per-tool decisions from a
        multi-tool turn.
        """
        if len(decisions) == 1:
            return decisions[0]

        winner = decisions[0]
        for candidate in decisions[1:]:
            if _DECISION_RANK[candidate.decision] > _DECISION_RANK[winner.decision]:
                winner = candidate

        checks = [check for decision in decisions for check in decision.checks]
        issues = list(dict.fromkeys(issue for decision in decisions for issue in decision.issues))
        confidence = min(float(decision.confidence) for decision in decisions)

        return JudgeDecision(
            decision=winner.decision,
            passed=winner.passed,
            confidence=confidence,
            checks=checks,
            issues=issues,
            constraints=dict(winner.constraints or {}),
            reason_code=winner.reason_code,
            simulated=any(decision.simulated for decision in decisions),
            next_action=winner.next_action,
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
