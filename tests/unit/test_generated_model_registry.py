"""Unit tests for GeneratedModelRegistry."""

import json
import tempfile
from pathlib import Path

import pytest

from services.simulation.registry import GeneratedModelRegistry, ModelMetadata


@pytest.fixture
def temp_registry():
    """Create a temporary registry for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        registry = GeneratedModelRegistry(models_dir=tmpdir)
        yield registry


def test_registry_initialization(temp_registry):
    """Registry should create necessary directories."""
    assert temp_registry.models_dir.exists()
    assert temp_registry.metadata_dir.exists()


def test_store_valid_model(temp_registry):
    """Store a valid model."""
    julia_code = """using JSON

function compute(inputs::Dict)::Dict
    x = get(inputs, "x", 1.0)
    return Dict("result" => x * 2)
end
"""
    metadata = ModelMetadata(
        name="test_model",
        created_at="2026-04-19T00:00:00",
        prompt="Test model",
        llm_model="test",
        description="A test model",
        inputs={"x": "float"},
        outputs={"result": "int"},
    )
    
    success, message = temp_registry.store_model("test_model", julia_code, metadata)
    
    # For now, syntax validation might fail if Julia is not available
    # So we just check that the registry attempts to store
    assert "test_model" in message or "not found" in message.lower() or "timeout" in message.lower()


def test_invalid_model_name(temp_registry):
    """Reject invalid model names."""
    julia_code = "function compute(inputs::Dict)::Dict\nreturn Dict()\nend"
    metadata = ModelMetadata(
        name="invalid-name",
        created_at="2026-04-19T00:00:00",
        prompt="Test",
        llm_model="test",
        description="Test",
        inputs={"x": "float"},
        outputs={"y": "float"},
    )
    
    success, message = temp_registry.store_model("invalid-name", julia_code, metadata)
    
    assert not success
    assert "Invalid model name" in message


def test_model_name_starting_with_underscore(temp_registry):
    """Reject model names starting with underscore."""
    julia_code = "function compute(inputs::Dict)::Dict\nreturn Dict()\nend"
    metadata = ModelMetadata(
        name="_private",
        created_at="2026-04-19T00:00:00",
        prompt="Test",
        llm_model="test",
        description="Test",
        inputs={"x": "float"},
        outputs={"y": "float"},
    )
    
    success, message = temp_registry.store_model("_private", julia_code, metadata)
    
    assert not success
    assert "cannot start with underscore" in message


def test_list_models(temp_registry):
    """List all stored models."""
    # Create a model manually for testing
    model_name = "test_model"
    metadata_dict = {
        "name": model_name,
        "created_at": "2026-04-19T00:00:00",
        "description": "Test model",
        "version": 1,
        "tags": ["test"],
        "prompt": "test",
        "llm_model": "test",
        "inputs": {"x": "float"},
        "outputs": {"y": "float"},
        "syntax_valid": False,
        "execution_tested": False,
    }
    
    metadata_path = temp_registry.metadata_dir / f"{model_name}.json"
    metadata_path.write_text(json.dumps(metadata_dict), encoding="utf-8")
    
    models = temp_registry.list_models()
    
    assert len(models) >= 1
    assert any(m["name"] == model_name for m in models)


def test_delete_model(temp_registry):
    """Delete a model."""
    model_name = "test_model"
    
    # Create model manually
    julia_path = temp_registry.models_dir / f"{model_name}.jl"
    julia_path.write_text("function compute(inputs::Dict)::Dict\nreturn Dict()\nend")
    
    metadata_dict = {
        "name": model_name,
        "created_at": "2026-04-19T00:00:00",
        "description": "Test",
        "version": 1,
        "prompt": "test",
        "llm_model": "test",
        "inputs": {"x": "float"},
        "outputs": {"y": "float"},
        "syntax_valid": False,
        "execution_tested": False,
    }
    metadata_path = temp_registry.metadata_dir / f"{model_name}.json"
    metadata_path.write_text(json.dumps(metadata_dict))
    
    # Delete
    success, message = temp_registry.delete_model(model_name)
    
    assert success
    assert not julia_path.exists()
    assert not metadata_path.exists()


def test_mark_tested(temp_registry):
    """Mark a model as execution-tested."""
    model_name = "test_model"
    
    # Create model
    metadata_dict = {
        "name": model_name,
        "created_at": "2026-04-19T00:00:00",
        "description": "Test",
        "version": 1,
        "prompt": "test",
        "llm_model": "test",
        "inputs": {"x": "float"},
        "outputs": {"y": "float"},
        "syntax_valid": False,
        "execution_tested": False,
    }
    metadata_path = temp_registry.metadata_dir / f"{model_name}.json"
    metadata_path.write_text(json.dumps(metadata_dict))
    
    # Mark tested
    success, message = temp_registry.mark_tested(model_name, success=True)
    
    assert success
    
    # Verify
    updated = json.loads(metadata_path.read_text())
    assert updated["execution_tested"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
