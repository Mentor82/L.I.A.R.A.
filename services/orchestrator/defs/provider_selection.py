from typing import Any, Dict, List


def select_inference_provider_for_step(
    orchestrator: Any,
    *,
    preferred_provider: str | None,
    query: str,
    tools_used: List[str],
    tool_outputs: Dict[str, Any],
    force_context: bool,
    retry_attempt: int,
) -> tuple[str, Dict[str, Any]]:
    req_src = getattr(orchestrator, "_active_request_source", "") or "default"

    if req_src == "co_worker" and getattr(orchestrator, "co_worker_provider_lock_enabled", True):
        return orchestrator.co_worker_main_provider, {
            "routing_class": "co_worker",
            "helper_offload_used": False,
            "helper_schema_ok": None,
            "helper_fallback_triggered": False,
            "helper_offload_reason": "co_worker_locked_main_path",
            "helper_task_type": None,
            "helper_expected_fields": None,
            "co_worker_provider_locked": True,
            "co_worker_locked_provider": orchestrator.co_worker_main_provider,
            "co_worker_preferred_provider_ignored": bool(preferred_provider),
            "fallback_depth": 0,
            "breaker_state": "closed",
        }

    if preferred_provider and preferred_provider != getattr(orchestrator, "default_inference_provider", "openvino"):
        return preferred_provider, {
            "routing_class": req_src,
            "helper_offload_used": False,
            "helper_schema_ok": None,
            "helper_fallback_triggered": False,
            "helper_offload_reason": "explicit_provider_override",
            "helper_task_type": None,
            "helper_expected_fields": None,
            "co_worker_provider_locked": False,
            "fallback_depth": 0,
            "breaker_state": "closed",
        }

    if req_src == "co_worker":
        return orchestrator.default_inference_provider, {
            "routing_class": "co_worker",
            "helper_offload_used": False,
            "helper_schema_ok": None,
            "helper_fallback_triggered": False,
            "helper_offload_reason": "co_worker_main_path",
            "helper_task_type": None,
            "helper_expected_fields": None,
            "co_worker_provider_locked": False,
            "fallback_depth": 0,
            "breaker_state": "closed",
        }

    helper_task = orchestrator._classify_npu_helper_task(
        query=query,
        tools_used=tools_used,
        tool_outputs=tool_outputs,
        force_context=force_context,
        retry_attempt=retry_attempt,
    )
    if helper_task is not None:
        return orchestrator.npu_helper_provider, {
            "routing_class": req_src,
            "helper_offload_used": True,
            "helper_schema_ok": None,
            "helper_fallback_triggered": False,
            "helper_offload_reason": str(helper_task.get("reason") or "short_parallelizable_subtask"),
            "helper_task_type": str(helper_task.get("task_type") or "quick_extract"),
            "helper_expected_fields": list(helper_task.get("expected_fields") or []),
            "co_worker_provider_locked": False,
            "fallback_depth": 0,
            "breaker_state": "closed",
        }

    return orchestrator.default_inference_provider, {
        "routing_class": req_src,
        "helper_offload_used": False,
        "helper_schema_ok": None,
        "helper_fallback_triggered": False,
        "helper_offload_reason": "default_main_path",
        "helper_task_type": None,
        "helper_expected_fields": None,
        "co_worker_provider_locked": False,
        "fallback_depth": 0,
        "breaker_state": "closed",
    }
