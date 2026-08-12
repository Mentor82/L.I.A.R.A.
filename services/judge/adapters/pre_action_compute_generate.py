"""
Judge adapter for compute.generate pre-action validation.

Ensures that model generation requests are safe and reasonable:
- Model names are valid and don't conflict
- Inputs/outputs are well-formed
- Prompts don't contain malicious patterns
"""

from services.judge.contracts import JudgeContext, JudgeCheckResult, JudgeDecisionType, JudgeDecision
from services.simulation.registry import GeneratedModelRegistry

__all__ = ["evaluate_pre_action_compute_generate"]


_COMPUTE_GEN_ACTIONS = {"compute.generate", "compute/generate"}


def evaluate_pre_action_compute_generate(context: JudgeContext) -> JudgeDecision:
    """
    Pre-action validation for compute.generate tool.
    
    Args:
        context: JudgeContext with action="compute.generate"
    
    Returns:
        JudgeDecision allowing or blocking the generation request.
    """
    if context.action not in _COMPUTE_GEN_ACTIONS:
        return JudgeDecision.block(
            checks=[
                JudgeCheckResult(
                    check="action_routing",
                    status="fail",
                    severity="high",
                    reason_code="judge.action.mismatch",
                    message=f"Action '{context.action}' does not match compute.generate adapter",
                )
            ],
            issues=["Action routing error."],
        )
    
    registry = GeneratedModelRegistry()
    checks = []
    all_passed = True
    issues = []
    
    # === CHECK 1: Model Name Conflict ===
    model_name = context.input.get("model_name", "").lower().strip()
    
    if not model_name:
        checks.append(JudgeCheckResult(
            check="model_conflict",
            status="fail",
            severity="high",
            reason_code="judge.model.name_empty",
            message="model_name is empty",
        ))
        all_passed = False
        issues.append("Model name is empty.")
    else:
        existing_models = {m["name"] for m in registry.list_models()}
        if model_name in existing_models:
            checks.append(JudgeCheckResult(
                check="model_conflict",
                status="fail",
                severity="high",
                reason_code="judge.model.exists",
                message=f"Model '{model_name}' already exists",
            ))
            all_passed = False
            issues.append(f"Model '{model_name}' already exists. Choose a different name.")
        else:
            checks.append(JudgeCheckResult(
                check="model_conflict",
                status="pass",
                severity="low",
                reason_code="judge.model.available",
                message=f"Model name '{model_name}' is available",
            ))
    
    # === CHECK 2: Input/Output Validity ===
    inputs = context.input.get("inputs", {})
    outputs = context.input.get("outputs", {})
    
    io_valid = True
    io_reasons = []
    
    if not isinstance(inputs, dict) or not inputs:
        io_valid = False
        io_reasons.append("inputs must be a non-empty dict")
    
    if not isinstance(outputs, dict) or not outputs:
        io_valid = False
        io_reasons.append("outputs must be a non-empty dict")
    
    # Check for suspicious parameter names only if inputs/outputs are valid dicts
    if isinstance(inputs, dict) and isinstance(outputs, dict):
        suspicious_patterns = ["file", "exec", "system", "os.", "subprocess", "import"]
        for suspicious in suspicious_patterns:
            for key in list(inputs.keys()) + list(outputs.keys()):
                if suspicious.lower() in key.lower():
                    io_valid = False
                    io_reasons.append(f"Suspicious parameter name: '{key}'")
    
    checks.append(JudgeCheckResult(
        check="input_output_validity",
        status="pass" if io_valid else "fail",
        severity="medium" if not io_valid else "low",
        reason_code="judge.io.invalid" if not io_valid else "judge.io.valid",
        message="; ".join(io_reasons) if io_reasons else "Inputs and outputs are valid",
    ))
    
    if not io_valid:
        all_passed = False
        issues.append("Input/output parameters are invalid or suspicious.")
    
    # === CHECK 3: Prompt Safety ===
    description = context.input.get("description", "").lower()
    
    forbidden_patterns = [
        "delete", "corrupt", "hack", "backdoor", "malware",
        "inject", "exploit", "break", "bypass", "attack",
    ]
    
    prompt_safe = True
    prompt_reasons = []
    
    for pattern in forbidden_patterns:
        if pattern in description:
            prompt_safe = False
            prompt_reasons.append(f"Contains forbidden pattern: '{pattern}'")
    
    checks.append(JudgeCheckResult(
        check="prompt_safety",
        status="pass" if prompt_safe else "fail",
        severity="high" if not prompt_safe else "low",
        reason_code="judge.prompt.unsafe" if not prompt_safe else "judge.prompt.safe",
        message="; ".join(prompt_reasons) if prompt_reasons else "Description is safe",
    ))
    
    if not prompt_safe:
        all_passed = False
        issues.append("Prompt contains unsafe patterns.")
    
    # Return decision
    if all_passed:
        return JudgeDecision.allow(
            checks=checks,
            confidence=0.95,
        )
    else:
        return JudgeDecision.block(
            checks=checks,
            confidence=0.85,
            issues=issues,
        )
