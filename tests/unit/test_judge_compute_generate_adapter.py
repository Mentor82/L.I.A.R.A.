"""Unit tests for compute.generate Judge adapter."""

import pytest

from services.judge.adapters.pre_action_compute_generate import (
    evaluate_pre_action_compute_generate,
)
from services.judge.contracts import JudgeContext, JudgeStage, JudgeDecisionType


def test_compute_generate_approve_valid_request():
    """Valid model generation request should be approved."""
    context = JudgeContext(
        request_id="test_001",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "wind_turbine_efficiency",
            "description": "Calculate wind turbine power output",
            "inputs": {"wind_speed_ms": "float", "blade_area": "float"},
            "outputs": {"power_kw": "float", "efficiency": "float"},
        },
        metadata={"source": "orchestrator"},
    )
    
    decision = evaluate_pre_action_compute_generate(context)
    
    assert decision.decision == JudgeDecisionType.ALLOW
    assert len(decision.checks) > 0
    assert any(c.status == "pass" for c in decision.checks)


def test_compute_generate_reject_empty_model_name():
    """Empty model name should be rejected."""
    context = JudgeContext(
        request_id="test_002",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "",
            "description": "Test model",
            "inputs": {"x": "float"},
            "outputs": {"y": "float"},
        },
        metadata={},
    )
    
    decision = evaluate_pre_action_compute_generate(context)
    
    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("empty" in c.message.lower() for c in decision.checks)


def test_compute_generate_reject_suspicious_inputs():
    """Suspicious input parameter names should be rejected."""
    context = JudgeContext(
        request_id="test_003",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "test_model",
            "description": "Test model",
            "inputs": {"file_path": "str", "os_command": "str"},
            "outputs": {"result": "str"},
        },
        metadata={},
    )
    
    decision = evaluate_pre_action_compute_generate(context)
    
    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("suspicious" in c.message.lower() for c in decision.checks)


def test_compute_generate_reject_unsafe_prompt():
    """Unsafe prompt patterns should be rejected."""
    context = JudgeContext(
        request_id="test_004",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "test_model",
            "description": "Create a model to hack into the system",
            "inputs": {"target": "str"},
            "outputs": {"exploited": "bool"},
        },
        metadata={},
    )
    
    decision = evaluate_pre_action_compute_generate(context)
    
    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("unsafe" in c.message.lower() or "forbidden" in c.message.lower() for c in decision.checks)


def test_compute_generate_reject_invalid_inputs():
    """Invalid inputs/outputs should be rejected."""
    context = JudgeContext(
        request_id="test_005",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "test_model",
            "description": "Test model",
            "inputs": None,  # Invalid: should be dict
            "outputs": {},
        },
        metadata={},
    )
    
    decision = evaluate_pre_action_compute_generate(context)
    
    assert decision.decision == JudgeDecisionType.BLOCK


def test_compute_generate_reject_routing_mismatch():
    """Wrong action should be rejected."""
    context = JudgeContext(
        request_id="test_006",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="test",
        action="sys",  # Wrong action
        input={},
        metadata={},
    )
    
    decision = evaluate_pre_action_compute_generate(context)
    
    assert decision.decision == JudgeDecisionType.BLOCK
    assert any("routing" in (c.message or "").lower() or "routing" in " ".join(decision.issues).lower() for c in decision.checks)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
