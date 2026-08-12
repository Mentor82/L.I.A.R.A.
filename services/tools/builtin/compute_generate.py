"""
Compute Generate Tool — Generates Julia models from natural language.

Allows agents to create custom deterministic computation models
by describing what they need in plain English.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from services.contracts import InferenceRequest, InferenceResult
from services.inference.gateway import InferenceGateway
from services.simulation.registry import GeneratedModelRegistry, ModelMetadata
from services.tools.base import Tool

__all__ = ["ComputeGenerateTool"]


JULIA_GENERATION_SYSTEM_PROMPT = """You are an expert Julia programmer specialized in scientific computing.
Your task is to generate complete, syntactically correct Julia functions for numerical computation.

REQUIREMENTS:
1. Generate a complete, runnable Julia function named `compute(inputs::Dict)::Dict`
2. The function MUST accept a Dict of inputs and return a Dict of outputs
3. Include all necessary imports (using statements) at the top
4. Add clear documentation strings (docstrings)
5. Validate input ranges where applicable
6. Return outputs as a properly typed Dict

SAFETY CONSTRAINTS:
- No file I/O operations
- No external process calls
- No network access
- Only mathematical/scientific operations

OUTPUT FORMAT:
Return ONLY the Julia code wrapped in <julia_code> tags, with NO other text:

<julia_code>
using SpecialFunctions

function compute(inputs::Dict)::Dict
    # Implementation here
    return outputs::Dict
end
</julia_code>"""


class ComputeGenerateTool(Tool):
    """Generate Julia computation models from natural language descriptions."""
    
    def __init__(self, inference_gateway: InferenceGateway | None = None):
        super().__init__()
        self.inference_gateway = inference_gateway or InferenceGateway()
        self.registry = GeneratedModelRegistry()
    
    @property
    def name(self) -> str:
        return "compute.generate"
    
    @property
    def description(self) -> str:
        return "Generate a custom Julia computation model from a natural language description"
    
    @property
    def required_parameters(self) -> list[str]:
        return ["model_name", "description", "inputs", "outputs"]
    
    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "Identifier for the generated model (lowercase alphanumeric + underscore)",
                },
                "description": {
                    "type": "string",
                    "description": "Natural language description of what the model should compute",
                },
                "inputs": {
                    "type": "object",
                    "description": "Expected input parameters: {name: type_hint}, e.g., {'speed': 'float', 'torque': 'float'}",
                },
                "outputs": {
                    "type": "object",
                    "description": "Expected output parameters: {name: type_hint}, e.g., {'power': 'float'}",
                },
                "llm_provider": {
                    "type": "string",
                    "description": "LLM provider to use (ollama, llama_cpp, openvino, etc.)",
                    "default": "ll_ol_fallback",
                },
            },
            "required": ["model_name", "description", "inputs", "outputs"],
        }
    
    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Generate and store a Julia model.
        
        Args:
            model_name: Model identifier
            description: Natural language description
            inputs: Dict of input parameter names and types
            outputs: Dict of output parameter names and types
            llm_provider: Which LLM to use (optional)
        
        Returns:
            {
                status: "success" | "error",
                model_name: str,
                message: str,
                model_url: str,  # e.g., "POST /compute/run with model=user_wind_simulation"
                metadata: dict,  # if success
            }
        """
        model_name = kwargs.get("model_name", "").strip()
        description = kwargs.get("description", "").strip()
        inputs = kwargs.get("inputs", {})
        outputs = kwargs.get("outputs", {})
        llm_provider = kwargs.get("llm_provider", "ll_ol_fallback")
        
        # Validate inputs
        if not model_name:
            return {
                "status": "error",
                "message": "model_name is required",
            }
        
        if not re.match(r"^[a-z0-9_]+$", model_name):
            return {
                "status": "error",
                "message": f"Invalid model_name '{model_name}': must be lowercase alphanumeric + underscore",
            }
        
        if not description:
            return {
                "status": "error",
                "message": "description is required",
            }
        
        if not inputs or not isinstance(inputs, dict):
            return {
                "status": "error",
                "message": "inputs must be a non-empty dict",
            }
        
        if not outputs or not isinstance(outputs, dict):
            return {
                "status": "error",
                "message": "outputs must be a non-empty dict",
            }
        
        # Build generation prompt
        inputs_str = ", ".join([f"{k}: {v}" for k, v in inputs.items()])
        outputs_str = ", ".join([f"{k}: {v}" for k, v in outputs.items()])
        
        generation_prompt = f"""Generate a Julia computation model with the following specification:

DESCRIPTION:
{description}

INPUT PARAMETERS: {inputs_str}
OUTPUT PARAMETERS: {outputs_str}

The model must:
1. Accept inputs as a Dict with keys: {list(inputs.keys())}
2. Return outputs as a Dict with keys: {list(outputs.keys())}
3. Use only mathematical operations (no I/O, no network, no processes)
4. Include proper error handling and validation
5. Be efficient and accurate

Generate the complete Julia code now."""
        
        try:
            # Call LLM to generate code
            inference_request = InferenceRequest(
                messages=[
                    {
                        "role": "system",
                        "content": JULIA_GENERATION_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": generation_prompt,
                    },
                ],
                provider=llm_provider,
            )
            
            result: InferenceResult = await self.inference_gateway.infer(inference_request)
            
            if not result.success:
                return {
                    "status": "error",
                    "message": f"LLM inference failed: {result.message}",
                }
            
            # Extract Julia code from response
            response_text = result.message or ""
            match = re.search(r"<julia_code>(.*?)</julia_code>", response_text, re.DOTALL)
            
            if not match:
                return {
                    "status": "error",
                    "message": "LLM did not return valid Julia code (missing <julia_code> tags)",
                }
            
            julia_code = match.group(1).strip()
            
            if not julia_code:
                return {
                    "status": "error",
                    "message": "Generated Julia code is empty",
                }
            
            # Create metadata
            metadata = ModelMetadata(
                name=model_name,
                created_at=datetime.utcnow().isoformat(),
                prompt=generation_prompt,
                llm_model=llm_provider,
                description=description,
                inputs=inputs,
                outputs=outputs,
                version=1,
                syntax_valid=False,
                execution_tested=False,
                tags=["auto-generated"],
            )
            
            # Store model
            success, store_message = self.registry.store_model(
                model_name,
                julia_code,
                metadata
            )
            
            if not success:
                return {
                    "status": "error",
                    "message": store_message,
                }
            
            return {
                "status": "success",
                "model_name": model_name,
                "message": f"Model '{model_name}' generated and stored successfully",
                "model_url": f"POST /compute/run with body: {{'model': '{model_name}', 'inputs': {{...}}}}",
                "metadata": {
                    "created_at": metadata.created_at,
                    "description": metadata.description,
                    "inputs": metadata.inputs,
                    "outputs": metadata.outputs,
                    "llm_model": metadata.llm_model,
                    "syntax_valid": metadata.syntax_valid,
                    "version": metadata.version,
                },
            }
        
        except Exception as e:
            return {
                "status": "error",
                "message": f"Generation error: {e}",
            }
