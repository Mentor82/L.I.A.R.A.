"""Pre-action judge adapter for the sys tool class."""

from __future__ import annotations

from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeStage
from services.tools.builtin.wsl_executor import _policy_check

_SYS_ACTIONS = {"sys", "/sys"}


def evaluate_pre_action_sys(context: JudgeContext) -> JudgeDecision:
    """Validate /sys requests under the unified pre-action judge contract."""
    checks: list[JudgeCheckResult] = []

    if context.stage != JudgeStage.PRE_ACTION:
        return JudgeDecision.block(
            checks=[
                JudgeCheckResult(
                    check="stage",
                    status="fail",
                    severity="high",
                    reason_code="judge.stage.invalid",
                    message="sys pre-action adapter requires stage=pre_action",
                )
            ],
            issues=["Invalid judge stage for sys pre-action adapter."],
            confidence=0.0,
        )

    if context.action not in _SYS_ACTIONS:
        return JudgeDecision.block(
            checks=[
                JudgeCheckResult(
                    check="adapter_scope",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No sys profile for action '{context.action}'.",
                )
            ],
            issues=["Action is not covered by the sys pre-action profile."],
            confidence=0.0,
        )

    payload = context.input or {}
    command = str(payload.get("command") or "").strip()
    args_raw = payload.get("args")
    workdir = str(payload.get("workdir") or "/home/liara/workspace")

    if not command:
        return JudgeDecision.revise(
            checks=[
                JudgeCheckResult(
                    check="input_shape",
                    status="fail",
                    severity="medium",
                    reason_code="judge.sys.command_missing",
                    message="'command' is required for sys execution.",
                )
            ],
            issues=["sys payload must include a non-empty 'command'."],
            confidence=0.45,
            constraints={"expected_schema": {"command": "str", "args": "list[str] (optional)"}},
        )

    args: list[str] | None = None
    if args_raw is not None:
        if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
            return JudgeDecision.revise(
                checks=[
                    JudgeCheckResult(
                        check="input_shape",
                        status="fail",
                        severity="medium",
                        reason_code="judge.sys.args_invalid",
                        message="'args' must be list[str] when provided.",
                    )
                ],
                issues=["sys payload field 'args' must be list[str]."],
                confidence=0.45,
            )
        args = list(args_raw)

    if not workdir.startswith("/home/liara/"):
        checks.append(
            JudgeCheckResult(
                check="scope",
                status="fail",
                severity="high",
                reason_code="judge.sys.workdir_outside_scope",
                message=f"workdir '{workdir}' is outside allowed /home/liara/ scope.",
            )
        )
        return JudgeDecision.block(
            checks=checks,
            issues=["sys workdir outside allowed scope."],
            confidence=0.0,
            constraints={"allowed_workdir_prefix": "/home/liara/"},
        )

    policy_error = _policy_check(command, args=args)
    checks.append(
        JudgeCheckResult(
            check="policy",
            status="pass" if policy_error is None else "fail",
            severity="low" if policy_error is None else "critical",
            reason_code=None if policy_error is None else "judge.sys.policy_denied",
            message=policy_error,
        )
    )

    if policy_error is not None:
        return JudgeDecision.block(
            checks=checks,
            issues=[policy_error],
            confidence=0.0,
            constraints={"action": "sys", "command": command},
        )

    # Structured mode is preferred for deterministic policy parsing.
    if args is None:
        return JudgeDecision.warn(
            checks=checks,
            issues=["Legacy shell-string mode detected; prefer structured command+args."],
            confidence=0.8,
            constraints={"preferred_mode": "structured", "allowed": True},
        )

    return JudgeDecision.allow(
        checks=checks,
        confidence=0.95,
        constraints={"workdir": workdir, "mode": "structured"},
    )
