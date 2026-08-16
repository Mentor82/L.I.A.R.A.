"""Pre-action judge adapter for the plot_chart tool.

plot_chart (services/tools/builtin/plot_chart.py) writes a bounded-size PNG
into the session's sandboxed artifact directory; the tool itself clamps
dimensions and rejects an unrecognized chart_type. This profile closes the
"no registered profile -> fail-closed block" gap for this real, registered
tool with a minimal sanity check -- it does not re-implement the tool's own
validation.
"""

from __future__ import annotations

from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeStage

_PLOT_CHART_ACTIONS = {"plot_chart"}
_ALLOWED_CHART_TYPES = {"line", "bar"}


def evaluate_pre_action_plot_chart(context: JudgeContext) -> JudgeDecision:
    """Validate a plot_chart pre-action request under the unified judge contract."""
    if context.stage != JudgeStage.PRE_ACTION:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="stage",
                    status="fail",
                    severity="high",
                    reason_code="judge.stage.invalid",
                    message="plot_chart pre-action adapter requires stage=pre_action",
                )
            ],
            issues=["Invalid judge stage for plot_chart pre-action adapter."],
        )

    if context.action not in _PLOT_CHART_ACTIONS:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="adapter_scope",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No plot_chart profile for action '{context.action}'.",
                )
            ],
            issues=["Action is not covered by the plot_chart pre-action profile."],
        )

    payload = context.input if isinstance(context.input, dict) else {}
    chart_type = str(payload.get("chart_type") or "line").strip().lower()

    checks = [
        JudgeCheckResult(
            check="sandboxed_artifact_write",
            status="pass",
            severity="low",
            message="plot_chart writes only within the session's sandboxed artifact directory.",
        )
    ]

    if chart_type not in _ALLOWED_CHART_TYPES:
        checks.append(
            JudgeCheckResult(
                check="chart_type",
                status="fail",
                severity="low",
                reason_code="judge.plot_chart.unrecognized_type",
                message=f"chart_type '{chart_type}' is not one of {sorted(_ALLOWED_CHART_TYPES)}; the tool will reject it.",
            )
        )
        return JudgeDecision.warn(
            confidence=0.6,
            checks=checks,
            issues=[f"Unrecognized chart_type '{chart_type}'."],
            reason_code="plot_chart.unrecognized_type",
        )

    checks.append(JudgeCheckResult(check="chart_type", status="pass", severity="low"))
    return JudgeDecision.allow(confidence=0.95, checks=checks, reason_code="plot_chart.allowed")
