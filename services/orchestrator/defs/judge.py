from typing import Any, List

from services.judge import JudgeContext, JudgeStage


def create_judge_context_for_pre_action(
    orchestrator: Any,
    *,
    run_id: str,
    tool_names: List[str],
    query: str,
    **kwargs: Any,
) -> JudgeContext:
    """Create a JudgeContext for pre-action evaluation of tool dispatch."""
    input_payload = kwargs.get("input")
    if input_payload is None:
        if "command" in kwargs:
            input_payload = {"command": kwargs["command"], "args": kwargs.get("args")}
        else:
            input_payload = {"tools": tool_names, "query": query}

    return JudgeContext(
        request_id=run_id,
        stage=JudgeStage.PRE_ACTION,
        actor="orchestrator",
        intent="tool_dispatch",
        action=",".join(tool_names),
        input=input_payload,
        metadata={
            "source": "orchestrator",
            "risk_hint": "medium" if len(tool_names) > 1 else "low",
            "session_id": getattr(orchestrator, "_active_session_id", None),
            "user_id": getattr(orchestrator, "_active_user_id", None),
        },
    )


def create_judge_context_for_post_result(
    orchestrator: Any = None,
    *,
    run_id: str = "",
    query: str = "",
    response_content: str = "",
    tools_used: List[str] = None,
    tool_outputs: dict[str, Any] = None,
    evidence_states: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> JudgeContext:
    """Create a JudgeContext for post-result evaluation of LLM response.

    evidence_states (Issue #8) is threaded through additively so Judge is
    never blind to the evidence-state classification, even though the
    redundant second ResponseValidator.validate() call this ultimately
    reaches doesn't drive the orchestrator's actual accept/revise/block
    control flow (that's the direct validate_response() call site).
    """
    effective_run_id = run_id or kwargs.get("request_id") or getattr(orchestrator, "_active_run_id", "") or ""
    return JudgeContext(
        request_id=effective_run_id,
        stage=JudgeStage.POST_RESULT,
        actor="orchestrator",
        intent="response_validation",
        action="validate_response",
        input={
            "original_query": query,
            "response": response_content,
            "tools_used": tools_used or [],
            "tool_outputs": tool_outputs or {},
            "evidence_states": evidence_states or [],
        },
        metadata={
            "source": "orchestrator",
            "risk_hint": "medium",
            "session_id": getattr(orchestrator, "_active_session_id", None),
            "user_id": getattr(orchestrator, "_active_user_id", None),
            "response_length": len(response_content or ""),
        },
    )


def enrich_judge_post_payload(
    judge_post: dict[str, Any],
    math_signals: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(judge_post or {})
    signals = dict(math_signals or {})
    payload["probabilistic_signals"] = {
        "belief_posterior": signals.get("belief_posterior"),
        "signal_confidence": signals.get("signal_confidence"),
        "utility_ig": signals.get("utility_ig"),
        "stability_score": signals.get("stability_score"),
        "decision_pareto_status": signals.get("decision_pareto_status"),
        "decision_dominant_objective": signals.get("decision_dominant_objective"),
    }
    return payload


def serialize_judge_decision(decision: Any) -> dict[str, Any]:
    if decision is None:
        return {}

    checks = getattr(decision, "checks", []) or []
    return {
        "decision": str(getattr(getattr(decision, "decision", None), "value", getattr(decision, "decision", "allow"))),
        "passed": bool(getattr(decision, "passed", True)),
        "confidence": round(float(getattr(decision, "confidence", 0.0) or 0.0), 6),
        "reason_code": getattr(decision, "reason_code", None),
        "issues": list(getattr(decision, "issues", []) or []),
        "next_action": str(getattr(decision, "next_action", "continue") or "continue"),
        "constraints": dict(getattr(decision, "constraints", {}) or {}),
        "check_count": len(checks),
    }
