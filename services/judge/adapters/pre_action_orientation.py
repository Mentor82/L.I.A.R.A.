"""Pre-action judge adapter for the orientation (self-description) tool.

orientation (services/tools/builtin/orientation.py) is a pure read-only
introspection tool: no required parameters, no side effects, nothing that
can be misused. This profile exists to close the "no registered profile ->
fail-closed block" gap for this real, registered tool, not because the
action needs active safety gating.
"""

from __future__ import annotations

from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeStage

_ORIENTATION_ACTIONS = {"orientation"}


def evaluate_pre_action_orientation(context: JudgeContext) -> JudgeDecision:
    """Validate an orientation pre-action request under the unified judge contract."""
    if context.stage != JudgeStage.PRE_ACTION:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="stage",
                    status="fail",
                    severity="high",
                    reason_code="judge.stage.invalid",
                    message="orientation pre-action adapter requires stage=pre_action",
                )
            ],
            issues=["Invalid judge stage for orientation pre-action adapter."],
        )

    if context.action not in _ORIENTATION_ACTIONS:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="adapter_scope",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No orientation profile for action '{context.action}'.",
                )
            ],
            issues=["Action is not covered by the orientation pre-action profile."],
        )

    return JudgeDecision.allow(
        confidence=1.0,
        checks=[
            JudgeCheckResult(
                check="orientation_read_only",
                status="pass",
                severity="low",
                message="orientation is a read-only, parameter-free introspection tool.",
            )
        ],
        reason_code="orientation.allowed",
    )
