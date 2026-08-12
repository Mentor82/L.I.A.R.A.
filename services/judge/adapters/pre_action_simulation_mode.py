"""Pre-action judge adapter for Safe Simulation Mode.

When simulation_mode=true in metadata, this adapter gates all actions:
- Simulated execution is ALLOWED
- Real execution is BLOCKED
- Planning/decision-making is fully enabled

This enables risk-free tool testing, debugging, and planning without
executing actual tools.
"""

from __future__ import annotations

from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeStage

_SIMULATION_ACTIONS = {
    "sys", "/sys",
    "compute.run", "compute/run",
    "compute.generate", "compute/generate",
    "web_search", "web/search",
    "read_file", "read/file",
    "list_files", "list/files",
}

_SIMPLE_SYS_COMMANDS = {
    "date",
    "time",
    "ls",
    "pwd",
    "echo",
    "whoami",
}


def _resolve_mock_profile(context: JudgeContext) -> str:
    metadata_profile = context.metadata.get("mock_profile")
    input_profile = context.input.get("mock_profile") if isinstance(context.input, dict) else None
    raw = metadata_profile or input_profile or "default"
    return str(raw).strip().lower() or "default"


def _calculate_simulation_confidence(context: JudgeContext, action: str) -> float:
    """Calculate simulation confidence using tool, input complexity, and mock profile."""
    payload = context.input or {}
    mock_profile = _resolve_mock_profile(context)

    if action in {"sys", "/sys"}:
        command = str(payload.get("command") or "").strip().lower()
        args = payload.get("args")
        args_count = len(args) if isinstance(args, list) else 0
        base = 0.99 if command in _SIMPLE_SYS_COMMANDS and args_count <= 2 else 0.95
    elif action in {"compute.run", "compute/run", "compute.generate", "compute/generate"}:
        inputs = payload.get("inputs")
        has_inputs = isinstance(inputs, dict) and bool(inputs)
        base = 0.90 if has_inputs else 0.86
    elif action in _SIMULATION_ACTIONS:
        base = 0.85
    else:
        base = 0.50

    profile_adjustment = {
        "default": 0.0,
        "high_fidelity": 0.02,
        "deterministic": 0.01,
        "medium_fidelity": -0.02,
        "low_fidelity": -0.05,
        "heuristic": -0.05,
    }.get(mock_profile, -0.08)

    return max(0.0, min(1.0, base + profile_adjustment))


def evaluate_pre_action_simulation_mode(context: JudgeContext) -> JudgeDecision:
    """
    Pre-action judge for Safe Simulation Mode.

    When simulation_mode=true:
    - The action is ALLOWED but flagged as simulated
    - Tool coordinator will generate mock results instead of executing
    - Post-result judge will validate the simulated output

    When simulation_mode=false or unset:
    - Returns ALLOW to pass through to standard pre-action adapters
    - Standard safety checks apply

    Args:
        context: JudgeContext with stage=PRE_ACTION and simulation_mode in metadata

    Returns:
        JudgeDecision with simulation constraints or pass-through ALLOW
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
                    message="Simulation mode adapter requires stage=pre_action",
                )
            ],
            issues=["Invalid judge stage for simulation mode adapter."],
        )

    # Extract simulation_mode flag
    simulation_mode = bool(context.metadata.get("simulation_mode", False))
    checks.append(
        JudgeCheckResult(
            check="simulation_mode_flag",
            status="pass",
            severity="low",
            reason_code=None,
            message=f"simulation_mode={simulation_mode}",
        )
    )

    # If not in simulation mode, pass through (other adapters handle the action)
    if not simulation_mode:
        return JudgeDecision.allow(
            confidence=0.9,
            checks=checks,
            constraints={"simulation_mode": False, "action": "pass_through"},
            simulated=False,
            next_action="continue",
        )

    # === Simulation Mode Active ===
    # Check if action is supported for simulation
    action = str(context.action or "").strip()
    if action not in _SIMULATION_ACTIONS:
        checks.append(
            JudgeCheckResult(
                check="simulation_action_support",
                status="fail",
                severity="medium",
                reason_code="judge.simulation.action_not_supported",
                message=f"Action '{action}' is not supported in simulation mode.",
            )
        )
        return JudgeDecision.warn(
            confidence=_calculate_simulation_confidence(context, action),
            checks=checks,
            issues=[f"Action '{action}' cannot be simulated."],
            constraints={
                "simulation_mode": True,
                "supported_actions": sorted(_SIMULATION_ACTIONS),
            },
            reason_code="simulation_mode.active",
            simulated=True,
        )

    checks.append(
        JudgeCheckResult(
            check="simulation_action_support",
            status="pass",
            severity="low",
            reason_code=None,
            message=f"Action '{action}' is supported in simulation mode.",
        )
    )

    # All safety checks for the action still apply, but we mark execution as simulated
    return JudgeDecision.allow(
        confidence=_calculate_simulation_confidence(context, action),
        checks=checks,
        constraints={
            "simulation_mode": True,
            "simulated_execution": True,
            "action": action,
            "skip_actual_execution": True,
            "mock_result_enabled": True,
            "mock_profile": _resolve_mock_profile(context),
        },
        reason_code="simulation_mode.active",
        simulated=True,
        next_action="continue_simulated",
    )
