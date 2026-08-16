"""Pre-action judge adapter for the wsl_session tool.

wsl_session (services/tools/builtin/wsl_session.py) manages isolated
native-WSL simulation session lifecycle: plan, create, status, collect,
validate, destroy. This profile enumerates the tool's own known lifecycle
actions (mirroring how pre_action_sys.py gates known/unknown commands) so
an unrecognized action is rejected before it reaches the session manager,
and closes the "no registered profile -> fail-closed block" gap for this
real, registered tool.
"""

from __future__ import annotations

from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeStage

_WSL_SESSION_ACTIONS = {"wsl_session"}
_KNOWN_LIFECYCLE_ACTIONS = {"plan", "create", "status", "collect", "validate", "destroy"}


def evaluate_pre_action_wsl_session(context: JudgeContext) -> JudgeDecision:
    """Validate a wsl_session pre-action request under the unified judge contract."""
    if context.stage != JudgeStage.PRE_ACTION:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="stage",
                    status="fail",
                    severity="high",
                    reason_code="judge.stage.invalid",
                    message="wsl_session pre-action adapter requires stage=pre_action",
                )
            ],
            issues=["Invalid judge stage for wsl_session pre-action adapter."],
        )

    if context.action not in _WSL_SESSION_ACTIONS:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="adapter_scope",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No wsl_session profile for action '{context.action}'.",
                )
            ],
            issues=["Action is not covered by the wsl_session pre-action profile."],
        )

    payload = context.input if isinstance(context.input, dict) else {}
    lifecycle_action = str(payload.get("action") or "").strip().lower()

    if not lifecycle_action:
        return JudgeDecision.revise(
            confidence=0.3,
            checks=[
                JudgeCheckResult(
                    check="lifecycle_action_present",
                    status="fail",
                    severity="medium",
                    reason_code="judge.wsl_session.action_missing",
                    message="wsl_session requires a non-empty 'action' field.",
                )
            ],
            issues=["wsl_session call is missing the required 'action' field."],
        )

    if lifecycle_action not in _KNOWN_LIFECYCLE_ACTIONS:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="lifecycle_action_known",
                    status="fail",
                    severity="high",
                    reason_code="judge.wsl_session.unknown_action",
                    message=f"'{lifecycle_action}' is not a known wsl_session lifecycle action.",
                )
            ],
            issues=[f"Unknown wsl_session action '{lifecycle_action}'."],
        )

    return JudgeDecision.allow(
        confidence=0.9,
        checks=[
            JudgeCheckResult(
                check="lifecycle_action_known",
                status="pass",
                severity="low",
                message=f"'{lifecycle_action}' is a known wsl_session lifecycle action.",
            )
        ],
        reason_code="wsl_session.allowed",
    )
