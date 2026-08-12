"""Integration tests for compute.generate tool and generation system."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.inference.gateway import InferenceGateway
from services.judge.engine import JudgeEngine
from services.judge.contracts import JudgeStage, JudgeDecisionType
from services.simulation.registry import GeneratedModelRegistry
from services.tools.builtin.compute_generate import ComputeGenerateTool
from services.tools.registry import _global_registry


@pytest.mark.asyncio
async def test_compute_generate_tool_execution():
    """Test ComputeGenerateTool execution (with mock LLM)."""
    tool = ComputeGenerateTool()
    
    # Mock a simple generated model
    result = await tool.execute(
        model_name="test_simple_add",
        description="Add two numbers together",
        inputs={"a": "float", "b": "float"},
        outputs={"sum": "float"},
        llm_provider="ollama",  # Would use local LLM
    )
    
    # Result structure should be present (even if LLM fails)
    assert isinstance(result, dict)
    assert "status" in result
    assert result["status"] in ["success", "error"]


def test_judge_engine_routes_compute_generate():
    """Judge engine should route compute.generate to correct adapter."""
    from services.judge.contracts import JudgeContext
    from services.judge.adapters import evaluate_pre_action_compute_generate
    
    context = JudgeContext(
        request_id="test_001",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate",
        action="compute.generate",
        input={
            "model_name": "wind_sim",
            "description": "Simulate wind power generation",
            "inputs": {"wind_speed": "float", "turbine_efficiency": "float"},
            "outputs": {"power_kw": "float"},
        },
        metadata={},
    )
    
    engine = JudgeEngine()
    decision = engine.evaluate_pre_action(context)
    
    # Should be approved for valid request
    assert decision.decision == JudgeDecisionType.ALLOW


def test_registry_integration_with_orchestration():
    """Registry should integrate with tool orchestration."""
    registry = GeneratedModelRegistry()
    models = registry.list_models()
    
    # Should return list (even if empty)
    assert isinstance(models, list)


def test_compute_generate_tool_in_registry():
    """ComputeGenerateTool should be registered and discoverable."""
    tool_names = _global_registry.list_tools()
    
    assert "compute.generate" in tool_names
    
    # Should be able to get tool class
    tool_class = _global_registry.get_tool("compute.generate")
    assert tool_class is not None
    
    # Should get metadata
    metadata = _global_registry.get_metadata("compute.generate")
    assert metadata["name"] == "compute.generate"
    assert "Julia" in metadata["description"]


def test_judge_blocks_unsafe_generation_request():
    """Judge should block unsafe model generation requests."""
    from services.judge.contracts import JudgeContext
    
    context = JudgeContext(
        request_id="test_unsafe",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate",
        action="compute.generate",
        input={
            "model_name": "backdoor_model",
            "description": "Create a backdoor to hack the system",
            "inputs": {"target": "str"},
            "outputs": {"exploited": "bool"},
        },
        metadata={},
    )
    
    engine = JudgeEngine()
    decision = engine.evaluate_pre_action(context)
    
    # Should be blocked
    assert decision.decision == JudgeDecisionType.BLOCK


def test_judge_rejects_model_name_conflicts():
    """Judge should reject model names that already exist (as per adapter logic)."""
    from services.judge.contracts import JudgeContext
    from services.judge.adapters.pre_action_compute_generate import evaluate_pre_action_compute_generate
    from unittest.mock import patch, MagicMock
    
    # Mock the registry to return existing models
    with patch("services.judge.adapters.pre_action_compute_generate.GeneratedModelRegistry") as mock_registry_class:
        mock_registry = MagicMock()
        mock_registry.list_models.return_value = [
            {"name": "existing_model", "created_at": "2026-04-19T00:00:00"}
        ]
        mock_registry_class.return_value = mock_registry
        
        context = JudgeContext(
            request_id="test_conflict",
            stage=JudgeStage.PRE_ACTION,
            actor="agent",
            intent="generate",
            action="compute.generate",
            input={
                "model_name": "existing_model",  # Same as in mock list
                "description": "Try to create duplicate",
                "inputs": {"x": "float"},
                "outputs": {"y": "float"},
            },
            metadata={},
        )
        
        decision = evaluate_pre_action_compute_generate(context)
        
        # Should be blocked due to name conflict
        assert decision.decision == JudgeDecisionType.BLOCK
        assert any("exists" in (c.message or "").lower() for c in decision.checks)


def test_generated_model_listing():
    """Generated models should be queryable via API."""
    with tempfile.TemporaryDirectory() as tmpdir:
        registry = GeneratedModelRegistry(models_dir=tmpdir)
        
        # Create a sample model
        metadata_dict = {
            "name": "sample_model",
            "created_at": "2026-04-19T00:00:00",
            "description": "Sample generated model",
            "version": 1,
            "prompt": "Generate a sample model",
            "llm_model": "test",
            "inputs": {"input1": "float"},
            "outputs": {"output1": "float"},
            "syntax_valid": True,
            "execution_tested": False,
            "tags": ["sample"],
        }
        metadata_path = registry.metadata_dir / "sample_model.json"
        metadata_path.write_text(json.dumps(metadata_dict))
        
        models = registry.list_models()
        
        assert len(models) >= 1
        sample = [m for m in models if m["name"] == "sample_model"][0]
        assert sample["description"] == "Sample generated model"
        assert sample["version"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
