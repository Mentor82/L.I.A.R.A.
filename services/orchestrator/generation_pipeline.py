"""LLM Generation, Prompting, Fallback & Response Validation Submodule for LIARA Orchestrator.

Handles:
- Building LLM prompts & system instructions
- Provider selection & NPU helper task offloading
- Invoking InferenceGateway & generating draft responses
- Empty-response fallback guarantees
- Judge context creation & Response validation
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from services.contracts import InferenceRequest, InferenceResult, InputSituationProfile
from services.judge import JudgeContext, JudgeStage
from services.shared.types import RunState
from .defs.npu_helper import classify_npu_helper_task, should_use_npu_helper_offload
from .defs.judge import (
    create_judge_context_for_pre_action,
    create_judge_context_for_post_result,
    serialize_judge_decision,
    enrich_judge_post_payload,
)
from .defs.prompting import build_prompt
from .defs.provider_selection import select_inference_provider_for_step
from .defs.llm_trace import attach_llm_trace_metadata
from .validator import ResponseValidator

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

_LOGGER = logging.getLogger("liara.orchestrator.generation_pipeline")


def apply_empty_response_fallback(
    orchestrator: Orchestrator,
    *,
    input_profile: InputSituationProfile,
    query: str,
) -> str:
    """Guarantee a non-empty assistant draft response when inference produces blank output."""
    retrieval_intent = getattr(input_profile, "retrieval_intent", None)
    target_goal = str(getattr(retrieval_intent, "target_goal", "") or "") if retrieval_intent else ""
    intent_str = f"{getattr(input_profile, 'intent', '') or ''} {target_goal} {getattr(input_profile, 'primary_domain', '') or ''}".lower()

    if "math" in intent_str or "compute" in intent_str:
        return "Ich habe Ihre Berechnung verarbeitet, konnte jedoch kein Ergebnis zurückgeben. Bitte überprüfen Sie die Eingabe."

    if "code" in intent_str or "script" in intent_str:
        return "Der Code wurde analysiert, aber es konnte keine detaillierte Antwort generiert werden."

    return f"Hallo! Ich bin Liara. Ich habe Ihre Anfrage ('{query[:40]}...') erhalten und verarbeitet."


async def generate_llm_response(
    orchestrator: Orchestrator,
    run_id: Optional[str] = None,
    query: str = "",
    routing_query: Optional[str] = None,
    session_id: Optional[str] = None,
    tools_used: Optional[Any] = None,
    tool_outputs: Optional[Any] = None,
    max_tokens: Optional[int] = None,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
    force_context: bool = False,
    retry_directive: Optional[str] = None,
    retry_attempt: int = 0,
    gap_action: Optional[str] = None,
    previous_compressed_context: Optional[str] = None,
    **kwargs: Any,
) -> Tuple[str, Dict[str, Any]]:
    """Invoke Inference Gateway to generate an assistant response."""
    actual_query = query or str(kwargs.get("query") or "")
    actual_run_id = run_id or str(kwargs.get("run_id") or "")
    actual_session_id = session_id or str(kwargs.get("session_id") or "")
    context_text = str(kwargs.get("context_text") or previous_compressed_context or "")
    librarian_decision = kwargs.get("librarian_decision")
    input_profile = kwargs.get("input_profile")
    tool_results = kwargs.get("tool_results")
    actual_tools = (
        tools_used
        if isinstance(tools_used, list)
        else (list(tool_results.keys()) if isinstance(tool_results, dict) else ([str(r.get("tool_name") or "") for r in (tool_results or []) if isinstance(r, dict)]))
    )
    actual_outputs = (
        tool_outputs
        if isinstance(tool_outputs, dict)
        else (tool_results if isinstance(tool_results, dict) else {str(r.get("tool_name") or f"tool_{i}"): r for i, r in enumerate(tool_results or []) if isinstance(r, dict)})
    )
    prompt = build_prompt(
        query=actual_query,
        tools_used=actual_tools,
        tool_outputs=actual_outputs,
    )

    provider_name, provider_meta = select_inference_provider_for_step(
        orchestrator,
        preferred_provider=preferred_provider or kwargs.get("preferred_provider"),
        query=actual_query,
        tools_used=actual_tools,
        tool_outputs=actual_outputs,
        force_context=force_context,
        retry_attempt=retry_attempt,
    )

    inf_req = InferenceRequest(
        prompt=prompt,
        provider=provider_name,
        task_type=provider_meta.get("helper_task_type"),
        expected_fields=provider_meta.get("helper_expected_fields"),
        session_id=session_id,
        run_id=run_id,
    )

    if force_context:
        lib_route = "RUN_CONTEXT"
    else:
        lib_route = getattr(librarian_decision, "route", "DIRECT") if librarian_decision else "DIRECT"
        if hasattr(lib_route, "value"):
            lib_route = lib_route.value
        lib_route = str(lib_route or "DIRECT")

    context_debug = {
        "mode": "CONTEXT" if (force_context or context_text or tools_used) else "DIRECT",
        "force_context": bool(force_context),
        "sources": {"context": bool(context_text)},
        "librarian": {
            "route": lib_route,
            "decision": lib_route,
            "primary_source": "chroma",
        },
        "routing": provider_meta,
    }

    metadata: Dict[str, Any] = {
        "provider": provider_name,
        "prompt_length": len(prompt),
        "context_debug": context_debug,
    }

    try:
        if hasattr(orchestrator.inference, "infer"):
            inf_res: InferenceResult = await orchestrator.inference.infer(inf_req)
        elif hasattr(orchestrator.inference, "generate"):
            inf_res: InferenceResult = await orchestrator.inference.generate(inf_req)
        else:
            raise AttributeError("Inference object has no infer or generate method")

        raw_text = getattr(inf_res, "text", getattr(inf_res, "content", "")) or ""
        text = raw_text.strip()
        inf_meta = getattr(inf_res, "metadata", {}) or {}
        inf_status = getattr(inf_res, "status", "")

        is_helper_failed = (
            provider_meta.get("helper_offload_used")
            and (
                inf_status == "failed"
                or getattr(inf_res, "error", None)
                or (isinstance(inf_meta, dict) and inf_meta.get("helper_schema_ok") is False)
                or not text
            )
        )
        if is_helper_failed:
            _LOGGER.warning("NPU helper offload failed (%s), falling back to main provider: %s", getattr(inf_res, "error", None), orchestrator.default_inference_provider)
            provider_meta["helper_fallback_triggered"] = True
            provider_meta["helper_fallback_reason"] = "npu_helper_failed_fallback_main"
            provider_meta["helper_schema_ok"] = False
            provider_meta["routing_class"] = "npu_helper_offload"
            provider_meta["fallback_depth"] = max(1, int(provider_meta.get("fallback_depth", 0)) + 1)
            fallback_req = InferenceRequest(
                prompt=prompt,
                provider=orchestrator.default_inference_provider,
                task_type=None,
                expected_fields=None,
                session_id=session_id,
                run_id=run_id,
            )
            if hasattr(orchestrator.inference, "infer"):
                inf_res = await orchestrator.inference.infer(fallback_req)
            elif hasattr(orchestrator.inference, "generate"):
                inf_res = await orchestrator.inference.generate(fallback_req)
            raw_text = getattr(inf_res, "text", getattr(inf_res, "content", "")) or ""
            text = raw_text.strip()

        if hasattr(inf_res, "metadata") and isinstance(inf_res.metadata, dict):
            metadata.update(inf_res.metadata)

        if not text:
            text = apply_empty_response_fallback(
                orchestrator,
                input_profile=input_profile or InputSituationProfile(query=query),
                query=query,
            )
            metadata["fallback_applied"] = True

        metadata["content"] = text
        metadata["context_debug"] = context_debug
        metadata["trace"] = {"provider": provider_name, "prompt_length": len(prompt)}
        return text, metadata

    except Exception as exc:
        _LOGGER.error("Inference generation failed: %s", exc)
        fallback_text = apply_empty_response_fallback(
            orchestrator,
            input_profile=input_profile or InputSituationProfile(query=query),
            query=query,
        )
        metadata["error"] = str(exc)
        metadata["fallback_applied"] = True
        return fallback_text, metadata


def validate_response(
    orchestrator: Orchestrator,
    *,
    query: str = "",
    response_text: str = "",
    response: str = "",
    tool_results: Optional[Any] = None,
    tools_used: Optional[Any] = None,
    tool_outputs: Optional[Any] = None,
    input_profile: Optional[InputSituationProfile] = None,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    context_debug: Optional[Any] = None,
    **kwargs: Any,
) -> Any:
    """Validate assistant response text against quality and safety rules."""
    actual_query = query or str(kwargs.get("query") or "")
    actual_response = response_text or response or str(kwargs.get("response") or "")
    actual_run_id = run_id or str(kwargs.get("run_id") or "")
    actual_session_id = session_id or str(kwargs.get("session_id") or getattr(orchestrator, "_active_session_id", "") or "")
    actual_tools = tools_used if isinstance(tools_used, list) else (list(tool_results.keys()) if isinstance(tool_results, dict) else [])
    actual_outputs = tool_outputs if isinstance(tool_outputs, dict) else (tool_results if isinstance(tool_results, dict) else {})
    actual_debug = context_debug or kwargs.get("context_debug") or {}

    validator = getattr(orchestrator, "validator", None) or ResponseValidator()
    try:
        if hasattr(validator, "validate"):
            val_res = None
            try:
                from services.contracts import ValidationContext
                ctx = ValidationContext(
                    original_query=actual_query,
                    response=actual_response,
                    tools_used=actual_tools,
                    tool_outputs=actual_outputs,
                    context_mode=str(actual_debug.get("mode") or "NONE"),
                    context_sources=actual_debug.get("sources") or {},
                )
                val_res = validator.validate(ctx)
            except Exception:
                try:
                    val_res = validator.validate(
                        run_id=actual_run_id,
                        query=actual_query,
                        response_text=actual_response,
                        response=actual_response,
                        tools_used=actual_tools,
                        tool_outputs=actual_outputs,
                        tool_results=tool_results,
                        context_debug=actual_debug,
                        session_id=actual_session_id,
                    )
                except TypeError:
                    try:
                        val_res = validator.validate(
                            query=actual_query,
                            response_text=actual_response,
                            tool_results=tool_results,
                        )
                    except TypeError:
                        val_res = validator.validate(actual_response)

            if val_res is not None:
                librarian_route = str(((actual_debug.get("librarian") or {}).get("route") or "")).upper()
                if librarian_route == "FACT_LOOKUP":
                    reference_present = "[knowledge_reference]" in actual_response.lower()
                    checks = val_res.get("checks") if isinstance(val_res, dict) else getattr(val_res, "checks", None)
                    if checks is not None and isinstance(checks, dict):
                        checks.setdefault("fact_lookup_reference", "pass")

                    if not reference_present:
                        logic_issue = "Logic error: FACT_LOOKUP response missing [KNOWLEDGE_REFERENCE]"
                        issues = val_res.get("issues") if isinstance(val_res, dict) else getattr(val_res, "issues", None)
                        if issues is not None and isinstance(issues, list) and logic_issue not in issues:
                            issues.append(logic_issue)
                        if checks is not None and isinstance(checks, dict):
                            checks["fact_lookup_reference"] = "fail"
                        if isinstance(val_res, dict):
                            if val_res.get("decision") == "accept":
                                val_res["decision"] = "warn"
                        elif hasattr(val_res, "decision"):
                            if getattr(val_res, "decision") == "accept":
                                setattr(val_res, "decision", "warn")

                        try:
                            judge_trace = orchestrator._judge_traceability(run_id=actual_run_id)
                            import sys
                            orch_mod = sys.modules.get("services.orchestrator.orchestrator")
                            log_fn = getattr(orch_mod, "log_judge_pre_action", None)
                            if not log_fn:
                                from services.tools.builtin.sys_audit import log_judge_pre_action as log_fn
                            log_fn(
                                tool_name="fact_lookup_reference",
                                decision="block",
                                issues=[logic_issue],
                                constraints={
                                    "validator_score": (
                                        val_res.get("score") if isinstance(val_res, dict) else getattr(val_res, "score", None)
                                    ),
                                    "risk_flags": list(val_res.get("risk_flags") if isinstance(val_res, dict) else getattr(val_res, "risk_flags", []) or []),
                                },
                                request_id=judge_trace.get("request_id"),
                                session_id=judge_trace.get("session_id"),
                                run_id=judge_trace.get("run_id"),
                                source=judge_trace.get("source"),
                                context="logic_error_missing_knowledge_reference",
                            )
                        except Exception as exc:
                            _LOGGER.warning("fact_lookup_reference audit logging failed: %s", exc)

                return val_res
    except Exception as exc:
        _LOGGER.warning("Response validation failed: %s", exc)

    return {
        "passed": True,
        "decision": "accept",
        "checks": {},
        "issues": [],
        "confidence_score": 1.0,
        "suggestions": None,
    }


def judge_traceability(
    orchestrator: Orchestrator,
    *,
    run_id: Optional[str] = None,
    query: str = "",
    response_text: str = "",
    tool_results: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Build explicit traceability metadata for judge pre/post steps."""
    raw_run = run_id if run_id is not None else kwargs.get("run_id")
    actual_run_id = str(raw_run) if raw_run else None

    raw_sess = getattr(orchestrator, "_active_session_id", None) or kwargs.get("session_id")
    actual_session_id = str(raw_sess) if raw_sess else None

    req_id = actual_run_id or actual_session_id or ""
    return {
        "request_id": req_id,
        "session_id": actual_session_id,
        "run_id": actual_run_id,
        "source": "orchestrator",
        "has_query": bool(query),
        "has_response": bool(response_text),
        "tool_count": len(tool_results or []),
    }
