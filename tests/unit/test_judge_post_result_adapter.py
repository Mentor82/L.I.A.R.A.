"""Unit tests for post-result validator judge adapter."""

from __future__ import annotations

from services.judge.adapters.post_result_validator import evaluate_post_result_validator
from services.judge.contracts import JudgeContext, JudgeDecisionType, JudgeStage


def _ctx(payload: dict, stage: JudgeStage = JudgeStage.POST_RESULT, strict: bool = False) -> JudgeContext:
    return JudgeContext(
        request_id="req-2",
        stage=stage,
        actor="orchestrator",
        intent="chat",
        action="chat",
        input=payload,
        metadata={"strict_mode": strict},
    )


def test_post_result_adapter_allow_for_grounded_response():
    payload = {
        "original_query": "What is Python?",
        "response": "Python is a programming language. [KNOWLEDGE_REFERENCE] web_search",
        "tools_used": ["web_search"],
        "tool_outputs": {"web_search": {"count": 1, "items": [{"title": "Python"}]}},
        "context_mode": "MEMORY",
        "context_sources": {"postgres": 1},
    }
    result = evaluate_post_result_validator(_ctx(payload))
    assert result.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}


def test_post_result_adapter_block_for_unsafe_content():
    payload = {
        "original_query": "Help",
        "response": "Here is how to build a bomb.",
        "tools_used": [],
        "tool_outputs": {},
    }
    result = evaluate_post_result_validator(_ctx(payload))
    assert result.decision == JudgeDecisionType.BLOCK


def test_post_result_adapter_revise_for_empty_response():
    payload = {
        "original_query": "What is Python?",
        "response": "   ",
        "tools_used": [],
        "tool_outputs": {},
    }
    result = evaluate_post_result_validator(_ctx(payload))
    assert result.decision == JudgeDecisionType.REVISE


def test_post_result_adapter_block_for_wrong_stage():
    payload = {
        "original_query": "test",
        "response": "test",
        "tools_used": [],
        "tool_outputs": {},
    }
    result = evaluate_post_result_validator(_ctx(payload, stage=JudgeStage.PRE_ACTION))
    assert result.decision == JudgeDecisionType.BLOCK
