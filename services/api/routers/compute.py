"""FastAPI router for Julia compute and simulation endpoints."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from services.api.security import Principal, get_verified_principal
from services.judge.contracts import JudgeContext, JudgeStage
from services.judge.engine import JudgeEngine
from services.simulation.bridge import JuliaBridge
from services.simulation.runner import SimulationRunner
from services.tools.builtin.compute_generate import ComputeGenerateTool
from services.tools.builtin.sys_audit import log_judge_pre_action


_LOGGER = logging.getLogger(__name__)
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


def _judge_engine(request: Request) -> Any:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    return getattr(orchestrator, "judge_engine", None) or JudgeEngine()


def _audit_judge(**entry: Any) -> None:
    try:
        log_judge_pre_action(**entry)
    except Exception:
        # An unavailable audit must not turn into unaudited generation.
        raise HTTPException(status_code=503, detail={
            "reason_code": "judge.audit_unavailable", "request_id": entry["request_id"],
        }) from None


@router.post("/generate")
async def compute_generate(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_verified_principal),
) -> dict[str, Any]:
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
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    # Validate required fields
    for required in ["model_name", "description", "inputs", "outputs"]:
        if required not in body:
            raise HTTPException(status_code=422, detail=f"'{required}' field is required")

    # Same pre-action gate as the orchestrator's tool path; fail closed.
    context = JudgeContext(
        request_id=f"api-compute-{uuid4().hex[:12]}",
        stage=JudgeStage.PRE_ACTION,
        actor=principal.actor_id,
        intent="compute.generate",
        action="compute.generate",
        input=body,
        metadata={"source": "api", "principal_source": principal.source},
    )
    audit = {
        "tool_name": "compute.generate",
        "request_id": context.request_id,
        "run_id": context.request_id,
        "source": "api",
        "context": "api.compute.generate",
    }
    try:
        decision = _judge_engine(request).evaluate_pre_action(context)
    except Exception as exc:
        _LOGGER.error("Pre-action judge failed for compute.generate request_id=%s error_type=%s", context.request_id, type(exc).__name__)
        _audit_judge(decision="block", issues=["Pre-action judge failure (fail-closed)."], **audit)
        raise HTTPException(status_code=403, detail={
            "reason_code": "judge.pre_action_failed",
            "request_id": context.request_id,
        }) from exc
    _audit_judge(
        decision=decision.decision.value,
        issues=decision.issues,
        constraints=decision.constraints,
        **{**audit, "context": f"api.compute.generate:{decision.decision.value}"},
    )
    if not decision.passed:
        raise HTTPException(
            status_code=403,
            detail={"decision": decision.decision.value, "issues": decision.issues},
        )

    tool = ComputeGenerateTool()
    result = await tool.execute(**body)

    if result.get("status") == "error":
        raise HTTPException(status_code=422, detail=result.get("error", "generation failed"))

    return result
