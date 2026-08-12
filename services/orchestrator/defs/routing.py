from typing import Any, Dict

from services.contracts import InferenceResult


def standardize_routing_telemetry(
    orchestrator: Any,
    *,
    selected_provider: str,
    preferred_provider: str | None,
    routing_telemetry: Dict[str, Any],
    result: InferenceResult,
) -> Dict[str, Any]:
    telemetry = dict(routing_telemetry or {})

    if bool(telemetry.get("co_worker_provider_locked")) or orchestrator._active_request_source == "co_worker":
        routing_class = "co_worker"
    elif preferred_provider:
        routing_class = "explicit_override"
    elif bool(telemetry.get("helper_offload_used")):
        routing_class = "npu_helper_offload"
    else:
        routing_class = "agent_core"

    fallback_depth = 0
    if bool(telemetry.get("helper_fallback_triggered")):
        fallback_depth = 1
    if selected_provider == "ll_ol_fallback":
        winner = str(result.winner_provider or "")
        depth_map = {
            "llama_cpp": 0,
            "ollama_gpu": 1,
            "ollama_cpu": 2,
        }
        if winner in depth_map:
            fallback_depth = max(fallback_depth, depth_map[winner])
        elif result.status != "success":
            meta = dict(result.metadata or {})
            if meta.get("tertiary_fallback_error"):
                fallback_depth = max(fallback_depth, 2)
            elif meta.get("fallback_error") or meta.get("secondary_fallback_error"):
                fallback_depth = max(fallback_depth, 1)

    breaker_state = "unknown"
    breaker_meta = dict((result.metadata or {}).get("breaker") or {})
    if breaker_meta:
        breaker_state = str(breaker_meta.get("state") or "unknown")

    telemetry["routing_class"] = routing_class
    telemetry["fallback_depth"] = int(fallback_depth)
    telemetry["breaker_state"] = breaker_state
    return telemetry
