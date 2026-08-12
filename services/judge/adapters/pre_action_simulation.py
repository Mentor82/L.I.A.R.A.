"""Pre-action judge adapter for the Julia simulation tool class."""

from __future__ import annotations

from services.config import Settings
from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeStage

_REQUIRED_INPUTS: dict[str, list[str]] = {
    "turbine_power": ["shaft_speed_rpm", "torque_nm"],
}

_SIM_ACTIONS = {"compute.run", "compute/run"}


def evaluate_pre_action_simulation(context: JudgeContext) -> JudgeDecision:
    """Validate simulation pre-action request under unified judge rules.

    Rules:
    - Adapter only handles simulation actions.
    - Model must be allowlisted.
    - Inputs must be a JSON object.
    - Known models require mandatory input fields.
    """
    checks: list[JudgeCheckResult] = []

    if context.stage != JudgeStage.PRE_ACTION:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="stage",
                    status="fail",
                    severity="high",
                    reason_code="judge.stage.invalid",
                    message="Simulation pre-action adapter called with non pre_action stage.",
                )
            ],
            issues=["Invalid judge stage for pre-action simulation adapter."],
        )

    if context.action not in _SIM_ACTIONS:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="adapter_scope",
                    status="fail",
                    severity="high",
                    reason_code="judge.profile.not_found",
                    message=f"No simulation profile for action '{context.action}'.",
                )
            ],
            issues=["Action is not covered by the simulation pre-action profile."],
        )

    payload = context.input or {}
    model = str(payload.get("model") or "").strip()
    inputs = payload.get("inputs")

    allowlisted_models = set(Settings.julia_allowlist())
    checks.append(
        JudgeCheckResult(
            check="allowlist",
            status="pass" if model in allowlisted_models else "fail",
            severity="critical" if model not in allowlisted_models else "low",
            reason_code=None if model in allowlisted_models else "judge.simulation.model_not_allowlisted",
            message=None if model in allowlisted_models else f"Model '{model or '<empty>'}' is not allowlisted.",
        )
    )

    if model not in allowlisted_models:
        return JudgeDecision.block(
            confidence=0.0,
            checks=checks,
            issues=[f"Simulation model '{model or '<empty>'}' is not in JULIA_ALLOWLIST."],
            constraints={
                "allowed_models": sorted(allowlisted_models),
                "timeout_seconds": Settings.JULIA_TIMEOUT_SECONDS,
            },
        )

    if not isinstance(inputs, dict):
        checks.append(
            JudgeCheckResult(
                check="input_shape",
                status="fail",
                severity="medium",
                reason_code="judge.simulation.inputs_invalid",
                message="'inputs' must be a JSON object.",
            )
        )
        return JudgeDecision.revise(
            confidence=0.45,
            checks=checks,
            issues=["Simulation input payload must include an object field 'inputs'."],
            constraints={"expected_schema": {"model": "str", "inputs": "object"}},
        )

    required = _REQUIRED_INPUTS.get(model, [])
    missing = [k for k in required if k not in inputs]
    checks.append(
        JudgeCheckResult(
            check="required_inputs",
            status="pass" if not missing else "fail",
            severity="medium" if missing else "low",
            reason_code=None if not missing else "judge.simulation.required_inputs_missing",
            message=None if not missing else f"Missing required inputs: {missing}",
        )
    )

    if missing:
        return JudgeDecision.revise(
            confidence=0.5,
            checks=checks,
            issues=[f"Missing required simulation inputs: {missing}"],
            constraints={"required_inputs": required, "model": model},
        )

    return JudgeDecision.allow(
        confidence=0.95,
        checks=checks,
        constraints={
            "timeout_seconds": Settings.JULIA_TIMEOUT_SECONDS,
            "allowed_models": sorted(allowlisted_models),
        },
    )
