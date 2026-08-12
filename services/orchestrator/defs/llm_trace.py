from typing import Any, Callable, Dict


def attach_llm_trace_metadata(
    *,
    state_mgr: Any,
    llm_response: Dict[str, Any],
    memory_service: Any,
    merge_transition_metadata_fn: Callable[..., None],
    run_state_llm_generation: Any,
) -> None:
    """Attach additive trace metadata for mode/error diagnostics."""
    inference_metadata = dict(llm_response.get("inference_metadata") or {})
    invocation_mode = inference_metadata.get("invocation_mode", "unknown")
    queue_errors = inference_metadata.get("queue_errors") or []

    memory_mode = "service" if memory_service.__class__.__name__ == "RemoteMemoryAdapter" else "in_process"
    trace_metadata = {
        "invocation_mode": invocation_mode,
        "inference_status": llm_response.get("status"),
        "inference_stop_reason": llm_response.get("stop_reason"),
        "memory_mode": memory_mode,
        "memory_adapter": memory_service.__class__.__name__,
        "queue_error_count": len(queue_errors),
    }
    if llm_response.get("error"):
        trace_metadata["inference_error"] = llm_response.get("error")
    if llm_response.get("prompt_debug"):
        trace_metadata["prompt_debug"] = dict(llm_response.get("prompt_debug") or {})
    if llm_response.get("context_debug"):
        trace_metadata["context_debug"] = dict(llm_response.get("context_debug") or {})

    state_mgr.metadata["llm_generation"] = trace_metadata
    merge_transition_metadata_fn(state_mgr, run_state_llm_generation, trace_metadata)
