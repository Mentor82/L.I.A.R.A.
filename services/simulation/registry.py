"""
Generated Model Registry — Manages dynamically generated Julia compute models.

Provides storage, metadata tracking, and validation for models created
by the compute.generate tool.
"""

import json
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

__all__ = ["GeneratedModelRegistry", "ModelMetadata"]


@dataclass
class ModelMetadata:
    """Metadata for a generated model."""
    
    name: str
    """Model identifier (e.g., 'user_wind_simulation')."""
    
    created_at: str
    """ISO 8601 timestamp of generation."""
    
    prompt: str
    """Natural language prompt that generated the model."""
    
    llm_model: str
    """LLM model used for generation (e.g., 'claude-haiku')."""
    
    description: str
    """Human-readable model description."""
    
    inputs: dict[str, str]
    """Input parameters: {name: type_hint}."""
    
    outputs: dict[str, str]
    """Output parameters: {name: type_hint}."""
    
    version: int = 1
    """Model version."""
    
    syntax_valid: bool = False
    """Whether Julia syntax was validated."""
    
    execution_tested: bool = False
    """Whether model was successfully executed."""
    
    tags: list[str] | None = None
    """Arbitrary tags for organization."""


class GeneratedModelRegistry:
    """Registry for dynamically generated Julia models."""
    
    def __init__(self, models_dir: str | Path = None):
        """
        Initialize registry.
        
        Args:
            models_dir: Root directory for generated models.
                       Defaults to services/simulation/models/generated/
        """
        if models_dir is None:
            models_dir = Path(__file__).parent / "models" / "generated"
        
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir = self.models_dir / "_metadata"
        self.metadata_dir.mkdir(exist_ok=True)
    
    def store_model(
        self,
        name: str,
        julia_code: str,
        metadata: ModelMetadata
    ) -> tuple[bool, str]:
        """
        Store a generated model with metadata.
        
        Args:
            name: Model identifier (alphanumeric + underscore only).
            julia_code: Complete Julia model code.
            metadata: ModelMetadata instance.
        
        Returns:
            (success: bool, message: str)
        """
        # Validate name
        if not re.match(r"^[a-z0-9_]+$", name):
            return False, f"Invalid model name '{name}': must be lowercase alphanumeric + underscore"
        
        if name.startswith("_"):
            return False, f"Model name '{name}' cannot start with underscore"
        
        # Check for existing model
        model_path = self.models_dir / f"{name}.jl"
        if model_path.exists():
            return False, f"Model '{name}' already exists. Use a different name or delete first."
        
        # Validate Julia syntax
        syntax_ok, syntax_msg = self._validate_julia_syntax(julia_code)
        if not syntax_ok:
            return False, f"Julia syntax error: {syntax_msg}"
        
        try:
            # Store Julia code
            model_path.write_text(julia_code, encoding="utf-8")
            
            # Update metadata
            metadata.syntax_valid = True
            metadata.name = name
            
            # Store metadata
            metadata_path = self.metadata_dir / f"{name}.json"
            metadata_path.write_text(
                json.dumps(asdict(metadata), indent=2),
                encoding="utf-8"
            )
            
            return True, f"Model '{name}' stored successfully (v{metadata.version})"
        
        except Exception as e:
            # Cleanup on failure
            if model_path.exists():
                model_path.unlink()
            return False, f"Storage error: {e}"
    
    def load_model(self, name: str) -> tuple[Optional[str], Optional[ModelMetadata], str]:
        """
        Load a generated model and its metadata.
        
        Args:
            name: Model identifier.
        
        Returns:
            (julia_code: str|None, metadata: ModelMetadata|None, message: str)
        """
        model_path = self.models_dir / f"{name}.jl"
        metadata_path = self.metadata_dir / f"{name}.json"
        
        if not model_path.exists():
            return None, None, f"Model '{name}' not found"
        
        if not metadata_path.exists():
            return None, None, f"Metadata for model '{name}' not found (corrupted registry)"
        
        try:
            julia_code = model_path.read_text(encoding="utf-8")
            metadata_dict = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = ModelMetadata(**metadata_dict)
            return julia_code, metadata, "OK"
        
        except Exception as e:
            return None, None, f"Load error: {e}"
    
    def list_models(self) -> list[dict[str, Any]]:
        """
        List all available generated models.
        
        Returns:
            List of {name, created_at, description, version, tags}.
        """
        models = []
        for metadata_path in self.metadata_dir.glob("*.json"):
            try:
                metadata_dict = json.loads(metadata_path.read_text(encoding="utf-8"))
                models.append({
                    "name": metadata_dict["name"],
                    "created_at": metadata_dict["created_at"],
                    "description": metadata_dict["description"],
                    "version": metadata_dict.get("version", 1),
                    "tags": metadata_dict.get("tags", []),
                })
            except Exception:
                continue
        
        return sorted(models, key=lambda m: m["created_at"], reverse=True)
    
    def delete_model(self, name: str) -> tuple[bool, str]:
        """
        Delete a generated model and its metadata.
        
        Args:
            name: Model identifier.
        
        Returns:
            (success: bool, message: str)
        """
        model_path = self.models_dir / f"{name}.jl"
        metadata_path = self.metadata_dir / f"{name}.json"
        
        if not model_path.exists():
            return False, f"Model '{name}' not found"
        
        try:
            model_path.unlink()
            if metadata_path.exists():
                metadata_path.unlink()
            return True, f"Model '{name}' deleted"
        
        except Exception as e:
            return False, f"Deletion error: {e}"
    
    def mark_tested(self, name: str, success: bool = True) -> tuple[bool, str]:
        """
        Mark a model as execution-tested.
        
        Args:
            name: Model identifier.
            success: Whether execution test passed.
        
        Returns:
            (success: bool, message: str)
        """
        metadata_path = self.metadata_dir / f"{name}.json"
        
        if not metadata_path.exists():
            return False, f"Model '{name}' metadata not found"
        
        try:
            metadata_dict = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata_dict["execution_tested"] = success
            metadata_path.write_text(
                json.dumps(metadata_dict, indent=2),
                encoding="utf-8"
            )
            return True, f"Model '{name}' marked as tested (success={success})"
        
        except Exception as e:
            return False, f"Update error: {e}"
    
    @staticmethod
    def _validate_julia_syntax(julia_code: str) -> tuple[bool, str]:
        """
        Validate Julia code syntax.
        
        Args:
            julia_code: Julia source code.
        
        Returns:
            (valid: bool, message: str)
        """
        import tempfile
        
        try:
            # Write code to temp file and check syntax
            with tempfile.NamedTemporaryFile(mode="w", suffix=".jl", delete=False, encoding="utf-8") as f:
                f.write(julia_code)
                temp_path = f.name
            
            try:
                result = subprocess.run(
                    ["julia", "--startup-file=no", temp_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout
                    return False, error_msg.split('\n')[0]
                
                return True, "Syntax OK"
            
            finally:
                Path(temp_path).unlink(missing_ok=True)
        
        except subprocess.TimeoutExpired:
            return False, "Julia syntax check timeout"
        
        except FileNotFoundError:
            return False, "Julia not found on system"
        
        except Exception as e:
            return False, str(e)
