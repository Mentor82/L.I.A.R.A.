"""FastAPI router for Julia compute and simulation endpoints."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Request, Response

from services.simulation.bridge import JuliaBridge
from services.simulation.runner import SimulationRunner
from services.tools.builtin.compute_generate import ComputeGenerateTool


router = APIRouter(prefix="/compute", tags=["compute"])


@router.post("/run")
async def compute_run(
    request: Request, response: Response
) -> dict[str, Any]:
    """Run a Julia computation model.

    Body JSON:
        model  (str)  — model name, must be in JULIA_ALLOWLIST
        inputs (dict) — model-specific input parameters
    """
    response.headers["Cache-Control"] = "no-store"
    body = await request.json()
    model = body.get("model", "")
    inputs = body.get("inputs", {})

    if not model:
        raise HTTPException(status_code=422, detail="'model' field is required")
    if not isinstance(inputs, dict):
        raise HTTPException(status_code=422, detail="'inputs' must be a JSON object")

    runner = SimulationRunner()
    result = await runner.run(model, inputs)

    if result.get("status") == "error":
        raise HTTPException(status_code=422, detail=result.get("error", "simulation failed"))
    return result


@router.get("/models")
async def compute_models(response: Response) -> dict[str, Any]:
    """List available (allowlisted) Julia computation models."""
    response.headers["Cache-Control"] = "no-store"
    bridge = JuliaBridge()
    return {"models": bridge.list_available()}


@router.post("/generate")
async def compute_generate(request: Request, response: Response) -> dict[str, Any]:
    """Generate a new Julia computation model from natural language.

    Body JSON:
        model_name (str)      — identifier for the generated model
        description (str)     — what the model should compute
        inputs (dict)         — input parameters: {name: type_hint}
        outputs (dict)        — output parameters: {name: type_hint}
        llm_provider (str)    — LLM to use for generation (optional)
    """
    response.headers["Cache-Control"] = "no-store"
    body = await request.json()

    # Validate required fields
    for required in ["model_name", "description", "inputs", "outputs"]:
        if required not in body:
            raise HTTPException(status_code=422, detail=f"'{required}' field is required")

    tool = ComputeGenerateTool()
    result = await tool.execute(**body)

    if result.get("status") == "error":
        raise HTTPException(status_code=422, detail=result.get("error", "generation failed"))

    return result
