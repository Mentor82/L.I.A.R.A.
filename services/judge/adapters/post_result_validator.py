"""Post-result judge adapter bridging ResponseValidator into JudgeDecision."""

from __future__ import annotations

from services.contracts import ValidationContext
from services.judge.contracts import JudgeCheckResult, JudgeContext, JudgeDecision, JudgeDecisionType, JudgeStage


def _map_decision(decision: str) -> JudgeDecisionType:
    mapping = {
        "accept": JudgeDecisionType.ALLOW,
        "warn": JudgeDecisionType.WARN,
        "revise": JudgeDecisionType.REVISE,
        "block": JudgeDecisionType.BLOCK,
    }
    return mapping.get(decision, JudgeDecisionType.REVISE)


def evaluate_post_result_validator(context: JudgeContext) -> JudgeDecision:
    """Evaluate final answer quality/safety and map to unified judge contract."""
    # Late import to avoid circular dependency
    from services.orchestrator.validator import ResponseValidator
    
    if context.stage != JudgeStage.POST_RESULT:
        return JudgeDecision.block(
            confidence=0.0,
            checks=[
                JudgeCheckResult(
                    check="stage",
                    status="fail",
                    severity="high",
                    reason_code="judge.stage.invalid",
                    message="post_result validator adapter requires stage=post_result",
                )
            ],
            issues=["Invalid judge stage for post-result validator adapter."],
        )

    payload = context.input or {}
    original_query = str(payload.get("original_query") or "")
    response = str(payload.get("response") or "")
    tools_used = payload.get("tools_used") or []
    tool_outputs = payload.get("tool_outputs") or {}
    context_mode = str(payload.get("context_mode") or "NONE")
    context_sources = payload.get("context_sources") or {}
    user_feedback_score = payload.get("user_feedback_score")
    user_feedback_stars = payload.get("user_feedback_stars")
    evidence_states = payload.get("evidence_states")

    validation_context = ValidationContext(
        original_query=original_query,
        response=response,
        tools_used=list(tools_used) if isinstance(tools_used, list) else [],
        tool_outputs=tool_outputs if isinstance(tool_outputs, dict) else {},
        context_mode=context_mode,
        context_sources=context_sources if isinstance(context_sources, dict) else {},
        user_feedback_score=(float(user_feedback_score) if isinstance(user_feedback_score, (int, float)) else None),
        user_feedback_stars=(int(user_feedback_stars) if isinstance(user_feedback_stars, (int, float)) else None),
        evidence_states=list(evidence_states) if isinstance(evidence_states, list) else [],
    )

    strict_mode = bool(context.metadata.get("strict_mode", False))
    validator = ResponseValidator(strict_mode=strict_mode)
    result = validator.validate(validation_context)

    constraint_payload = {
        "validator_decision": result.decision,
        "validator_score": (result.score.model_dump() if result.score is not None else None),
        "risk_flags": list(result.risk_flags or []),
    }

    checks = [
        JudgeCheckResult(
            check=name,
            status=status,
            severity="medium" if status == "fail" else "low",
            reason_code=(f"judge.validator.{name}.failed" if status == "fail" else None),
            message=(f"Validator check '{name}' failed" if status == "fail" else None),
        )
        for name, status in result.checks.items()
    ]

    decision_type = _map_decision(result.decision)
    if decision_type == JudgeDecisionType.ALLOW:
        return JudgeDecision.allow(
            confidence=float(result.confidence_score),
            checks=checks,
            issues=list(result.issues),
            constraints=constraint_payload,
        )
    if decision_type == JudgeDecisionType.WARN:
        return JudgeDecision.warn(
            confidence=float(result.confidence_score),
            checks=checks,
            issues=list(result.issues),
            constraints={**constraint_payload, "suggestions": result.suggestions or []},
        )
    if decision_type == JudgeDecisionType.REVISE:
        return JudgeDecision.revise(
            confidence=float(result.confidence_score),
            checks=checks,
            issues=list(result.issues),
            constraints={**constraint_payload, "suggestions": result.suggestions or []},
        )
    return JudgeDecision.block(
        confidence=float(result.confidence_score),
        checks=checks,
        issues=list(result.issues),
        constraints={**constraint_payload, "suggestions": result.suggestions or []},
    )
