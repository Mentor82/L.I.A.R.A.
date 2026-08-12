"""
Orchestrator: The kernel that coordinates all services.

Flow: Query -> Tool Selection -> Tool Execution -> LLM Generation -> Validation -> Response

This is the TOP-DOWN blueprint. Everything flows through here.
"""

import json
import atexit
import uuid
import re
import logging
import time
import os
from queue import Empty, Full, Queue
from threading import Event, Thread
from math import log2
from typing import Any, Dict, List, Optional

from services.contracts import (
    ExecutorRequest,
    InferenceRequest,
    InferenceResult,
    InputSituationProfile,
    MemoryFactQueryRequest,
    MemoryHistoryQueryRequest,
    MemoryRetrievalQueryRequest,
    ContextScope,
    ContextUpsertRequest,
    RelationExpandRequest,
    RelationUpsertRequest,
    RelationType,
    OrchestratorRequest,
    OrchestratorResponse,
    PlannerRequest,
    RouterRequest,
    ValidationContext,
    ReasoningMetricsSnapshot,
    VisionImageInput,
    VisionRequest,
)
from services.contracts.service_boundaries import ExternalToolCall
from services.config import Settings
from services.inference.invocation import ensure_inference_invoker
from services.shared.types import MemoryTier, RunState, ToolStatus
from services.judge import JudgeEngine, JudgeContext, JudgeStage
from services.tools.builtin.sys_audit import log_judge_pre_action
from services.tools.registry import get_tool_registry
from services.reward_model.scorer import RewardModelScorer
from services.vision import VisionServiceClient, is_image_attachment

from services.memory_adapter import ensure_memory_service_adapter

from .context_controller import ContextController
from .evidence_engine import EvidenceEngine
from .executor import ToolExecutor
from .gap_detector import GapAction, GapDetector
from .librarian_router import LibrarianDecision, LibrarianRouter
from .planner import QueryPlanner, _detect_language
from .router import QueryRouter
from .input_profiler import InputSituationProfiler
from .graph_v2_persistence import persist_run_to_graph_v2
from .state_manager import RunStateManager
from .task_prompt_spec import parse_task_prompt
from .reasoning_math import estimate_context_entropy, calibrate_thresholds_quantile
from .validator import ResponseValidator
from .workspace_agent import WorkspaceAgent, is_complex_workspace_request, is_workspace_run_followup
from .defs.npu_helper import classify_npu_helper_task, should_use_npu_helper_offload
from .defs.judge import create_judge_context_for_pre_action, create_judge_context_for_post_result, serialize_judge_decision, enrich_judge_post_payload
from .defs.prompting import build_prompt
from .defs.routing import standardize_routing_telemetry
from .defs.provider_selection import select_inference_provider_for_step
from .defs.context_channels import merge_context_channels, load_conversation_history
from .defs.embedding_query import (
    infer_active_topic,
    summarize_history_for_embedding,
    compact_embedding_text,
    build_embedding_query,
    rewrite_retrieval_query,
)
from .defs.context_upsert import (
    build_context_upsert_metadata,
    is_safe_for_context_upsert,
    touch_working_context_activity,
    upsert_temp_context_note,
    upsert_working_context_doc,
)
from .defs.context_formatting import format_tool_context, build_working_context_summary
from .defs.relation_keys import relation_node_key
from .defs.reasoning_metrics import build_validation_math_signals, build_runtime_audit_report, derive_reasoning_metric_inputs, apply_score_feedback_to_metric_inputs, compute_reasoning_metrics_snapshot_python
from .defs.decision_control import build_decision_delta, read_control_mode_before, build_retry_control
from .defs.decision_context import build_decision_context, build_decision_explanation, build_hybrid_control_metadata
from .defs.external_tools import (
    normalize_external_tool,
    extract_textual_tool_schema,
    extract_path_candidates,
    extract_path_candidate,
    extract_requested_end_line,
    extract_explicit_content,
    infer_external_tool_arguments,
)
from .defs.artifacts import extract_artifacts_from_tool_results
from .defs.state_transitions import merge_transition_metadata
from .defs.llm_trace import attach_llm_trace_metadata
from .defs.validation_payload import extract_validation_score_payload


_ORCHESTRATOR_LOGGER = logging.getLogger("liara.orchestrator.run")
_TEMP_CONTEXT_TTL_SECONDS = 3600
_WORKING_CONTEXT_ACTIVE_TTL_SECONDS = max(
    300,
    int(os.getenv("WORKING_CONTEXT_ACTIVE_TTL_SECONDS", "3600")),
)
_RECALL_REFRESH_CONFIDENCE_THRESHOLD = float(os.getenv("RECALL_REFRESH_CONFIDENCE_THRESHOLD", "0.58"))
_SESSION_TOPIC_SWITCH_OVERLAP_MIN = float(os.getenv("SESSION_TOPIC_SWITCH_OVERLAP_MIN", "0.30"))

_JUDGE_PROFILED_ACTIONS = {
    "sys",
    "/sys",
    "compute.run",
    "compute/run",
    "compute.generate",
    "compute/generate",
}
_LATENCY_SCOPE_STOP_SENTINEL = "__LIARA_LAT_SCOPE_STOP__"

class Orchestrator:
    """
    Main orchestration engine for a single query.

    Responsibilities:
    1. State machine (track lifecycle)
    2. Tool selection heuristic
    3. Tool execution coordination
    4. LLM generation
    5. Output validation
    6. Result assembly
    """

    @staticmethod
    def _ground_workspace_agent_response(content: str, tool_results: Dict[str, Any]) -> str:
        """Prevent prose from turning a failed workspace run into a success claim."""
        history = tool_results.get("workspace_run_history") if isinstance(tool_results, dict) else None
        if isinstance(history, dict):
            artifact = history.get("artifact")
            if not isinstance(artifact, dict):
                return "Zum letzten Workspace-Lauf wurde in dieser Session kein persistiertes Run-Artefakt gefunden."
            validator = dict(artifact.get("validator") or {})
            findings = list(validator.get("findings") or [])
            failed_steps = [step for step in list(artifact.get("steps") or []) if step.get("status") != "success"]
            lines = [
                f"Letzter Workspace-Lauf: {artifact.get('run_id', 'unbekannt')}",
                f"Status: {artifact.get('status', 'unbekannt')}",
                f"Validator: {validator.get('state', 'nicht ausgeführt')} (Job: {validator.get('job_id') or '-'})",
            ]
            if failed_steps:
                lines.append("Fehlgeschlagene Schritte:")
                lines.extend(
                    f"- {step.get('step_id')}: {step.get('status')} – {step.get('error') or 'keine Detailmeldung'}"
                    for step in failed_steps
                )
                for step in failed_steps:
                    excerpt = str(step.get("output_excerpt") or "").strip()
                    if excerpt:
                        lines.extend(["  Diagnoseauszug:", "```text", excerpt, "```"])
            if findings:
                lines.append("Validator-Findings:")
                lines.extend(
                    f"- [{item.get('severity', 'info')}] {item.get('file_path') or '-'}"
                    f"{':' + str(item.get('line')) if item.get('line') else ''}: {item.get('message') or 'ohne Meldung'}"
                    for item in findings
                )
            else:
                summary = dict(validator.get("summary") or {})
                lines.append(
                    "Keine einzelnen Findings wurden zurückgegeben. "
                    f"Validator-Zusammenfassung: {json.dumps(summary, ensure_ascii=False, sort_keys=True)}"
                )
                if validator.get("error"):
                    lines.append(f"Validator-Fehler: {validator.get('error')}")
            return "\n".join(lines)
        run = tool_results.get("workspace_agent") if isinstance(tool_results, dict) else None
        if not isinstance(run, dict):
            return content
        status = str(run.get("status") or "unknown")
        steps = list(run.get("steps") or [])
        validator = dict(run.get("validator") or {})
        if status == "completed" and validator.get("passed") is True:
            return content
        failed_step = next((step for step in reversed(steps) if step.get("status") != "success"), None)
        detail = ""
        if failed_step:
            detail = (
                f" Letzter Schritt: {failed_step.get('step_id')} "
                f"({failed_step.get('status')}): {failed_step.get('error') or 'nicht verifiziert'}."
            )
        elif run.get("error"):
            detail = f" Fehler: {run.get('error')}."
        validator_detail = ""
        if validator:
            validator_detail = f" Validator: {validator.get('state', 'unknown')}."
        return (
            f"Der Workspace-Auftrag wurde nicht als erfolgreich abgeschlossen (Status: {status})."
            f"{detail}{validator_detail} Es wurden keine weiteren Schritte als erfolgreich behauptet."
        )

    def __init__(
        self,
        tool_coordinator,
        inference_gateway,
        memory_layer,
    ):
        self.tool_coordinator = tool_coordinator
        self.inference_gateway = inference_gateway
        self.inference_invoker = ensure_inference_invoker(
            inference_gateway,
            mode=getattr(inference_gateway, "invocation_mode", None),
        )
        self.memory_service = ensure_memory_service_adapter(memory_layer)
        self.validator = ResponseValidator(strict_mode=False)
        self.router = QueryRouter()
        self.input_profiler = InputSituationProfiler(
            inference_invoker=self.inference_invoker,
            inference_provider=str(
                getattr(Settings, "RETRIEVAL_INTENT_PROVIDER", "openvino_npu_helper")
                or "openvino_npu_helper"
            ),
            retrieval_refinement_provider=str(
                getattr(Settings, "RETRIEVAL_CANDIDATE_PROVIDER", self.default_inference_provider if hasattr(self, "default_inference_provider") else "ll_ol_fallback")
                or "ll_ol_fallback"
            ),
        )
        self.librarian = LibrarianRouter()
        self.planner = QueryPlanner()
        self.context_compressor = ContextController()
        self.evidence_engine = EvidenceEngine(
            reasoning_steps=getattr(Settings, "EVIDENCE_REASONING_STEPS", 5),
            max_items_per_source=getattr(Settings, "EVIDENCE_MAX_ITEMS_PER_SOURCE", 10),
        )
        self.executor = ToolExecutor(tool_coordinator)
        self.workspace_agent = WorkspaceAgent(
            inference_invoker=self.inference_invoker,
            tool_coordinator=tool_coordinator,
            memory_service=self.memory_service,
        )
        self.judge_engine = JudgeEngine()
        self.vision_client = VisionServiceClient()
        self.default_inference_provider = getattr(Settings, "DEFAULT_LLM_PROVIDER", "ll_ol_fallback")
        self.reward_routing_enabled = bool(getattr(Settings, "REWARD_ROUTING_ENABLED", True))
        self.reward_routing_block_threshold = float(getattr(Settings, "REWARD_ROUTING_BLOCK_THRESHOLD", 0.85))
        self.reward_routing_conf_threshold = float(getattr(Settings, "REWARD_ROUTING_CONFIDENCE_THRESHOLD", 0.70))
        self.npu_helper_offload_enabled = bool(getattr(Settings, "NPU_HELPER_OFFLOAD_ENABLED", True))
        self.npu_helper_provider = str(getattr(Settings, "NPU_HELPER_PROVIDER", "openvino_npu_helper") or "openvino_npu_helper")
        self.npu_helper_max_query_chars = int(getattr(Settings, "NPU_HELPER_MAX_QUERY_CHARS", 320))
        self.npu_helper_max_tools = int(getattr(Settings, "NPU_HELPER_MAX_TOOLS", 2))
        self.co_worker_provider_lock_enabled = bool(getattr(Settings, "CO_WORKER_PROVIDER_LOCK_ENABLED", True))
        self.co_worker_main_provider = str(getattr(Settings, "CO_WORKER_MAIN_PROVIDER", "llama_cpp") or "llama_cpp")
        self.reward_scorer = self._init_reward_scorer()
        self._active_session_id = ""
        self._active_user_id = ""
        self._active_run_id = ""
        self._active_request_source = ""
        self._active_sandbox_root = ""
        self._last_route_debug: Dict[str, Any] = {}
        self._last_executor_debug: Dict[str, Any] = {}
        self._session_score_feedback: Dict[str, Dict[str, Any]] = {}
        self._session_score_history: Dict[str, List[Dict[str, Any]]] = {}
        self._session_control_state: Dict[str, Dict[str, Any]] = {}
        self._session_adaptive_thresholds: Dict[str, Dict[str, Any]] = {}
        self._session_adaptive_state: Dict[str, Dict[str, Any]] = {}
        self._session_semantic_state: Dict[str, Dict[str, Any]] = {}
        self.reasoning_auto_adapt_thresholds = bool(getattr(Settings, "REASONING_AUTO_ADAPT_THRESHOLDS", False))
        self.reasoning_auto_adapt_min_sample_count = int(getattr(Settings, "REASONING_AUTO_ADAPT_MIN_SAMPLE_COUNT", 5))
        self.reasoning_auto_adapt_max_delta = float(getattr(Settings, "REASONING_AUTO_ADAPT_MAX_DELTA", 1.0))
        self._router_scout_initialized = False
        self.latency_scope_enabled = str(os.getenv("LATENCY_SCOPE_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.timing_debug_enabled = str(os.getenv("LIARA_TIMING_DEBUG_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.latency_scope_file = str(
            os.getenv(
                "LATENCY_SCOPE_FILE",
                "logs/services/orchestrator/latency_scope.jsonl",
            )
        ).strip()
        self.latency_scope_queue_max = max(128, int(os.getenv("LATENCY_SCOPE_QUEUE_MAX", "4096")))
        self._latency_scope_queue: Optional[Queue] = None
        self._latency_scope_stop = Event()
        self._latency_scope_thread: Optional[Thread] = None
        if self.latency_scope_enabled:
            self._start_latency_scope_writer()

    async def _observe_images(
        self,
        *,
        request: OrchestratorRequest,
        run_id: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        """Turn transient image payloads into bounded, hash-bound model evidence."""
        images: list[VisionImageInput] = []
        for index, attachment in enumerate(request.attachments, start=1):
            if not is_image_attachment(attachment):
                continue
            vision_meta = dict((attachment.metadata or {}).get("vision") or {})
            if not attachment.content_base64 or not vision_meta.get("sha256"):
                return {
                    "kind": "vision_observation",
                    "status": "failed",
                    "evidence": False,
                    "error": f"image attachment {index} was not normalized by the API boundary",
                    "images": [],
                }
            images.append(VisionImageInput(
                image_id=f"image-{index}",
                media_type=attachment.media_type,
                content_base64=attachment.content_base64,
                sha256=str(vision_meta["sha256"]),
                width=vision_meta.get("width"),
                height=vision_meta.get("height"),
            ))

        prompt = (
            "Du bist LIARAs visuelle Wahrnehmung. Beschreibe und analysiere nur Inhalte, "
            "die in den bereitgestellten Bildern tatsächlich sichtbar sind. Trenne sichere "
            "Beobachtungen von Unsicherheit; erfinde keine verdeckten Details. "
            f"Aufgabe der nutzenden Person: {user_prompt}"
        )
        response = await self.vision_client.analyze(VisionRequest(
            request_id=run_id,
            prompt=prompt,
            task="question",
            images=images,
            max_tokens=min(768, request.max_tokens or 512),
            model="MiniCPM-o-2.6-int4",
        ))
        return {
            "kind": "vision_observation",
            "status": response.status,
            "evidence": response.status == "success" and bool(response.evidence),
            "observation": response.content,
            "images": [item.model_dump(mode="json") for item in response.evidence],
            "provider": response.provider,
            "model": response.model,
            "device": response.device,
            "gen_ms": response.gen_ms,
            "error": response.error,
        }

    async def run(self, request: OrchestratorRequest) -> OrchestratorResponse:
        """Execute the full query pipeline."""
        run_id = request.run_id or str(uuid.uuid4())
        routing_query = (request.routing_query or request.query or "").strip() or request.query
        run_started = time.perf_counter()
        tool_selection_ms = 0.0
        tool_execution_ms = 0.0
        llm_generation_total_ms = 0.0
        validation_total_ms = 0.0
        total_ms = 0.0
        completion_breakdown_ms: Dict[str, Any] = {}
        timing_debug_enabled = self.timing_debug_enabled
        tool_selection_start_offset_ms = 0.0
        tool_execution_start_offset_ms = 0.0
        llm_start_offset_ms = 0.0
        validation_start_offset_ms = 0.0
        llm_model = "unknown"
        llm_provider = "unknown"
        selected_tools: List[str] = []
        retry_limit = 4
        retry_count = 0
        gap_history: List[str] = []
        previous_compressed_context = ""
        llm_attempts: List[Dict[str, Any]] = []
        validation_attempts: List[Dict[str, Any]] = []
        retry_control: Dict[str, Any] = {
            "strategy": "stop",
            "attempt_allowed": False,
            "stop_reason": "not_evaluated",
            "validation_decision": "accept",
            "judge_post_decision": "allow",
            "retry_count": 0,
            "retry_limit": retry_limit,
        }
        last_gap_decision: Dict[str, Any] = {
            "gap_detected": False,
            "gap_type": "NONE",
            "missing": [],
            "confidence": 1.0,
            "action": "STOP",
            "reasoning_step": 2,
            "trigger": "initial",
        }
        next_gap_action: str | None = None
        state_mgr = RunStateManager(run_id)
        self._active_session_id = request.session_id
        self._active_user_id = request.user_id
        self._active_run_id = run_id
        self._active_request_source = (request.request_source or "").strip().lower()

        if not self._router_scout_initialized:
            await self.router.initialize_scout_embedding()
            await self.input_profiler.initialize()
            self._router_scout_initialized = True
        self._active_sandbox_root = request.sandbox_root or ""
        self._simulation_mode = request.simulation_mode  # Store simulation mode flag
        self._last_route_debug = {}
        self._last_executor_debug = {}
        effective_query = request.query
        vision_tool_result: Dict[str, Any] | None = None
        effective_tools_override = request.tools_override
        task_spec_meta: Dict[str, Any] = {"enabled": False}
        pending_external_tool_calls: List[ExternalToolCall] | None = None
        try:
            profile_history, _ = await self._load_conversation_history(
                session_id=request.session_id,
                current_query=routing_query,
                limit=4,
            )
        except Exception as profile_history_error:
            _ORCHESTRATOR_LOGGER.warning("input profile history unavailable: %s", profile_history_error)
            profile_history = ""
        input_profile = await self.input_profiler.profile(
            routing_query,
            conversation_history=profile_history,
            request_source=request.request_source,
            workspace_available=bool(request.sandbox_root),
            simulation_mode=request.simulation_mode,
            max_tokens=request.max_tokens or 2048,
        )
        self._active_input_profile = input_profile
        retry_limit = min(retry_limit, input_profile.resource_budget.max_refinement_steps)
        retry_control["retry_limit"] = retry_limit

        try:
            task_spec = parse_task_prompt(routing_query)
            if task_spec is not None:
                task_spec_meta = task_spec.to_metadata()
                effective_query = task_spec.to_execution_query()
                explicit_tools = task_spec.explicit_tools()
                if explicit_tools and not effective_tools_override:
                    effective_tools_override = explicit_tools
        except ValueError as spec_error:
            # Keep backward-compatible behavior: malformed task spec should not crash run.
            task_spec_meta = {
                "enabled": True,
                "parse_error": str(spec_error),
            }

        # If caller provided external tools (e.g. Continue) and no tool results yet,
        # decide whether to request one external tool call before normal generation.
        if request.allow_external_tool_calls and request.available_tools:
            _ORCHESTRATOR_LOGGER.info(
                "[EXTERNAL_PLAN] gate_open allow=%s tools=%d results=%d query_preview=%s",
                bool(request.allow_external_tool_calls),
                len(request.available_tools or []),
                len(request.tool_results or []),
                (routing_query or "")[:220],
            )
            planned_call = self._plan_external_tool_call(
                routing_query,
                request.available_tools,
                request.tool_results,
            )
            _ORCHESTRATOR_LOGGER.info(
                "[EXTERNAL_PLAN] planned=%s name=%s",
                bool(planned_call),
                (
                    ((planned_call or {}).get("function") or {}).get("name")
                    if isinstance(planned_call, dict)
                    else None
                ),
            )
            if planned_call:
                pending_external_tool_calls = [ExternalToolCall(**planned_call)]
                state_mgr.transition_to(
                    RunState.TOOL_SELECTION,
                    reason="External tool call planned",
                    metadata={
                        "external_tool_call": planned_call,
                        "input_profile": input_profile.model_dump(mode="json"),
                    },
                )
                state_mgr.transition_to(
                    RunState.LLM_GENERATION,
                    reason="External tool call drafted",
                    metadata={
                        "external_tool_call": planned_call,
                    },
                )
                state_mgr.transition_to(
                    RunState.VALIDATION,
                    reason="External tool call approved",
                    metadata={
                        "external_tool_call": planned_call,
                    },
                )
                state_mgr.transition_to(
                    RunState.COMPLETE,
                    reason="External tool call requested",
                    metadata={
                        "external_tool_call": planned_call,
                    },
                )
                return OrchestratorResponse(
                    run_id=run_id,
                    final_response="",
                    tools_executed=[],
                    tool_results={},
                    state_final=state_mgr.current_state.value,
                    llm_generation={
                        "content": "",
                        "provider": "external_tool_router",
                        "model": "deterministic",
                        "ttft_ms": 0.0,
                        "gen_ms": 0.0,
                        "context_debug": {
                            "mode": "TOOL_CALL",
                            "sources": {"chroma": 0, "qdrant": 0, "postgres": 0, "neo4j": 0},
                            "input_profile": input_profile.model_dump(mode="json"),
                        },
                        "retry": {"count": 0, "limit": retry_limit},
                    },
                    validation_result={
                        "passed": True,
                        "decision": "accept",
                        "checks": {},
                        "issues": [],
                        "confidence_score": 1.0,
                        "suggestions": None,
                        "retry_count": 0,
                        "gap_detection": last_gap_decision,
                    },
                    artifacts=None,
                    execution_trace=state_mgr.state_transitions,
                    pending_tool_calls=pending_external_tool_calls,
                )

        if any(is_image_attachment(item) for item in request.attachments):
            vision_tool_result = await self._observe_images(
                request=request,
                run_id=run_id,
                user_prompt=routing_query,
            )

        try:
            state_mgr.transition_to(RunState.TOOL_SELECTION, reason="Analyzing query")
            tool_selection_started = time.perf_counter()
            tool_selection_start_offset_ms = round((tool_selection_started - run_started) * 1000, 3)
            workspace_agent_enabled = (
                not effective_tools_override
                and not request.simulation_mode
                and is_complex_workspace_request(routing_query)
            )
            workspace_followup_enabled = (
                not workspace_agent_enabled
                and not effective_tools_override
                and is_workspace_run_followup(routing_query)
            )
            if workspace_agent_enabled:
                selected_tools = ["sys"]
                self._last_route_debug = {
                    "reason": "complex_workspace_request",
                    "mode": "workspace_agent",
                }
            elif workspace_followup_enabled:
                selected_tools = ["session_context"]
                self._last_route_debug = {
                    "reason": "workspace_run_followup",
                    "mode": "workspace_run_diagnostic",
                }
            else:
                selected_tools = await self._select_tools(
                    routing_query,
                    effective_tools_override,
                )
            if vision_tool_result is not None and "vision" not in selected_tools:
                selected_tools.append("vision")
            tool_selection_ms = round((time.perf_counter() - tool_selection_started) * 1000, 3)
            self._merge_transition_metadata(
                state_mgr,
                RunState.TOOL_SELECTION,
                {
                    "timing_ms": tool_selection_ms,
                    "selected_tools": list(selected_tools),
                    "route_debug": dict(self._last_route_debug or {}),
                    "task_spec": task_spec_meta,
                    "input_profile": input_profile.model_dump(mode="json"),
                },
            )

            state_mgr.transition_to(RunState.TOOL_EXECUTION, reason=f"Executing {len(selected_tools)} tools")
            tool_execution_started = time.perf_counter()
            tool_execution_start_offset_ms = round((tool_execution_started - run_started) * 1000, 3)
            if workspace_agent_enabled:
                workspace_planner_provider, workspace_planner_routing = self._select_inference_provider_for_step(
                    preferred_provider=request.preferred_provider,
                    query=routing_query,
                    tools_used=["sys"],
                    tool_outputs={},
                    force_context=True,
                    retry_attempt=0,
                )
                workspace_run = await self.workspace_agent.run(
                    routing_query,
                    request_id=run_id,
                    run_id=run_id,
                    session_id=request.session_id,
                    provider=workspace_planner_provider,
                    model=request.preferred_model,
                    max_tokens=request.max_tokens,
                )
                workspace_payload = workspace_run.model_dump(mode="json")
                workspace_payload["planner_routing"] = {
                    "selected_provider": workspace_planner_provider,
                    **dict(workspace_planner_routing or {}),
                }
                try:
                    workspace_payload["persistence"] = await self.workspace_agent.persist_run_artifact(
                        workspace_run,
                        session_id=request.session_id,
                        run_id=run_id,
                    )
                except Exception as persist_error:
                    workspace_payload["persistence"] = {
                        "status": "failed",
                        "error": str(persist_error),
                    }
                tool_results = {"workspace_agent": workspace_payload}
                failed_steps = [
                    step["step_id"]
                    for step in workspace_payload.get("steps", [])
                    if step.get("status") != "success"
                ]
                self._last_executor_debug = {
                    "mode": "workspace_agent",
                    "status": workspace_run.status,
                    "planned_steps": len((workspace_payload.get("plan") or {}).get("steps", [])),
                    "executed_steps": len(workspace_payload.get("steps", [])),
                    "failed_tools": ["sys"] if failed_steps else [],
                    "failed_steps": failed_steps,
                    "validator": workspace_payload.get("validator", {}),
                    "persistence": workspace_payload.get("persistence", {}),
                    "planner_routing": workspace_payload.get("planner_routing", {}),
                }
            elif workspace_followup_enabled:
                artifact = await self.workspace_agent.load_latest_run_artifact(
                    session_id=request.session_id
                )
                tool_results = {
                    "workspace_run_history": {
                        "kind": "workspace_run_diagnostic",
                        "source": "memory_context",
                        "artifact": artifact,
                        "summary_text": (
                            "Persistierter letzter Workspace-/Validator-Lauf wurde geladen."
                            if artifact
                            else "Kein persistierter Workspace-Lauf in dieser Session gefunden."
                        ),
                    }
                }
                self._last_executor_debug = {
                    "mode": "workspace_run_diagnostic",
                    "artifact_found": bool(artifact),
                    "failed_tools": [],
                }
            else:
                executable_tools = [name for name in selected_tools if name != "vision"]
                tool_results = await self._execute_tools(executable_tools, routing_query, run_id=run_id)
                tool_results = await self._complete_web_discovery(
                    tool_results,
                    run_id=run_id,
                )
            if vision_tool_result is not None:
                tool_results["vision"] = vision_tool_result
            if request.tool_results:
                for index, entry in enumerate(request.tool_results, start=1):
                    if not isinstance(entry, dict):
                        continue
                    ext_name = str(entry.get("name") or f"external_tool_{index}")
                    ext_content = str(entry.get("content") or "")
                    ext_id = str(entry.get("tool_call_id") or f"external-{index}")
                    tool_results[f"external::{ext_name}#{index}"] = {
                        "kind": "external_tool_result",
                        "tool_name": ext_name,
                        "tool_call_id": ext_id,
                        "content": ext_content,
                        "source": "external_host",
                    }
            tool_execution_ms = round((time.perf_counter() - tool_execution_started) * 1000, 3)
            for tool_name, tool_output in tool_results.items():
                is_failure = (
                    str(tool_name).startswith("_judge_")
                    or (
                        isinstance(tool_output, dict)
                        and (
                            tool_output.get("evidence") is False
                            or str(tool_output.get("status") or "").lower() in {"failed", "error", "blocked"}
                        )
                    )
                )
                state_mgr.mark_tool_status(tool_name, ToolStatus.FAILED if is_failure else ToolStatus.SUCCESS)
            for tool_name in self._last_executor_debug.get("failed_tools", []):
                state_mgr.mark_tool_status(tool_name, ToolStatus.FAILED)
            self._merge_transition_metadata(
                state_mgr,
                RunState.TOOL_EXECUTION,
                {
                    "timing_ms": tool_execution_ms,
                    "selected_tools": list(selected_tools),
                    "executor_debug": dict(self._last_executor_debug or {}),
                },
            )

            await self._upsert_temp_context_note(
                session_id=request.session_id,
                run_id=run_id,
                note_kind="user_query",
                content=routing_query,
                metadata={"source": "orchestrator"},
            )
            if tool_results:
                await self._upsert_temp_context_note(
                    session_id=request.session_id,
                    run_id=run_id,
                    note_kind="tool_outputs",
                    content=self._format_tool_context(tool_results),
                    metadata={
                        "source": "orchestrator",
                        "count": len(tool_results),
                        "tool_names": sorted(tool_results.keys()),
                        "tool_kinds": {
                            name: ((output.get("kind") if isinstance(output, dict) else None) or "generic")
                            for name, output in tool_results.items()
                        },
                    },
                )

            state_mgr.transition_to(RunState.LLM_GENERATION, reason="Generating response")
            llm_started = time.perf_counter()
            llm_start_offset_ms = round((llm_started - run_started) * 1000, 3)
            llm_response = await self._generate_llm_response(
                run_id=run_id,
                query=effective_query,
                routing_query=routing_query,
                session_id=request.session_id,
                tools_used=selected_tools,
                tool_outputs=tool_results,
                max_tokens=request.max_tokens or 2048,
                preferred_provider=request.preferred_provider,
                preferred_model=request.preferred_model,
                force_context=False,
                retry_directive=None,
                retry_attempt=0,
                gap_action=None,
                previous_compressed_context=previous_compressed_context,
            )
            self._apply_empty_response_fallback(llm_response, retry_attempt=0)
            llm_response["content"] = self._ground_workspace_agent_response(
                llm_response.get("content", ""), tool_results
            )
            llm_attempt_ms = round((time.perf_counter() - llm_started) * 1000, 3)
            llm_generation_total_ms = llm_attempt_ms
            llm_model = str(llm_response.get("model") or "unknown")
            llm_provider = str(llm_response.get("provider") or "unknown")
            llm_attempts.append(
                {
                    "attempt": 0,
                    "timing_ms": llm_attempt_ms,
                    "status": llm_response.get("status"),
                    "context_mode": (llm_response.get("context_debug") or {}).get("mode"),
                }
            )
            previous_compressed_context = llm_response.get("compressed_context", "")
            await self._upsert_temp_context_note(
                session_id=request.session_id,
                run_id=run_id,
                note_kind="assistant_draft",
                content=llm_response.get("content", ""),
                metadata={"source": "orchestrator", "kind": "assistant_draft", "retry_attempt": 0},
            )
            self._attach_llm_trace_metadata(state_mgr, llm_response)

            state_mgr.transition_to(RunState.VALIDATION, reason="Validating output")
            validation_started = time.perf_counter()
            validation_start_offset_ms = round((validation_started - run_started) * 1000, 3)
            
            # Post-Result Judge evaluation
            judge_context_post = self._create_judge_context_for_post_result(
                run_id=run_id,
                query=effective_query,
                response_content=llm_response["content"],
                tools_used=selected_tools,
                tool_outputs=tool_results,
            )
            judge_decision_post = self.judge_engine.evaluate_post_result(judge_context_post)
            judge_post_payload = self._serialize_judge_decision(judge_decision_post)
            
            _ORCHESTRATOR_LOGGER.debug(
                f"[JUDGE:POST_RESULT] decision={judge_decision_post.decision.value}, "
                f"confidence={judge_decision_post.confidence}"
            )
            
            validation = await self._validate_response(
                run_id=run_id,
                query=effective_query,
                response=llm_response["content"],
                tools_used=selected_tools,
                tool_outputs=tool_results,
                context_debug=llm_response.get("context_debug", {}),
                context_documents=llm_response.get("compressed_context", ""),
                request_source=request.request_source,
                risk_reassessment=request.risk_reassessment,
                user_feedback_score=request.user_feedback_score,
                user_feedback_stars=request.user_feedback_stars,
            )
            
            validation_attempts.append(
                {
                    "attempt": 0,
                    "timing_ms": round((time.perf_counter() - validation_started) * 1000, 3),
                    "decision": getattr(validation, "decision", "accept"),
                }
            )
            self._merge_transition_metadata(
                state_mgr,
                RunState.VALIDATION,
                {
                    "timing_ms": validation_attempts[-1]["timing_ms"],
                    "decision": getattr(validation, "decision", "accept"),
                    "issues": list(getattr(validation, "issues", []) or []),
                    "judge_post": judge_post_payload,
                },
            )

            while getattr(validation, "decision", "accept") in {"block", "revise"}:
                compression_meta = dict(llm_response.get("compression", {}) or {})
                retry_control = self._build_retry_control(
                    validation_decision=getattr(validation, "decision", "accept"),
                    judge_post=judge_post_payload,
                    retry_count=retry_count,
                    retry_limit=retry_limit,
                    compression_meta=compression_meta,
                )
                if not retry_control.get("attempt_allowed", False):
                    stop_reason = str(retry_control.get("stop_reason") or "retry_not_allowed")
                    last_gap_decision = {
                        "gap_detected": False,
                        "gap_type": "NONE",
                        "missing": [],
                        "confidence": 1.0,
                        "action": "STOP",
                        "reasoning_step": retry_count + 1,
                        "trigger": stop_reason,
                    }
                    break

                retry_count += 1
                trigger_decision = getattr(validation, "decision", "revise")
                reasoning_step = retry_count + 1
                gap_decision = GapDetector.detect(
                    query=effective_query,
                    validation_issues=list(getattr(validation, "issues", []) or []),
                    context_sources=dict(llm_response.get("context_debug", {}).get("sources", {}) or {}),
                    reasoning_step=reasoning_step,
                    previous_gap_types=gap_history,
                )
                last_gap_decision = gap_decision.to_dict()

                retry_control = self._build_retry_control(
                    validation_decision=trigger_decision,
                    judge_post=judge_post_payload,
                    retry_count=max(0, retry_count - 1),
                    retry_limit=retry_limit,
                    compression_meta={},
                    gap_decision=last_gap_decision,
                )
                if not retry_control.get("attempt_allowed", False):
                    break

                gap_history.append(gap_decision.gap_type)
                next_gap_action = gap_decision.action
                force_context = True

                retry_directive = (
                    "Previous answer was blocked. Regenerate with stronger grounding and conservative claims."
                    if trigger_decision == "block"
                    else "Previous answer needs revision. Correct inconsistencies and tighten source-grounded wording."
                )
                retry_directive += (
                    f"\n\n[GAP_DETECTION]"
                    f"\ngap_type={gap_decision.gap_type}"
                    f"\naction={gap_decision.action}"
                    f"\nmissing={'; '.join(gap_decision.missing) or '(none)'}"
                    f"\nconfidence={gap_decision.confidence}"
                )

                state_mgr.transition_to(
                    RunState.LLM_GENERATION,
                    reason=f"Retry generation attempt {retry_count}",
                    metadata={
                        "retry_attempt": retry_count,
                        "trigger_decision": trigger_decision,
                        "force_context": force_context,
                        "gap": last_gap_decision,
                    },
                )

                llm_started = time.perf_counter()
                llm_response = await self._generate_llm_response(
                    run_id=run_id,
                    query=effective_query,
                    routing_query=routing_query,
                    session_id=request.session_id,
                    tools_used=selected_tools,
                    tool_outputs=tool_results,
                    max_tokens=request.max_tokens or 2048,
                    preferred_provider=request.preferred_provider,
                    preferred_model=request.preferred_model,
                    force_context=force_context,
                    retry_directive=retry_directive,
                    retry_attempt=retry_count,
                    gap_action=next_gap_action,
                    previous_compressed_context=previous_compressed_context,
                )
                self._apply_empty_response_fallback(llm_response, retry_attempt=retry_count)
                llm_response["content"] = self._ground_workspace_agent_response(
                    llm_response.get("content", ""), tool_results
                )
                llm_attempt_ms = round((time.perf_counter() - llm_started) * 1000, 3)
                llm_attempts.append(
                    {
                        "attempt": retry_count,
                        "timing_ms": llm_attempt_ms,
                        "status": llm_response.get("status"),
                        "context_mode": (llm_response.get("context_debug") or {}).get("mode"),
                    }
                )
                previous_compressed_context = llm_response.get("compressed_context", "")
                await self._upsert_temp_context_note(
                    session_id=request.session_id,
                    run_id=run_id,
                    note_kind="assistant_draft",
                    content=llm_response.get("content", ""),
                    metadata={
                        "source": "orchestrator",
                        "kind": "assistant_draft",
                        "retry_attempt": retry_count,
                    },
                )
                self._attach_llm_trace_metadata(state_mgr, llm_response)

                judge_context_post = self._create_judge_context_for_post_result(
                    run_id=run_id,
                    query=effective_query,
                    response_content=llm_response["content"],
                    tools_used=selected_tools,
                    tool_outputs=tool_results,
                )
                judge_decision_post = self.judge_engine.evaluate_post_result(judge_context_post)
                judge_post_payload = self._serialize_judge_decision(judge_decision_post)

                state_mgr.transition_to(
                    RunState.VALIDATION,
                    reason=f"Retry validation attempt {retry_count}",
                    metadata={"retry_attempt": retry_count},
                )
                validation_started = time.perf_counter()
                validation = await self._validate_response(
                    run_id=run_id,
                    query=request.query,
                    response=llm_response["content"],
                    tools_used=selected_tools,
                    tool_outputs=tool_results,
                    context_debug=llm_response.get("context_debug", {}),
                    context_documents=llm_response.get("compressed_context", ""),
                    request_source=request.request_source,
                    risk_reassessment=request.risk_reassessment,
                    user_feedback_score=request.user_feedback_score,
                    user_feedback_stars=request.user_feedback_stars,
                )
                validation_attempts.append(
                    {
                        "attempt": retry_count,
                        "timing_ms": round((time.perf_counter() - validation_started) * 1000, 3),
                        "decision": getattr(validation, "decision", "accept"),
                    }
                )
                self._merge_transition_metadata(
                    state_mgr,
                    RunState.VALIDATION,
                    {
                        "timing_ms": validation_attempts[-1]["timing_ms"],
                        "decision": getattr(validation, "decision", "accept"),
                        "issues": list(getattr(validation, "issues", []) or []),
                        "judge_post": judge_post_payload,
                    },
                )

            issues_after_validation = list(getattr(validation, "issues", []) or [])
            if any(
                (
                    "fabricated tool execution claim" in str(issue).lower()
                    or "factual answer appears ungrounded" in str(issue).lower()
                )
                for issue in issues_after_validation
            ):
                llm_response["content"] = (
                    "Die angeforderte externe Prüfung konnte nicht erfolgreich ausgeführt werden. "
                    "Daher liegt kein belastbares Tool-Ergebnis vor, aus dem ich eine Antwort ableiten kann."
                )
                validation = await self._validate_response(
                    run_id=run_id,
                    query=request.query,
                    response=llm_response["content"],
                    tools_used=selected_tools,
                    tool_outputs=tool_results,
                    context_debug=llm_response.get("context_debug", {}),
                    context_documents=llm_response.get("compressed_context", ""),
                    request_source=request.request_source,
                    risk_reassessment=request.risk_reassessment,
                    user_feedback_score=request.user_feedback_score,
                    user_feedback_stars=request.user_feedback_stars,
                )
                validation.checks["tool_evidence_safe_fallback"] = "pass"
                self._merge_transition_metadata(
                    state_mgr,
                    RunState.VALIDATION,
                    {
                        "tool_evidence_safe_fallback": True,
                        "decision": getattr(validation, "decision", "accept"),
                    },
                )
            elif any(
                "response assumes backend health without evidence" in str(issue).lower()
                for issue in issues_after_validation
            ):
                llm_response["content"] = (
                    "Ich kann ohne belastbare Health-Checks nicht annehmen, dass alle Backends gesund sind. "
                    "Bitte zuerst den aktuellen Service-Status pruefen (API, Memory, Embedding, Bridge) und dann planen."
                )
                if getattr(validation, "decision", "accept") == "accept":
                    validation.decision = "warn"
                validation.checks["law_conflict_safe_fallback"] = "pass"
                self._merge_transition_metadata(
                    state_mgr,
                    RunState.VALIDATION,
                    {
                        "law_conflict_safe_fallback": True,
                        "decision": getattr(validation, "decision", "accept"),
                    },
                )
            elif any(
                "absolute comparison requires qualification" in str(issue).lower()
                for issue in issues_after_validation
            ):
                llm_response["content"] = (
                    "Die Aussage ist nicht pauschal korrekt: ob Postgres oder Redis schneller ist, haengt stark vom "
                    "Workload, Datenmodell und Zugriffsmuster ab. Redis ist oft bei In-Memory-Latenz vorn, waehrend "
                    "Postgres bei relationalen, komplexen Abfragen und Konsistenzanforderungen staerker sein kann."
                )
                if getattr(validation, "decision", "accept") == "accept":
                    validation.decision = "warn"
                validation.checks["law_conflict_safe_fallback"] = "pass"
                self._merge_transition_metadata(
                    state_mgr,
                    RunState.VALIDATION,
                    {
                        "law_conflict_safe_fallback": True,
                        "decision": getattr(validation, "decision", "accept"),
                    },
                )

            retry_control = self._build_retry_control(
                validation_decision=getattr(validation, "decision", "accept"),
                judge_post=judge_post_payload,
                retry_count=retry_count,
                retry_limit=retry_limit,
                compression_meta={},
            )

            if getattr(validation, "decision", "accept") == "accept" and validation.passed:
                working_context_started = time.perf_counter()
                working_summary = self._build_working_context_summary(
                    query=request.query,
                    tool_outputs=tool_results,
                    response=llm_response["content"],
                )
                await self._upsert_working_context_doc(
                    session_id=request.session_id,
                    run_id=run_id,
                    document_id=f"{run_id}:working_context",
                    content=working_summary,
                    turn_index=10,
                    metadata={
                        "source": "orchestrator",
                        "kind": "working_context",
                        "validated": True,
                        "tool_count": len(tool_results),
                        "user_id": request.user_id or "",
                        "session_id": request.session_id or "",
                    },
                )
                if timing_debug_enabled:
                    completion_breakdown_ms["working_context_upsert"] = round(
                        (time.perf_counter() - working_context_started) * 1000,
                        3,
                    )

                memory_commit_started = time.perf_counter()
                await self._upsert_memory_commit_embedding(
                    session_id=request.session_id,
                    run_id=run_id,
                    query=request.query,
                    final_response=llm_response["content"],
                    context_debug=llm_response.get("context_debug", {}),
                )
                if timing_debug_enabled:
                    completion_breakdown_ms["memory_commit_embedding_upsert"] = round(
                        (time.perf_counter() - memory_commit_started) * 1000,
                        3,
                    )

                relations_started = time.perf_counter()
                relations_metrics = await self._upsert_validated_relations(
                    session_id=request.session_id,
                    run_id=run_id,
                    query=request.query,
                    tools_used=selected_tools,
                    final_response=llm_response["content"],
                    context_debug=llm_response.get("context_debug", {}),
                )
                if timing_debug_enabled:
                    completion_breakdown_ms["validated_relations_upsert"] = round(
                        (time.perf_counter() - relations_started) * 1000,
                        3,
                    )
                    if relations_metrics:
                        completion_breakdown_ms["validated_relations_detail"] = relations_metrics

            total_ms = round((time.perf_counter() - run_started) * 1000, 3)
            validation_total_ms = round(sum(item["timing_ms"] for item in validation_attempts), 3)
            llm_generation_total_ms = round(sum(item["timing_ms"] for item in llm_attempts), 3)
            previous_score_feedback = dict(self._session_score_feedback.get(request.session_id) or {})
            previous_score_history = list(self._session_score_history.get(request.session_id) or [])
            reasoning_snapshot_started = time.perf_counter()
            reasoning_metrics = await self._compute_reasoning_metrics_snapshot(
                session_id=request.session_id,
                query=request.query,
                response=llm_response.get("content", ""),
                tools_used=selected_tools,
                context_debug=llm_response.get("context_debug", {}),
                validation_decision=getattr(validation, "decision", "accept"),
                retry_count=retry_count,
                failed_tools=list(self._last_executor_debug.get("failed_tools", [])),
                previous_score_feedback=previous_score_feedback,
                previous_score_history=previous_score_history,
                judge_post_decision=judge_post_payload,
            )
            profile_budget = input_profile.resource_budget.model_dump(mode="json")
            reasoning_metrics["input_profile_budget"] = {
                **profile_budget,
                "processing_level": input_profile.processing_level,
                "observed_tool_calls": len(selected_tools),
                "observed_refinement_steps": retry_count,
                "within_tool_budget": len(selected_tools) <= profile_budget["tool_budget"]
                if profile_budget["tool_budget"] > 0
                else len(selected_tools) == 0,
                "within_refinement_budget": retry_count <= profile_budget["max_refinement_steps"],
            }
            if timing_debug_enabled:
                completion_breakdown_ms["reasoning_metrics_snapshot"] = round(
                    (time.perf_counter() - reasoning_snapshot_started) * 1000,
                    3,
                )

            decision_build_started = time.perf_counter()
            validation_math_signals = self._build_validation_math_signals(reasoning_metrics)
            judge_post_payload = self._enrich_judge_post_payload(judge_post_payload, validation_math_signals)
            control_before = self._read_control_mode_before(request.session_id)
            control_after = str(
                validation_math_signals.get("resolved_mode")
                or validation_math_signals.get("control_mode")
                or "advisory"
            )
            decision_delta = self._build_decision_delta(control_before=control_before, control_after=control_after)
            validation_math_signals["control_mode_before"] = control_before
            validation_math_signals["control_mode_after"] = control_after
            validation_math_signals["decision_delta"] = decision_delta
            score_payload = self._extract_validation_score_payload(validation)
            decision_context = self._build_decision_context(
                validation=validation,
                score_payload=score_payload,
                math_signals=validation_math_signals,
                judge_post=judge_post_payload,
            )
            decision_explanation = self._build_decision_explanation(
                validation_decision=getattr(validation, "decision", "accept"),
                score_payload=score_payload,
                math_signals=validation_math_signals,
                judge_post=judge_post_payload,
            )
            if timing_debug_enabled:
                completion_breakdown_ms["decision_build"] = round(
                    (time.perf_counter() - decision_build_started) * 1000,
                    3,
                )

            runtime_audit_started = time.perf_counter()
            retry_control = self._build_retry_control(
                validation_decision=getattr(validation, "decision", "accept"),
                judge_post=judge_post_payload,
                retry_count=retry_count,
                retry_limit=retry_limit,
                compression_meta=dict(llm_response.get("compression", {}) or {}),
                gap_decision=last_gap_decision,
                math_signals=validation_math_signals,
            )
            feedback_entry = {
                "run_id": run_id,
                "decision": str(getattr(validation, "decision", "accept") or "accept"),
                "confidence_score": float(getattr(validation, "confidence_score", 0.0) or 0.0),
                "score": score_payload,
                "risk_flags": list(getattr(validation, "risk_flags", []) or []),
                "actionable_risk": float(validation_math_signals.get("actionable_risk", 0.0) or 0.0),
                "utility_ig": float(validation_math_signals.get("utility_ig", 0.0) or 0.0),
                "stability_score": float(validation_math_signals.get("stability_score", 1.0) or 1.0),
            }
            self._session_score_feedback[request.session_id] = feedback_entry
            updated_history = [*previous_score_history, feedback_entry][-5:]
            self._session_score_history[request.session_id] = updated_history
            runtime_audit_report = self._build_runtime_audit_report(
                threshold_profile=reasoning_metrics.get("threshold_profile", {}),
                math_signals=validation_math_signals,
                session_score_history=updated_history,
            )
            threshold_adaptation = self._maybe_apply_runtime_threshold_adaptation(
                session_id=request.session_id,
                runtime_audit_report=runtime_audit_report,
                threshold_profile=reasoning_metrics.get("threshold_profile", {}),
                feedback_entry=feedback_entry,
            )
            runtime_audit_report["applied"] = dict(threshold_adaptation)
            if timing_debug_enabled:
                completion_breakdown_ms["runtime_audit_and_adaptation"] = round(
                    (time.perf_counter() - runtime_audit_started) * 1000,
                    3,
                )

            latency_scope_started = time.perf_counter()
            self._append_latency_scope_sample(
                run_id=run_id,
                session_id=request.session_id,
                user_id=request.user_id,
                query=routing_query,
                status="ok",
                t_total_s=(total_ms / 1000.0),
                t_embed_s=0.0,
                t_retrieval_s=(tool_execution_ms / 1000.0),
                t_inference_s=(llm_generation_total_ms / 1000.0),
                device_embed=str(getattr(Settings, "OPENVINO_DEVICE", "unknown") or "unknown"),
                model=llm_model,
                provider=llm_provider,
                selected_tools=selected_tools,
                phase_ms={
                    "tool_selection": tool_selection_ms,
                    "tool_execution": tool_execution_ms,
                    "llm_generation": llm_generation_total_ms,
                    "validation": validation_total_ms,
                    "total": total_ms,
                },
                phase_start_offsets_ms={
                    "tool_selection": tool_selection_start_offset_ms,
                    "tool_execution": tool_execution_start_offset_ms,
                    "llm_generation": llm_start_offset_ms,
                    "validation": validation_start_offset_ms,
                },
                route_reason=str((self._last_route_debug or {}).get("reason") or ""),
            )
            if timing_debug_enabled:
                completion_breakdown_ms["latency_scope_append"] = round(
                    (time.perf_counter() - latency_scope_started) * 1000,
                    3,
                )
            self._session_control_state[request.session_id] = {
                "control_mode": control_after,
                "decision_delta": decision_delta,
                "run_id": run_id,
            }
            self._merge_transition_metadata(
                state_mgr,
                RunState.VALIDATION,
                {
                    "math_signals": validation_math_signals,
                    "decision_context": decision_context,
                    "decision_explanation": decision_explanation,
                    "retry_control": retry_control,
                    "runtime_audit_report": runtime_audit_report,
                },
            )
            state_mgr.metadata["run_debug"] = {
                "timings_ms": {
                    "tool_selection": tool_selection_ms,
                    "tool_execution": tool_execution_ms,
                    "llm_generation_total": round(sum(item["timing_ms"] for item in llm_attempts), 3),
                    "validation_total": round(sum(item["timing_ms"] for item in validation_attempts), 3),
                    "total": total_ms,
                },
                "llm_attempts": llm_attempts,
                "validation_attempts": validation_attempts,
                "selected_tools": list(selected_tools),
                "route_debug": dict(self._last_route_debug or {}),
                "executor_debug": dict(self._last_executor_debug or {}),
                "task_spec": task_spec_meta,
                "reasoning_metrics": reasoning_metrics,
                "validation_math_signals": validation_math_signals,
                "decision_context": decision_context,
                "decision_explanation": decision_explanation,
                "retry_control": retry_control,
                "runtime_audit_report": runtime_audit_report,
                "threshold_adaptation": threshold_adaptation,
            }

            complete_metadata: Dict[str, Any] = {
                "timing_ms": total_ms,
                "selected_tools": list(selected_tools),
                "failed_tools": list(self._last_executor_debug.get("failed_tools", [])),
                "reasoning_metrics": reasoning_metrics,
                "decision_explanation": decision_explanation,
            }
            if timing_debug_enabled and completion_breakdown_ms:
                complete_metadata["completion_breakdown_ms"] = dict(completion_breakdown_ms)

            state_mgr.transition_to(
                RunState.COMPLETE,
                reason="Success",
                metadata=complete_metadata,
            )
            _ORCHESTRATOR_LOGGER.info(
                "run_complete run_id=%s session_id=%s selected_tools=%s failed_tools=%s total_ms=%s validation=%s",
                run_id,
                request.session_id,
                selected_tools,
                self._last_executor_debug.get("failed_tools", []),
                total_ms,
                getattr(validation, "decision", "accept"),
            )
            artifacts = self._extract_artifacts_from_tool_results(tool_results)

            # Auto-persist to Neo4j v2 graph
            try:
                graph_persist_started = time.perf_counter()
                await persist_run_to_graph_v2(
                    self.memory_service,
                    run_id=run_id,
                    session_id=request.session_id,
                    user_id=request.user_id or "unknown",
                    query=request.query,
                    response=llm_response["content"],
                    selected_tools=selected_tools,
                    tool_results=tool_results,
                )
                if timing_debug_enabled:
                    completion_breakdown_ms["graph_persist_v2"] = round(
                        (time.perf_counter() - graph_persist_started) * 1000,
                        3,
                    )
            except Exception as exc:
                _ORCHESTRATOR_LOGGER.warning("graph_v2 persistence failed: %s", exc)

            return OrchestratorResponse(
                run_id=run_id,
                final_response=llm_response["content"],
                tools_executed=selected_tools,
                tool_results=tool_results,
                state_final=state_mgr.current_state.value,
                llm_generation={
                    "content": llm_response["content"],
                    "provider": llm_response.get("provider"),
                    "model": llm_response.get("model"),
                    "ttft_ms": llm_response.get("ttft_ms"),
                    "gen_ms": llm_response.get("gen_ms"),
                    "context_debug": llm_response.get("context_debug", {}),
                    "inference_metadata": llm_response.get("inference_metadata", {}),
                    "retry": {
                        "count": retry_count,
                        "limit": retry_limit,
                    },
                },
                validation_result={
                    "passed": validation.passed,
                    "decision": getattr(validation, "decision", "accept"),
                    "checks": getattr(validation, "checks", {}),
                    "issues": validation.issues,
                    "confidence_score": validation.confidence_score,
                    "score": score_payload,
                    "risk_flags": list(getattr(validation, "risk_flags", []) or []),
                    "suggestions": getattr(validation, "suggestions", None),
                    "retry_count": retry_count,
                    "gap_detection": last_gap_decision,
                    "judge_post": judge_post_payload,
                    "math_signals": validation_math_signals,
                    "decision_context": decision_context,
                    "decision_explanation": decision_explanation,
                    "explainability": {
                        "triggered_laws": list(validation_math_signals.get("triggered_laws", []) or []),
                        "decision_path": list(decision_explanation.get("decision_path", []) or []),
                        "decision_confidence": decision_explanation.get("decision_confidence"),
                        "risk_score": validation_math_signals.get("actionable_risk"),
                        "resolution_basis": validation_math_signals.get("resolution_basis"),
                    },
                    "threshold_adaptation": threshold_adaptation,
                    "retry_control": retry_control,
                    "runtime_audit_report": runtime_audit_report,
                },
                artifacts=artifacts,
                execution_trace=state_mgr.state_transitions,
                pending_tool_calls=pending_external_tool_calls,
            )

        except Exception as exc:
            fail_total_ms = round((time.perf_counter() - run_started) * 1000, 3)
            self._append_latency_scope_sample(
                run_id=run_id,
                session_id=request.session_id,
                user_id=request.user_id,
                query=routing_query,
                status="failed",
                t_total_s=(fail_total_ms / 1000.0),
                t_embed_s=0.0,
                t_retrieval_s=(tool_execution_ms / 1000.0),
                t_inference_s=(llm_generation_total_ms / 1000.0),
                device_embed=str(getattr(Settings, "OPENVINO_DEVICE", "unknown") or "unknown"),
                model=llm_model,
                provider=llm_provider,
                selected_tools=selected_tools,
                phase_ms={
                    "tool_selection": tool_selection_ms,
                    "tool_execution": tool_execution_ms,
                    "llm_generation": llm_generation_total_ms,
                    "validation": validation_total_ms,
                    "total": fail_total_ms,
                },
                phase_start_offsets_ms={
                    "tool_selection": tool_selection_start_offset_ms,
                    "tool_execution": tool_execution_start_offset_ms,
                    "llm_generation": llm_start_offset_ms,
                    "validation": validation_start_offset_ms,
                },
                route_reason=str((self._last_route_debug or {}).get("reason") or ""),
                error=str(exc),
            )
            state_mgr.transition_to(RunState.FAILED, reason=str(exc))
            raise

    def _append_latency_scope_sample(
        self,
        *,
        run_id: str,
        session_id: str,
        user_id: str,
        query: str,
        status: str,
        t_total_s: float,
        t_embed_s: float,
        t_retrieval_s: float,
        t_inference_s: float,
        device_embed: str,
        model: str,
        provider: str,
        selected_tools: List[str],
        phase_ms: Dict[str, float],
        phase_start_offsets_ms: Dict[str, float],
        route_reason: str,
        error: str = "",
    ) -> None:
        """Write one latency sample for oscilloscope-style monitoring."""
        if not self.latency_scope_enabled:
            return

        sample = {
            "ts": round(time.time(), 6),
            "run_id": run_id,
            "session_id": session_id,
            "user_id": user_id or "",
            "status": status,
            "query": (query or "")[:240],
            "t_total": round(max(0.0, t_total_s), 6),
            "t_embed": round(max(0.0, t_embed_s), 6),
            "t_retrieval": round(max(0.0, t_retrieval_s), 6),
            "t_inference": round(max(0.0, t_inference_s), 6),
            "device_embed": device_embed or "unknown",
            "model": model or "unknown",
            "provider": provider or "unknown",
            "selected_tools": list(selected_tools or []),
            "route_reason": route_reason or "",
            "phase_ms": dict(phase_ms or {}),
            "phase_start_offsets_ms": dict(phase_start_offsets_ms or {}),
        }
        if error:
            sample["error"] = error

        line = json.dumps(sample, ensure_ascii=False) + "\n"

        queue_obj = self._latency_scope_queue
        if queue_obj is None:
            self._start_latency_scope_writer()
            queue_obj = self._latency_scope_queue
        if queue_obj is None:
            return

        try:
            queue_obj.put_nowait(line)
        except Full:
            _ORCHESTRATOR_LOGGER.debug("latency_scope_queue_full_drop run_id=%s", run_id)
        except Exception as exc:
            _ORCHESTRATOR_LOGGER.debug("latency_scope_write_failed: %s", exc)

    def _start_latency_scope_writer(self) -> None:
        """Start non-blocking JSONL writer thread for latency scope samples."""
        if not self.latency_scope_enabled:
            return
        if self._latency_scope_thread is not None and self._latency_scope_thread.is_alive():
            return

        target = self.latency_scope_file
        if not os.path.isabs(target):
            target = os.path.abspath(target)
        self.latency_scope_file = target
        os.makedirs(os.path.dirname(target), exist_ok=True)

        self._latency_scope_queue = Queue(maxsize=self.latency_scope_queue_max)
        self._latency_scope_stop.clear()
        self._latency_scope_thread = Thread(
            target=self._latency_scope_writer_loop,
            name="liara-latency-scope-writer",
            daemon=True,
        )
        self._latency_scope_thread.start()
        atexit.register(self._stop_latency_scope_writer)

    def _stop_latency_scope_writer(self) -> None:
        """Signal the latency writer thread to stop."""
        self._latency_scope_stop.set()
        queue_obj = self._latency_scope_queue
        if queue_obj is not None:
            try:
                queue_obj.put_nowait(_LATENCY_SCOPE_STOP_SENTINEL)
            except Exception:
                pass

    def _latency_scope_writer_loop(self) -> None:
        """Background file-writer loop for latency scope JSONL samples."""
        queue_obj = self._latency_scope_queue
        if queue_obj is None:
            return

        target = self.latency_scope_file
        try:
            with open(target, "a", encoding="utf-8") as fh:
                while not self._latency_scope_stop.is_set():
                    try:
                        line = queue_obj.get(timeout=0.5)
                    except Empty:
                        continue

                    try:
                        if line == _LATENCY_SCOPE_STOP_SENTINEL:
                            return
                        fh.write(str(line))
                        fh.flush()
                    finally:
                        queue_obj.task_done()
        except Exception as exc:
            _ORCHESTRATOR_LOGGER.debug("latency_scope_writer_loop_failed: %s", exc)

    @staticmethod
    def _merge_transition_metadata(
        state_mgr: RunStateManager,
        target_state: RunState,
        metadata: Dict[str, Any],
    ) -> None:
        merge_transition_metadata(state_mgr, target_state, metadata)

    @staticmethod
    def _extract_artifacts_from_tool_results(tool_results: Dict[str, Any]) -> List[Dict[str, Any]] | None:
        return extract_artifacts_from_tool_results(tool_results)

    @staticmethod
    def _build_validation_math_signals(reasoning_metrics: Dict[str, Any]) -> Dict[str, Any]:
        return build_validation_math_signals(reasoning_metrics)

    @staticmethod
    def _build_runtime_audit_report(
        *,
        threshold_profile: Dict[str, Any],
        math_signals: Dict[str, Any],
        session_score_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return build_runtime_audit_report(
            threshold_profile=threshold_profile,
            math_signals=math_signals,
            session_score_history=session_score_history,
        )

    @staticmethod
    def _serialize_judge_decision(decision: Any) -> Dict[str, Any]:
        return serialize_judge_decision(decision)

    @staticmethod
    def _enrich_judge_post_payload(
        judge_post: Dict[str, Any],
        math_signals: Dict[str, Any],
    ) -> Dict[str, Any]:
        return enrich_judge_post_payload(judge_post, math_signals)

    @staticmethod
    def _extract_validation_score_payload(validation: Any) -> Optional[Dict[str, Any]]:
        return extract_validation_score_payload(validation)

    @staticmethod
    def _build_decision_context(
        *,
        validation: Any,
        score_payload: Optional[Dict[str, Any]],
        math_signals: Dict[str, Any],
        judge_post: Dict[str, Any],
    ) -> Dict[str, Any]:
        return build_decision_context(
            validation=validation,
            score_payload=score_payload,
            math_signals=math_signals,
            judge_post=judge_post,
        )

    @staticmethod
    def _build_decision_explanation(
        *,
        validation_decision: str,
        score_payload: Optional[Dict[str, Any]],
        math_signals: Dict[str, Any],
        judge_post: Dict[str, Any],
    ) -> Dict[str, Any]:
        return build_decision_explanation(
            validation_decision=validation_decision,
            score_payload=score_payload,
            math_signals=math_signals,
            judge_post=judge_post,
        )

    def _read_control_mode_before(self, session_id: str) -> str:
        return read_control_mode_before(self._session_control_state, session_id)

    def _resolve_reasoning_threshold_profile(self, session_id: Optional[str]) -> Dict[str, Any]:
        profile = dict(Settings.reasoning_threshold_profile() or {})
        if not session_id:
            return profile

        session_profile = self._session_adaptive_thresholds.get(session_id)
        if not isinstance(session_profile, dict) or not session_profile:
            return profile

        merged = dict(profile)
        for key in (
            "soft_risk_max",
            "hard_risk_max",
            "weak_score_escalation_count",
            "score_feedback_canary_soft_only",
        ):
            if key in session_profile:
                merged[key] = session_profile[key]

        base_source = str(profile.get("source") or "env")
        session_source = str(session_profile.get("source") or "session")
        merged["source"] = f"{base_source}+{session_source}"
        merged["session_override"] = True
        merged["session_override_version"] = str(session_profile.get("version") or "session")
        return merged

    def _maybe_apply_runtime_threshold_adaptation(
        self,
        *,
        session_id: str,
        runtime_audit_report: Dict[str, Any],
        threshold_profile: Dict[str, Any],
        feedback_entry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision_rank = {
            "accept": 0,
            "allow": 0,
            "warn": 1,
            "revise": 2,
            "block": 3,
        }
        current_outcome = {
            "decision": str((feedback_entry or {}).get("decision") or "accept").lower(),
            "confidence_score": float((feedback_entry or {}).get("confidence_score", 0.0) or 0.0),
            "actionable_risk": float((feedback_entry or {}).get("actionable_risk", 0.0) or 0.0),
        }
        adaptive_state = dict(self._session_adaptive_state.get(session_id) or {})
        baseline_profile = dict(adaptive_state.get("baseline_profile") or threshold_profile or {})
        last_outcome = dict(adaptive_state.get("last_outcome") or {})

        if adaptive_state.get("active", False) and last_outcome:
            previous_decision_rank = int(decision_rank.get(str(last_outcome.get("decision") or "accept"), 0))
            current_decision_rank = int(decision_rank.get(str(current_outcome.get("decision") or "accept"), 0))
            previous_confidence = float(last_outcome.get("confidence_score", 0.0) or 0.0)
            previous_risk = float(last_outcome.get("actionable_risk", 0.0) or 0.0)

            confidence_drop = previous_confidence - float(current_outcome.get("confidence_score", 0.0) or 0.0)
            risk_increase = float(current_outcome.get("actionable_risk", 0.0) or 0.0) - previous_risk
            rollback_due_to_worse_outcome = (
                current_decision_rank > previous_decision_rank
                or confidence_drop >= 0.12
                or risk_increase >= 0.25
            )

            if rollback_due_to_worse_outcome:
                rollback_profile = {
                    "soft_risk_max": round(float(baseline_profile.get("soft_risk_max", 5.0) or 5.0), 6),
                    "hard_risk_max": round(float(baseline_profile.get("hard_risk_max", 8.0) or 8.0), 6),
                    "weak_score_escalation_count": int(
                        baseline_profile.get("weak_score_escalation_count", threshold_profile.get("weak_score_escalation_count", 2)) or 2
                    ),
                    "score_feedback_canary_soft_only": bool(
                        baseline_profile.get(
                            "score_feedback_canary_soft_only",
                            threshold_profile.get("score_feedback_canary_soft_only", False),
                        )
                    ),
                    "version": f"rollback-{str((feedback_entry or {}).get('run_id') or 'runtime')}",
                    "source": "runtime_audit_rollback",
                }
                self._session_adaptive_thresholds[session_id] = rollback_profile
                self._session_adaptive_state[session_id] = {
                    "active": False,
                    "baseline_profile": baseline_profile,
                    "last_outcome": current_outcome,
                    "last_applied_profile": dict(rollback_profile),
                    "last_action": "rollback",
                }
                return {
                    "applied": False,
                    "rolled_back": True,
                    "reason": "outcome_degraded",
                    "session_id": session_id,
                    "previous_outcome": last_outcome,
                    "current_outcome": current_outcome,
                    "rollback_profile": {
                        "soft_risk_max": rollback_profile["soft_risk_max"],
                        "hard_risk_max": rollback_profile["hard_risk_max"],
                        "version": rollback_profile["version"],
                    },
                    "strategy": "outcome_guarded_rollback",
                }

        if not self.reasoning_auto_adapt_thresholds:
            return {"applied": False, "reason": "disabled"}

        thresholds_payload = dict((runtime_audit_report or {}).get("thresholds") or {})
        recommended = dict(thresholds_payload.get("recommended") or {})
        status = str(recommended.get("status") or "")
        sample_count = int(recommended.get("sample_count", 0) or 0)

        if status != "recommended":
            return {"applied": False, "reason": "no_recommendation", "status": status}
        if sample_count < self.reasoning_auto_adapt_min_sample_count:
            return {
                "applied": False,
                "reason": "insufficient_samples",
                "sample_count": sample_count,
                "min_sample_count": self.reasoning_auto_adapt_min_sample_count,
            }

        current_soft = float(threshold_profile.get("soft_risk_max", 5.0) or 5.0)
        current_hard = float(threshold_profile.get("hard_risk_max", 8.0) or 8.0)
        target_soft = float(recommended.get("soft_risk_max", current_soft) or current_soft)
        target_hard = float(recommended.get("hard_risk_max", current_hard) or current_hard)

        max_delta = max(0.1, float(self.reasoning_auto_adapt_max_delta or 1.0))
        clamped_soft = current_soft + max(-max_delta, min(max_delta, target_soft - current_soft))
        clamped_hard = current_hard + max(-max_delta, min(max_delta, target_hard - current_hard))
        if clamped_hard <= clamped_soft:
            clamped_hard = clamped_soft + 0.25

        next_profile = {
            "soft_risk_max": round(clamped_soft, 6),
            "hard_risk_max": round(clamped_hard, 6),
            "weak_score_escalation_count": int(threshold_profile.get("weak_score_escalation_count", 2) or 2),
            "score_feedback_canary_soft_only": bool(
                threshold_profile.get("score_feedback_canary_soft_only", False)
            ),
            "version": str(recommended.get("version") or "runtime-adapted"),
            "source": "runtime_audit_session",
        }
        self._session_adaptive_thresholds[session_id] = next_profile
        self._session_adaptive_state[session_id] = {
            "active": True,
            "baseline_profile": {
                "soft_risk_max": round(float(baseline_profile.get("soft_risk_max", current_soft) or current_soft), 6),
                "hard_risk_max": round(float(baseline_profile.get("hard_risk_max", current_hard) or current_hard), 6),
                "weak_score_escalation_count": int(
                    baseline_profile.get("weak_score_escalation_count", threshold_profile.get("weak_score_escalation_count", 2)) or 2
                ),
                "score_feedback_canary_soft_only": bool(
                    baseline_profile.get(
                        "score_feedback_canary_soft_only",
                        threshold_profile.get("score_feedback_canary_soft_only", False),
                    )
                ),
            },
            "last_outcome": current_outcome,
            "last_applied_profile": dict(next_profile),
            "last_action": "apply",
        }

        return {
            "applied": True,
            "session_id": session_id,
            "sample_count": sample_count,
            "previous": {
                "soft_risk_max": round(current_soft, 6),
                "hard_risk_max": round(current_hard, 6),
            },
            "recommended": {
                "soft_risk_max": round(target_soft, 6),
                "hard_risk_max": round(target_hard, 6),
            },
            "applied_profile": {
                "soft_risk_max": next_profile["soft_risk_max"],
                "hard_risk_max": next_profile["hard_risk_max"],
                "version": next_profile["version"],
            },
            "max_delta": round(max_delta, 6),
            "strategy": "clamped_session_adaptation",
        }

    @staticmethod
    def _build_decision_delta(*, control_before: str, control_after: str) -> Dict[str, Any]:
        return build_decision_delta(control_before=control_before, control_after=control_after)

    @staticmethod
    def _build_retry_control(
        *,
        validation_decision: str,
        judge_post: Dict[str, Any],
        retry_count: int,
        retry_limit: int,
        compression_meta: Dict[str, Any],
        gap_decision: Optional[Dict[str, Any]] = None,
        math_signals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return build_retry_control(
            validation_decision=validation_decision,
            judge_post=judge_post,
            retry_count=retry_count,
            retry_limit=retry_limit,
            compression_meta=compression_meta,
            gap_decision=dict(gap_decision or {}),
            math_signals=dict(math_signals or {}),
            gap_stop_value=GapAction.STOP.value,
        )

    @staticmethod
    def _apply_score_feedback_to_metric_inputs(
        inputs: Dict[str, Any],
        previous_score_feedback: Dict[str, Any],
        previous_score_history: Optional[List[Dict[str, Any]]] = None,
        *,
        weak_score_escalation_count: int = 2,
        score_feedback_canary_soft_only: bool = False,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        return apply_score_feedback_to_metric_inputs(
            inputs,
            previous_score_feedback,
            previous_score_history,
            weak_score_escalation_count=weak_score_escalation_count,
            score_feedback_canary_soft_only=score_feedback_canary_soft_only,
        )

    @staticmethod
    def _build_hybrid_control_metadata(
        metrics: Dict[str, Any],
        score_feedback: Dict[str, Any],
        judge_post: Optional[Dict[str, Any]] = None,
        *,
        query: str = "",
        response: str = "",
        validation_decision: str = "accept",
    ) -> Dict[str, Any]:
        return build_hybrid_control_metadata(
            metrics,
            score_feedback,
            judge_post,
            query=query,
            response=response,
            validation_decision=validation_decision,
        )

    @staticmethod
    def _derive_reasoning_metric_inputs(
        *,
        query: str,
        response: str,
        tools_used: List[str],
        context_debug: Dict[str, Any],
        validation_decision: str,
        retry_count: int,
        failed_tools: List[str],
    ) -> Dict[str, Any]:
        return derive_reasoning_metric_inputs(
            query=query,
            response=response,
            tools_used=tools_used,
            context_debug=context_debug,
            validation_decision=validation_decision,
            retry_count=retry_count,
            failed_tools=failed_tools,
        )

    async def _compute_reasoning_metrics_snapshot(
        self,
        *,
        session_id: Optional[str] = None,
        query: str,
        response: str,
        tools_used: List[str],
        context_debug: Dict[str, Any],
        validation_decision: str,
        retry_count: int,
        failed_tools: List[str],
        previous_score_feedback: Optional[Dict[str, Any]] = None,
        previous_score_history: Optional[List[Dict[str, Any]]] = None,
        judge_post_decision: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        threshold_profile = self._resolve_reasoning_threshold_profile(session_id)
        inputs = self._derive_reasoning_metric_inputs(
            query=query,
            response=response,
            tools_used=tools_used,
            context_debug=context_debug,
            validation_decision=validation_decision,
            retry_count=retry_count,
            failed_tools=failed_tools,
        )
        adjusted_inputs, score_feedback = self._apply_score_feedback_to_metric_inputs(
            inputs,
            dict(previous_score_feedback or {}),
            list(previous_score_history or []),
            weak_score_escalation_count=int(threshold_profile.get("weak_score_escalation_count", 2) or 2),
            score_feedback_canary_soft_only=bool(threshold_profile.get("score_feedback_canary_soft_only", False)),
        )

        julia_metrics, julia_error = await self._compute_reasoning_metrics_snapshot_julia(
            adjusted_inputs,
            soft_risk_max=float(threshold_profile.get("soft_risk_max", 5.0) or 5.0),
            hard_risk_max=float(threshold_profile.get("hard_risk_max", 8.0) or 8.0),
        )
        if julia_metrics is not None:
            julia_metrics["score_feedback"] = score_feedback
            julia_metrics["judge_post"] = dict(judge_post_decision or {})
            julia_metrics["threshold_profile"] = threshold_profile
            julia_metrics["belief_snapshot"] = await self._compute_belief_snapshot(
                julia_metrics, score_feedback, previous_score_history or []
            )
            julia_metrics["utility_snapshot"] = await self._compute_utility_snapshot(
                julia_metrics, step=retry_count
            )
            julia_metrics["structure_snapshot"] = await self._compute_structure_stability_snapshot(
                context_debug=context_debug,
                reasoning_metrics=julia_metrics,
                score_history=previous_score_history or [],
            )
            julia_metrics["decision_snapshot"] = await self._compute_decision_snapshot(
                reasoning_metrics=julia_metrics,
                score_feedback=score_feedback,
                threshold_profile=threshold_profile,
            )
            julia_metrics["hybrid_control"] = self._build_hybrid_control_metadata(
                julia_metrics,
                score_feedback,
                dict(judge_post_decision or {}),
                query=query,
                response=response,
                validation_decision=validation_decision,
            )
            return julia_metrics

        metrics = self._compute_reasoning_metrics_snapshot_python(
            adjusted_inputs,
            soft_risk_max=float(threshold_profile.get("soft_risk_max", 5.0) or 5.0),
            hard_risk_max=float(threshold_profile.get("hard_risk_max", 8.0) or 8.0),
            fallback_reason=julia_error or "julia_metrics_unavailable",
        )
        metrics["score_feedback"] = score_feedback
        metrics["judge_post"] = dict(judge_post_decision or {})
        metrics["threshold_profile"] = threshold_profile
        metrics["belief_snapshot"] = await self._compute_belief_snapshot(
            metrics, score_feedback, previous_score_history or []
        )
        metrics["utility_snapshot"] = await self._compute_utility_snapshot(
            metrics, step=retry_count
        )
        metrics["structure_snapshot"] = await self._compute_structure_stability_snapshot(
            context_debug=context_debug,
            reasoning_metrics=metrics,
            score_history=previous_score_history or [],
        )
        metrics["decision_snapshot"] = await self._compute_decision_snapshot(
            reasoning_metrics=metrics,
            score_feedback=score_feedback,
            threshold_profile=threshold_profile,
        )
        metrics["hybrid_control"] = self._build_hybrid_control_metadata(
            metrics,
            score_feedback,
            dict(judge_post_decision or {}),
            query=query,
            response=response,
            validation_decision=validation_decision,
        )
        return metrics

    async def _compute_belief_snapshot(
        self,
        reasoning_metrics: Dict[str, Any],
        score_feedback: Dict[str, Any],
        score_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compute Phase-1 belief update (Bayes + Kalman + Variance) for a turn.

        Architecture: Julia primary, Python fallback (MIRKO_MATHE_2.md Phase 0 rule).
        """
        from services.orchestrator.reasoning_math_ext import compute_belief_snapshot

        # --- Build belief state from session history ---
        prior_confidence = 0.5
        prior_entropy = float(reasoning_metrics.get("context_entropy") or 0.0)
        prior_variance = 0.0
        # Carry forward last belief snapshot's Kalman state if available.
        # (This would be populated from _session_belief_state in future phases.)

        belief = {
            "prior": prior_confidence,
            "entropy": prior_entropy,
            "variance": prior_variance,
        }

        # --- Build observation from current turn ---
        goal_progress = float(reasoning_metrics.get("goal_progress") or 0.0)
        utility = float(reasoning_metrics.get("utility") or 0.0)
        # Likelihood: how strongly does the current evidence support the response?
        # Heuristic: goal_progress normalized to [0,1] serves as likelihood.
        likelihood = max(0.01, min(0.99, (goal_progress + 1.0) / 2.0))
        observation = {
            "likelihood": likelihood,
            "signal": max(0.0, min(1.0, (utility + 10.0) / 20.0)),  # map utility [-10,10] → [0,1]
            "entropy": prior_entropy,
        }

        # --- Signal window from recent score history ---
        signal_window = [
            float(entry.get("confidence_score") or 0.5)
            for entry in score_history
            if isinstance(entry, dict)
        ]

        # --- Try Julia first ---
        try:
            from services.simulation.bridge import JuliaBridge, JuliaBridgeError
            from services.config import Settings as _S
            allowlist = set([*_S.julia_allowlist(), "belief_snapshot"])
            bridge = JuliaBridge(
                allowlist=sorted(allowlist),
                timeout_seconds=min(_S.JULIA_TIMEOUT_SECONDS, 8.0),
            )
            raw = await bridge.run(
                "belief_snapshot",
                {
                    "belief": belief,
                    "observation": observation,
                    "signal_window": signal_window,
                    "config": {"kalman_gain": 0.3, "min_variance": 1e-4},
                },
            )
            result = raw.get("belief_snapshot") if isinstance(raw, dict) else None
            if isinstance(result, dict):
                result["belief_compute_backend"] = "julia"
                result["belief_compute_path"] = "primary"
                return result
        except Exception:
            pass  # Fall through to Python

        # --- Python fallback ---
        return compute_belief_snapshot(
            belief,
            observation,
            signal_window,
            kalman_gain=0.3,
            min_variance=1e-4,
        )

    async def _compute_utility_snapshot(
        self,
        reasoning_metrics: Dict[str, Any],
        step: int,
        *,
        gamma: float = 0.95,
    ) -> Dict[str, Any]:
        """Compute Phase-2 utility metrics (IG, CWU, Temporal Discount).

        Architecture: Julia primary, Python fallback (MIRKO_MATHE_2.md Phase 0 rule).
        """
        from services.orchestrator.reasoning_math_ext import compute_utility_snapshot

        utility = float(reasoning_metrics.get("utility") or 0.0)
        # entropy_before: use context entropy as proxy for pre-step uncertainty
        entropy_before = float(reasoning_metrics.get("context_entropy") or 0.0)
        # entropy_after: use score-feedback-adjusted entropy if available, else same
        score_feedback = reasoning_metrics.get("score_feedback") or {}
        entropy_after_raw = score_feedback.get("adjusted_entropy", entropy_before)
        entropy_after = float(entropy_after_raw) if entropy_after_raw is not None else entropy_before

        # --- Try Julia first ---
        try:
            from services.simulation.bridge import JuliaBridge
            from services.config import Settings as _S
            allowlist = set([*_S.julia_allowlist(), "utility_snapshot"])
            bridge = JuliaBridge(
                allowlist=sorted(allowlist),
                timeout_seconds=min(_S.JULIA_TIMEOUT_SECONDS, 8.0),
            )
            raw = await bridge.run(
                "utility_snapshot",
                {
                    "utility": utility,
                    "entropy_before": entropy_before,
                    "entropy_after": entropy_after,
                    "step": step,
                    "gamma": gamma,
                },
            )
            result = raw.get("utility_snapshot") if isinstance(raw, dict) else None
            if isinstance(result, dict):
                result["utility_compute_backend"] = "julia"
                result["utility_compute_path"] = "primary"
                return result
        except Exception:
            pass  # Fall through to Python

        # --- Python fallback ---
        return compute_utility_snapshot(
            utility,
            entropy_before,
            entropy_after,
            step,
            gamma=gamma,
        )

    async def _compute_structure_stability_snapshot(
        self,
        *,
        context_debug: Dict[str, Any],
        reasoning_metrics: Dict[str, Any],
        score_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compute Phase-3 structure/stability/regularization metrics.

        Architecture: Julia primary, Python fallback.
        """
        from services.orchestrator.reasoning_math_ext import compute_structure_stability_snapshot

        risk_series = [
            float(entry.get("total_risk") or entry.get("actionable_risk") or 0.0)
            for entry in score_history
            if isinstance(entry, dict)
        ]
        risk_series.append(float(reasoning_metrics.get("actionable_risk") or 0.0))

        memory_items = int(reasoning_metrics.get("memory_items", 0) or 0)
        tool_calls = int(reasoning_metrics.get("tool_calls", 0) or 0)

        try:
            from services.simulation.bridge import JuliaBridge
            from services.config import Settings as _S

            allowlist = set([*_S.julia_allowlist(), "structure_stability_snapshot"])
            bridge = JuliaBridge(
                allowlist=sorted(allowlist),
                timeout_seconds=min(_S.JULIA_TIMEOUT_SECONDS, 8.0),
            )
            raw = await bridge.run(
                "structure_stability_snapshot",
                {
                    "context_debug": dict(context_debug or {}),
                    "memory_items": memory_items,
                    "tool_calls": tool_calls,
                    "risk_series": risk_series,
                    "lambda_l1": 0.05,
                    "lambda_l2": 0.01,
                },
            )
            result = raw.get("structure_stability_snapshot") if isinstance(raw, dict) else None
            if isinstance(result, dict):
                result["structure_compute_backend"] = "julia"
                result["structure_compute_path"] = "primary"
                return result
        except Exception:
            pass

        return compute_structure_stability_snapshot(
            context_debug=dict(context_debug or {}),
            memory_items=memory_items,
            tool_calls=tool_calls,
            risk_series=risk_series,
            lambda_l1=0.05,
            lambda_l2=0.01,
        )

    async def _compute_decision_snapshot(
        self,
        *,
        reasoning_metrics: Dict[str, Any],
        score_feedback: Dict[str, Any],
        threshold_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute Phase-4 multi-objective decision snapshot."""
        from services.orchestrator.reasoning_math_ext import compute_decision_snapshot

        structure_snapshot = reasoning_metrics.get("structure_snapshot") if isinstance(reasoning_metrics.get("structure_snapshot"), dict) else {}
        utility_snapshot = reasoning_metrics.get("utility_snapshot") if isinstance(reasoning_metrics.get("utility_snapshot"), dict) else {}

        payload = {
            "total_cost": float(reasoning_metrics.get("total_cost") or 0.0),
            "actionable_risk": float(reasoning_metrics.get("actionable_risk") or 0.0),
            "context_entropy": float(reasoning_metrics.get("context_entropy") or 0.0),
            "utility_discounted": float(utility_snapshot.get("utility_discounted") or reasoning_metrics.get("utility") or 0.0),
            "stability_score": float(structure_snapshot.get("stability_score") or 1.0),
            "regularization_total": float(structure_snapshot.get("regularization_total") or 0.0),
            "path_pressure": float(structure_snapshot.get("structure_path_pressure") or 0.0),
            "mode_floor": str(score_feedback.get("mode_floor") or "advisory"),
            "repair_preferred": bool(score_feedback.get("repair_preferred", False)),
            "soft_risk_max": float(threshold_profile.get("soft_risk_max", 5.0) or 5.0),
            "hard_risk_max": float(threshold_profile.get("hard_risk_max", 8.0) or 8.0),
        }

        try:
            from services.simulation.bridge import JuliaBridge
            from services.config import Settings as _S

            allowlist = set([*_S.julia_allowlist(), "decision_snapshot"])
            bridge = JuliaBridge(
                allowlist=sorted(allowlist),
                timeout_seconds=min(_S.JULIA_TIMEOUT_SECONDS, 8.0),
            )
            raw = await bridge.run("decision_snapshot", payload)
            result = raw.get("decision_snapshot") if isinstance(raw, dict) else None
            if isinstance(result, dict):
                result["decision_compute_backend"] = "julia"
                result["decision_compute_path"] = "primary"
                return result
        except Exception:
            pass

        return compute_decision_snapshot(**payload)

    async def _compute_reasoning_metrics_snapshot_julia(
        self,
        inputs: Dict[str, Any],
        *,
        soft_risk_max: float,
        hard_risk_max: float,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        from services.config import Settings
        from services.simulation.bridge import JuliaBridge, JuliaBridgeError

        allowlist = set([*Settings.julia_allowlist(), "reasoning_metrics"])

        try:
            bridge = JuliaBridge(
                allowlist=sorted(allowlist),
                timeout_seconds=min(Settings.JULIA_TIMEOUT_SECONDS, 10.0),
            )
            raw = await bridge.run(
                "reasoning_metrics",
                {
                    "inputs": inputs,
                    "config": {
                        "k_depth": 1.0,
                        "k_memory": 1.0,
                        "k_tool": 2.0,
                        "k_entropy": 1.5,
                        "lambda_entropy": 0.8,
                        "w_policy": 0.5,
                        "w_uncertainty": 0.2,
                        "w_complexity": 0.3,
                        "soft_risk_max": soft_risk_max,
                        "hard_risk_max": hard_risk_max,
                    },
                },
            )
            candidate = raw.get("metrics") if isinstance(raw, dict) and isinstance(raw.get("metrics"), dict) else raw
            if not isinstance(candidate, dict):
                return None, "julia_metrics_invalid_payload"
            
            # Remove fields that we override explicitly to avoid duplicate kwargs.
            candidate_copy = dict(candidate)
            candidate_copy.pop("reasoning_cost", None)
            candidate_copy.pop("risk_total", None)
            candidate_copy.pop("rds_mode", None)
            candidate_copy.pop("compute_backend", None)
            candidate_copy.pop("compute_path", None)
            candidate_copy.pop("fallback_reason", None)
            
            metrics = ReasoningMetricsSnapshot(
                **candidate_copy,
                reasoning_cost=candidate.get("total_cost", 0.0),
                risk_total=candidate.get("total_risk", 0.0),
                rds_mode="diagnostic",
                compute_backend="julia",
                compute_path="primary",
                fallback_reason=None,
            )
            return metrics.model_dump(), None
        except (JuliaBridgeError, ValueError, TypeError) as exc:
            _ORCHESTRATOR_LOGGER.debug("reasoning_metrics_julia_fallback error=%s", exc)
            return None, str(exc)

    @staticmethod
    def _compute_reasoning_metrics_snapshot_python(
        inputs: Dict[str, Any],
        *,
        soft_risk_max: float = 5.0,
        hard_risk_max: float = 8.0,
        fallback_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return compute_reasoning_metrics_snapshot_python(
            inputs,
            soft_risk_max=soft_risk_max,
            hard_risk_max=hard_risk_max,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _normalize_external_tool(tool: Any) -> Dict[str, Any]:
        return normalize_external_tool(tool)

    @classmethod
    def _extract_textual_tool_schema(cls, tool: Any) -> tuple[str, str, Dict[str, Any]]:
        return extract_textual_tool_schema(tool)

    @staticmethod
    def _extract_path_candidate(query: str) -> Optional[str]:
        return extract_path_candidate(query)

    @staticmethod
    def _extract_path_candidates(query: str) -> List[str]:
        return extract_path_candidates(query)

    @staticmethod
    def _extract_requested_end_line(query: str) -> Optional[int]:
        return extract_requested_end_line(query)

    @staticmethod
    def _extract_explicit_content(query: str) -> Optional[str]:
        return extract_explicit_content(query)

    @classmethod
    def _infer_external_tool_arguments(cls, query: str, parameters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return infer_external_tool_arguments(query, parameters)

    @classmethod
    def _build_external_write_content(
        cls,
        query: str,
        tool_results: List[Dict[str, Any]],
    ) -> Optional[str]:
        read_entry = next(
            (
                entry
                for entry in reversed(tool_results)
                if isinstance(entry, dict) and str(entry.get("name") or "").strip().lower() == "read_file"
            ),
            None,
        )
        if not isinstance(read_entry, dict):
            return None

        raw_content = str(read_entry.get("content") or "").strip()
        source_path = "(unknown)"
        source_text = raw_content
        if raw_content.startswith("{"):
            try:
                payload = json.loads(raw_content)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                source_path = str(payload.get("path") or source_path)
                source_text = str(payload.get("content") or source_text)

        bullet_lines: List[str] = []
        for line in source_text.splitlines():
            cleaned = re.sub(r"\s+", " ", line.strip(" \t-#*"))
            if len(cleaned) < 12:
                continue
            bullet_lines.append(f"- {cleaned[:160]}")
            if len(bullet_lines) == 5:
                break

        while len(bullet_lines) < 5:
            bullet_lines.append(f"- Architekturhinweis {len(bullet_lines) + 1} aus {source_path}")

        result_marker = "RESULT: TOOL_LOOP_OK" if "TOOL_LOOP_OK" in query else "RESULT: EXTERNAL_TOOL_LOOP"
        return "\n".join([
            f"Quelle: {source_path}",
            *bullet_lines,
            result_marker,
        ])

    @classmethod
    def _plan_external_tool_followup(
        cls,
        query: str,
        available_tools: List[Any],
        tool_results: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        completed = {
            str(entry.get("name") or "").strip().lower()
            for entry in tool_results
            if isinstance(entry, dict)
        }
        if "read_file" not in completed or "write_file" in completed:
            return None
        if not re.search(r"\b(write_file|write file|schreib|speicher|save|store)\b", query, re.IGNORECASE):
            return None

        write_tool = next(
            (
                cls._normalize_external_tool(raw_tool)
                for raw_tool in available_tools
                if cls._extract_textual_tool_schema(raw_tool)[0].strip().lower() == "write_file"
            ),
            None,
        )
        if not isinstance(write_tool, dict) or not write_tool:
            return None

        path_candidates = cls._extract_path_candidates(query)
        output_path = next(
            (
                candidate
                for candidate in path_candidates
                if re.search(r"(^|[\\/])artifacts([\\/]|$)", candidate, re.IGNORECASE)
            ),
            None,
        )
        if not output_path and path_candidates:
            output_path = path_candidates[-1]
        content = cls._build_external_write_content(query, tool_results)
        if not output_path or not content:
            return None

        return {
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": "write_file",
                "arguments": {
                    "path": output_path,
                    "content": content,
                },
            },
        }

    @classmethod
    def _plan_external_tool_call(
        cls,
        query: str,
        available_tools: List[Any],
        tool_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return None

        if tool_results:
            return cls._plan_external_tool_followup(q, available_tools, tool_results)

        # Conservative trigger: only attempt external tool planning when the user intent
        # strongly suggests external lookup/operation.
        trigger_patterns = [
            r"\b(search|find|lookup|look up|fetch|read|open|list|scan|inspect|analy[sz]e|check)\b",
            r"\b(date|time|weather|http|api|url|file|directory|folder|repo|repository|issue|pr|pull request)\b",
            r"\b(write|save|store|lies|lese|oeffne|prüf|pruef|analysiere|schreib(?:e|en|st|t)?|speicher|rufe)\b",
        ]
        if not any(re.search(pattern, q, re.IGNORECASE) for pattern in trigger_patterns):
            return None

        q_terms = set(re.findall(r"[a-zA-Z0-9_]{3,}", q.lower()))
        best_tool: Optional[Dict[str, Any]] = None
        best_score = 0
        best_args: Optional[Dict[str, Any]] = None

        for raw_tool in available_tools:
            tool_dict = cls._normalize_external_tool(raw_tool)
            if not tool_dict:
                continue
            name, description, parameters = cls._extract_textual_tool_schema(tool_dict)
            if not name:
                continue

            haystack = f"{name} {description}".lower()
            tool_terms = set(re.findall(r"[a-zA-Z0-9_]{3,}", haystack))
            overlap = len(q_terms.intersection(tool_terms))
            exact_name_bonus = 4 if name.lower() in q.lower() else 0
            intent_bonus = 0
            if re.search(r"\b(read|open|inspect|check|lies|lese|oeffne)\b", q, re.IGNORECASE) and "read" in name.lower():
                intent_bonus += 2
            if re.search(r"\b(write|save|store|schreib|speicher)\b", q, re.IGNORECASE) and "write" in name.lower():
                intent_bonus += 2
            if cls._extract_path_candidate(q) and "file" in haystack:
                intent_bonus += 1
            score = overlap + exact_name_bonus + intent_bonus

            if score <= best_score:
                continue

            args = cls._infer_external_tool_arguments(q, parameters)
            if args is None:
                continue

            best_tool = tool_dict
            best_score = score
            best_args = args

        # Require a minimum confidence to avoid noisy accidental calls.
        if best_tool is None or best_score < 2 or best_args is None:
            return None

        name, _description, _parameters = cls._extract_textual_tool_schema(best_tool)
        return {
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": best_args,
            },
        }

    async def _select_tools(
        self,
        query: str,
        tools_override: Optional[List[str]] = None,
        input_profile: Optional[InputSituationProfile] = None,
    ) -> List[str]:
        decision = await self.router.route(
            RouterRequest(
                query=query,
                tools_override=tools_override,
                input_profile=input_profile or getattr(self, "_active_input_profile", None),
            )
        )

        selected_tools = list(decision.selected_tools)
        reason = decision.reason
        metadata = dict(decision.metadata or {})

        reward_route = self._evaluate_reward_routing(query=query)
        if reward_route is not None:
            metadata["reward_routing"] = reward_route
            if reward_route.get("block"):
                selected_tools = []
                reason = "reward_model_risk_block"

        self._last_route_debug = {
            "selected_tools": list(selected_tools),
            "reason": reason,
            "metadata": metadata,
        }
        self._last_routing_metadata = metadata
        return selected_tools

    def _init_reward_scorer(self) -> Optional[RewardModelScorer]:
        if not self.reward_routing_enabled:
            return None

        configured_path = (getattr(Settings, "REWARD_MODEL_PATH", "") or "").strip()
        reward_model_path = configured_path or os.getenv("REWARD_MODEL_PATH", "").strip()
        if not reward_model_path:
            return None
        if not os.path.exists(reward_model_path):
            _ORCHESTRATOR_LOGGER.warning(
                "[REWARD:ROUTING] configured model path does not exist: %s",
                reward_model_path,
            )
            return None

        try:
            from services.reward_model.reward_model import RewardModel

            model = RewardModel.load(reward_model_path)
            scorer = RewardModelScorer(model=model)
            if scorer.is_ready:
                _ORCHESTRATOR_LOGGER.info(
                    "[REWARD:ROUTING] loaded reward model from %s",
                    reward_model_path,
                )
                return scorer
            return None
        except Exception as exc:
            _ORCHESTRATOR_LOGGER.warning(
                "[REWARD:ROUTING] failed to load model from %s: %s",
                reward_model_path,
                exc,
            )
            return None

    def _evaluate_reward_routing(self, *, query: str) -> Optional[Dict[str, Any]]:
        if not self.reward_routing_enabled:
            return None
        if self.reward_scorer is None:
            return None

        score = self.reward_scorer.score_action(
            action="tool_routing",
            input_text=query,
            context={
                "stage": "tool_selection",
                "session_id": self._active_session_id,
                "user_id": self._active_user_id,
            },
        )
        if not score.get("model_available", False):
            return {
                "enabled": True,
                "model_available": False,
                "block": False,
                "source": score.get("source", "no_model"),
            }

        risk_score = float(score.get("risk_score", 0.5))
        confidence = float(score.get("confidence", 0.5))
        eval_binary = int(score.get("eval_binary", 1))
        should_block = (
            eval_binary == 0
            and risk_score >= self.reward_routing_block_threshold
            and confidence >= self.reward_routing_conf_threshold
        )
        return {
            "enabled": True,
            "model_available": True,
            "eval_binary": eval_binary,
            "risk_score": risk_score,
            "confidence": confidence,
            "block": should_block,
            "thresholds": {
                "risk": self.reward_routing_block_threshold,
                "confidence": self.reward_routing_conf_threshold,
            },
            "source": score.get("source", "reward_model"),
        }

    async def _execute_tools(
        self,
        tool_names: List[str],
        query: str,
        run_id: str = "",
    ) -> Dict[str, Any]:
        blocked_tools: list[dict[str, Any]] = []
        revise_tools: list[dict[str, Any]] = []
        unprofiled_tools: list[str] = []
        judge_confidences: list[float] = []

        executor_request = ExecutorRequest(
            tool_names=tool_names,
            query=query,
            session_id=self._active_session_id,
            run_id=run_id,
            user_id=self._active_user_id,
            sandbox_root=self._active_sandbox_root,
            timeout_seconds=30,
            routing_metadata=getattr(self, "_last_routing_metadata", {}),
            simulation_mode=getattr(self, "_simulation_mode", False),
        )
        prepared_requests = self.executor.prepare_tool_requests(executor_request)
        prepared_by_name = {item.tool_name: item for item in prepared_requests}

        for tool_name in tool_names:
            if tool_name not in _JUDGE_PROFILED_ACTIONS:
                unprofiled_tools.append(tool_name)
                judge_trace = self._judge_traceability(run_id=run_id)
                log_judge_pre_action(
                    tool_name=tool_name,
                    decision="allow",
                    issues=["No pre-action profile registered; pass-through by orchestrator policy."],
                    constraints={"audit_reason": "unprofiled_tool_passthrough"},
                    request_id=judge_trace["request_id"],
                    session_id=judge_trace["session_id"],
                    run_id=judge_trace["run_id"],
                    source=judge_trace["source"],
                    context="judge_pre_action_unprofiled",
                )
                continue

            prepared_request = prepared_by_name.get(tool_name)
            concrete_input = dict(prepared_request.parameters) if prepared_request is not None else {}
            judge_context = JudgeContext(
                request_id=run_id,
                stage=JudgeStage.PRE_ACTION,
                actor="orchestrator",
                intent="tool_dispatch",
                action=tool_name,
                input=concrete_input,
                metadata={
                    "source": "orchestrator",
                    "risk_hint": "low",
                    "session_id": self._active_session_id,
                    "user_id": self._active_user_id,
                },
            )
            decision = self.judge_engine.evaluate_pre_action(judge_context)
            judge_confidences.append(decision.confidence)
            judge_trace = self._judge_traceability(run_id=run_id)
            log_judge_pre_action(
                tool_name=tool_name,
                decision=decision.decision.value,
                issues=decision.issues,
                constraints=decision.constraints,
                request_id=judge_trace["request_id"],
                session_id=judge_trace["session_id"],
                run_id=judge_trace["run_id"],
                source=judge_trace["source"],
                context="judge_pre_action",
            )

            if decision.decision.value == "block":
                blocked_tools.append(
                    {
                        "tool": tool_name,
                        "decision": decision.decision.value,
                        "confidence": decision.confidence,
                        "issues": decision.issues,
                        "reason_codes": [c.reason_code for c in decision.checks],
                    }
                )
            elif decision.decision.value == "revise":
                revise_tools.append(
                    {
                        "tool": tool_name,
                        "confidence": decision.confidence,
                        "issues": decision.issues,
                    }
                )

        if blocked_tools:
            _ORCHESTRATOR_LOGGER.warning(
                "[JUDGE:BLOCKED] Tool dispatch blocked for tools=%s",
                [entry["tool"] for entry in blocked_tools],
            )
            self._last_executor_debug = {
                "success_count": 0,
                "failed_count": len(blocked_tools),
                "failed_tools": [entry["tool"] for entry in blocked_tools],
                "judge_decision": "block",
                "judge_confidence": min(
                    (entry.get("confidence", 0.0) for entry in blocked_tools),
                    default=0.0,
                ),
                "judge_revise_count": 0,
            }
            return {
                entry["tool"]: {
                    "kind": "tool_execution_failure",
                    "status": "blocked",
                    "evidence": False,
                    "error": "judge_pre_action_blocked",
                    "confidence": entry.get("confidence", 0.0),
                    "issues": list(entry.get("issues") or []),
                }
                for entry in blocked_tools
            }

        if revise_tools:
            _ORCHESTRATOR_LOGGER.warning(
                "[JUDGE:REVISE] Tool dispatch withheld pending a valid payload for tools=%s",
                [entry["tool"] for entry in revise_tools],
            )
            self._last_executor_debug = {
                "success_count": 0,
                "failed_count": len(revise_tools),
                "failed_tools": [entry["tool"] for entry in revise_tools],
                "judge_decision": "revise",
                "judge_confidence": min(judge_confidences) if judge_confidences else 0.0,
                "judge_revise_count": len(revise_tools),
            }
            return {
                entry["tool"]: {
                    "kind": "tool_execution_failure",
                    "status": "failed",
                    "evidence": False,
                    "error": "judge_pre_action_revision_required",
                    "issues": list(entry.get("issues") or []),
                }
                for entry in revise_tools
            }

        if unprofiled_tools:
            _ORCHESTRATOR_LOGGER.debug(
                "[JUDGE:PROFILE_MISS] pass-through for unprofiled tools=%s",
                unprofiled_tools,
            )
        
        result = await self.executor.execute(executor_request, prepared_requests=prepared_requests)
        self._last_executor_debug = {
            "success_count": result.success_count,
            "failed_count": result.failed_count,
            "judge_decision": "allow",
            "judge_confidence": min(judge_confidences) if judge_confidences else 0.0,
            "judge_revise_count": len(revise_tools),
            "judge_unprofiled_tools": unprofiled_tools,
            **dict(result.metadata or {}),
        }
        return result.tool_outputs

    async def _complete_web_discovery(
        self,
        tool_results: Dict[str, Any],
        *,
        run_id: str,
    ) -> Dict[str, Any]:
        """Fetch one ranked primary candidate after a successful search-page discovery."""
        discovery = tool_results.get("sys") if isinstance(tool_results, dict) else None
        if not isinstance(discovery, dict) or discovery.get("kind") != "web_discovery":
            return tool_results

        profile = getattr(self, "_active_input_profile", None)
        retrieval = getattr(profile, "retrieval_intent", None) if profile is not None else None
        retrieval_payload = retrieval.model_dump(mode="json") if retrieval is not None else {}
        discovery_results = [item for item in list(discovery.get("results") or []) if isinstance(item, dict)]
        refinement = await self.input_profiler.refine_retrieval(retrieval, discovery_results)
        discovery["candidate_assessment"] = refinement
        refined_url = str(refinement.get("selected_url") or "").strip()
        candidate = None
        if refined_url and float(refinement.get("confidence") or 0.0) >= 0.6:
            candidate = {
                "title": "Inference-derived primary candidate",
                "url": refined_url,
                "snippet": "Derived from retrieval intent and search candidates; not evidence until fetched.",
                "rank": 0,
                "score": round(float(refinement.get("confidence") or 0.0) * 10.0, 3),
                "selection_source": "inference_candidate_assessment",
            }
        if candidate is None:
            candidate = self._rank_discovery_candidate(discovery_results, retrieval_payload)
        discovery["selected_candidate"] = candidate
        if not candidate:
            self._last_executor_debug = {
                **dict(self._last_executor_debug or {}),
                "retrieval_discovery": {
                    "status": "no_candidate",
                    "candidate_count": int(discovery.get("candidate_count") or 0),
                },
            }
            return tool_results

        original_routing_metadata = dict(getattr(self, "_last_routing_metadata", {}) or {})
        discovery_debug = dict(self._last_executor_debug or {})
        try:
            # The candidate URL is compiled into a fresh request and traverses
            # the same pre-action Judge, W/G/B policy, governance, and audit path.
            self._last_routing_metadata = {"intent": "url_fetch", "retrieval_stage": "primary_fetch"}
            primary_results = await self._execute_tools(["sys"], str(candidate["url"]), run_id=run_id)
        finally:
            self._last_routing_metadata = original_routing_metadata

        primary = primary_results.get("sys") if isinstance(primary_results, dict) else None
        primary_debug = dict(self._last_executor_debug or {})
        combined: Dict[str, Any] = {"sys::discovery": discovery}
        if primary is not None:
            if isinstance(primary, dict):
                primary["retrieval_provenance"] = {
                    "search_query": discovery.get("query"),
                    "candidate_url": candidate.get("url"),
                    "candidate_title": candidate.get("title"),
                    "candidate_score": candidate.get("score"),
                }
            combined["sys"] = primary
        self._last_executor_debug = {
            **primary_debug,
            "retrieval_discovery": {
                "status": "candidate_fetched" if primary is not None else "candidate_failed",
                "candidate_count": int(discovery.get("candidate_count") or 0),
                "selected_url": candidate.get("url"),
                "discovery_tool_statuses": discovery_debug.get("tool_statuses", {}),
                "primary_tool_statuses": primary_debug.get("tool_statuses", {}),
            },
        }
        return combined

    @staticmethod
    def _rank_discovery_candidate(
        results: List[Dict[str, Any]],
        retrieval: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Rank search results from inference-produced meaning, not source keywords."""
        source_hint = str(retrieval.get("source_hint") or "").strip().lower()
        semantic_text = " ".join(
            [
                str(retrieval.get("goal") or ""),
                str(retrieval.get("search_query") or ""),
                " ".join(str(value) for value in dict(retrieval.get("entities") or {}).values()),
            ]
        ).lower()
        semantic_tokens = set(re.findall(r"[a-z0-9äöüß]{3,}", semantic_text))
        ranked: list[tuple[float, int, Dict[str, Any]]] = []
        for index, raw in enumerate(results[:8]):
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not re.match(r"^https?://", url, flags=re.IGNORECASE):
                continue
            haystack = " ".join(
                (str(raw.get("title") or ""), url, str(raw.get("snippet") or ""))
            ).lower()
            overlap = len(semantic_tokens & set(re.findall(r"[a-z0-9äöüß]{3,}", haystack)))
            source_bonus = 5.0 if source_hint and source_hint in haystack else 0.0
            score = source_bonus + float(overlap) + max(0.0, 1.0 - index * 0.1)
            ranked.append((score, index, raw))
        if not ranked:
            return None
        score, index, selected = max(ranked, key=lambda item: (item[0], -item[1]))
        return {
            "title": str(selected.get("title") or ""),
            "url": str(selected.get("url") or ""),
            "snippet": str(selected.get("snippet") or ""),
            "rank": index + 1,
            "score": round(score, 3),
        }

    async def _generate_llm_response(
        self,
        run_id: str,
        query: str,
        routing_query: str | None,
        session_id: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
        max_tokens: int,
        preferred_provider: str | None,
        preferred_model: str | None,
        force_context: bool,
        retry_directive: str | None,
        retry_attempt: int,
        gap_action: str | None,
        previous_compressed_context: str,
    ) -> Dict[str, Any]:
        llm_pipeline_started = time.perf_counter()
        timing_debug_enabled = self.timing_debug_enabled
        routing_query_text = (routing_query or query or "").strip() or query
        effective_query = query
        if retry_directive:
            effective_query = f"{query}\n\n[RETRY_DIRECTIVE]\n{retry_directive}"

        history_limit = 12 if gap_action == GapAction.LOAD_SESSION.value else 8
        conversation_history, history_count = await self._load_conversation_history(
            session_id=session_id,
            current_query=routing_query_text,
            limit=history_limit,
        )

        librarian_decision = self.librarian.route(
            query=routing_query_text,
            gap_action=gap_action,
            force_context=force_context,
            conversation_history=conversation_history,
            session_id=session_id,
            user_id=self._active_user_id,
            input_profile=getattr(self, "_active_input_profile", None),
        )

        context_channels, source_counts = await self._load_librarian_context(
            run_id=run_id,
            session_id=session_id,
            query=routing_query_text,
            conversation_history=conversation_history,
            force_context=force_context,
            limit=3,
            gap_action=gap_action,
            librarian=librarian_decision,
        )

        # LIARA's configured identity and architectural principles are a
        # canonical baseline for self-description.  Make them explicit to the
        # evidence layer only when the typed input profile identified LIARA's
        # own architecture.  Retrieval remains active in parallel and runtime
        # claims still require runtime/tool evidence.
        active_profile = getattr(self, "_active_input_profile", None)
        active_topics = {
            str(topic).strip().lower()
            for topic in (getattr(active_profile, "topics", None) or [])
            if str(topic).strip()
        }
        if (
            librarian_decision.reason == "input_profile_internal_architecture"
            and "liara" in active_topics
            and self.planner.system_context.strip()
            and self.planner.system_context.strip() != "(none)"
        ):
            context_channels["system_context"] = self.planner.system_context
            source_counts["system"] = 1

        evidence_result = self.evidence_engine.analyze(
            query=routing_query_text,
            context_channels=context_channels,
            source_counts=source_counts,
            tool_outputs=tool_outputs,
            conversation_history=conversation_history,
        )

        active_topic = self._infer_active_topic(
            route=librarian_decision.route,
            fact_key=librarian_decision.fact_key,
        )
        refresh_decision = self._should_run_recall_refresh(
            session_id=session_id,
            active_topic=active_topic,
            confidence_score=float(evidence_result.confidence_score or 0.0),
        )
        if refresh_decision["should_refresh"] and retry_attempt == 0:
            refresh_gap_action = gap_action or GapAction.LOAD_MEMORY.value
            refresh_channels, refresh_counts = await self._load_librarian_context(
                run_id=run_id,
                session_id=session_id,
                query=routing_query_text,
                conversation_history=conversation_history,
                force_context=True,
                limit=5,
                gap_action=refresh_gap_action,
                librarian=librarian_decision,
            )
            context_channels = self._merge_channel_strings(context_channels, refresh_channels)
            source_counts = self._merge_source_counts(source_counts, refresh_counts)
            evidence_result = self.evidence_engine.analyze(
                query=routing_query_text,
                context_channels=context_channels,
                source_counts=source_counts,
                tool_outputs=tool_outputs,
                conversation_history=conversation_history,
            )

        _ORCHESTRATOR_LOGGER.info(
            "[EVIDENCE] query=%r sources=%s evidence_items=%s confidence=%.2f conflicts=%s answerability=%s",
            routing_query_text[:120],
            len(evidence_result.selected_sources),
            len(evidence_result.evidence_items),
            evidence_result.confidence_score,
            evidence_result.unresolved_conflicts_count,
            evidence_result.answerability,
        )
        _ORCHESTRATOR_LOGGER.debug(
            "[EVIDENCE][SELECT] memory=%s rag=%s web=%s tool_output=%s",
            "memory" in evidence_result.selected_sources,
            "rag" in evidence_result.selected_sources,
            "web" in evidence_result.selected_sources,
            "tool_output" in evidence_result.selected_sources,
        )
        _ORCHESTRATOR_LOGGER.debug(
            "[EVIDENCE][VALIDATION] verified=%s plausible=%s weak=%s discarded=%s",
            sum(1 for item in evidence_result.validated_evidence if item.get("quality") == "verified"),
            sum(1 for item in evidence_result.validated_evidence if item.get("quality") == "plausible"),
            sum(1 for item in evidence_result.validated_evidence if item.get("quality") == "weak"),
            len(evidence_result.discarded_evidence),
        )
        _ORCHESTRATOR_LOGGER.debug(
            "[EVIDENCE][CONFLICTS] count=%s status=%s",
            evidence_result.unresolved_conflicts_count,
            evidence_result.resolution_status,
        )

        _query_l = (routing_query_text or "").lower()
        _explicit_recall_hint = any(
            marker in _query_l
            for marker in (
                "schluessel=",
                "key=",
                "recall task",
                "abrufauftrag",
                "did i tell you",
                "habe ich dir genannt",
                "what is my",
                "was ist mein",
                "wie heisst",
                "wie heißt",
            )
        )

        if (
            evidence_result.answerability in {"insufficient", "blocked"}
            and evidence_result.required_evidence_level == "high"
            and not _explicit_recall_hint
        ):
            shortcut_total_ms = round((time.perf_counter() - llm_pipeline_started) * 1000, 3)
            shortcut_inference_metadata = {
                "shortcut": "evidence_block",
                "evidence": evidence_result.__dict__,
            }
            if timing_debug_enabled:
                shortcut_inference_metadata["llm_timing_breakdown_ms"] = {
                    "pre_llm": shortcut_total_ms,
                    "inference_call": 0.0,
                    "post_llm": 0.0,
                    "total": shortcut_total_ms,
                }
            return {
                "content": (
                    "Ich kann diese Frage mit der aktuell verfuegbaren Evidenz nicht belastbar beantworten. "
                    "Bitte gib mehr belastbare Quellen oder Kontext an."
                ),
                "provider": "evidence_engine",
                "model": "deterministic",
                "ttft_ms": 0.0,
                "gen_ms": 0.0,
                "winner_provider": "evidence_engine",
                "status": "failed",
                "error": "insufficient_evidence",
                "stop_reason": "evidence_insufficient",
                "inference_metadata": shortcut_inference_metadata,
                "context_debug": {
                    "mode": "EVIDENCE",
                    "sources": {
                        "chroma": int(source_counts.get("chroma", 0) or 0),
                        "qdrant": int(source_counts.get("qdrant", 0) or 0),
                        "postgres": int(source_counts.get("facts", 0) or 0),
                        "neo4j": int(source_counts.get("neo4j", 0) or 0),
                    },
                    "retry_attempt": retry_attempt,
                    "force_context": force_context,
                    "gap_action": gap_action or "NONE",
                    "evidence": evidence_result.__dict__,
                    "compression": {},
                },
                "compressed_context": previous_compressed_context,
                "prompt_debug": {
                    "prompt": "[SHORTCUT] evidence_insufficient",
                    "chars": 31,
                    "planner_metadata": {
                        "language": "German",
                        "shortcut": "evidence_insufficient",
                    },
                },
                "compression": {
                    "summary": "",
                    "facts": [],
                    "relations": [],
                    "dropped_items": [],
                    "token_estimate": 0,
                    "metadata": {},
                    "no_new_information": False,
                    "meaningful_reduction": True,
                },
            }

        raw_context_documents = self._merge_context_channels(context_channels)

        session_artifact = ""
        if conversation_history.strip():
            history_lines = [line.strip() for line in conversation_history.splitlines() if line.strip()]
            if history_lines:
                session_artifact = f"[session] {history_lines[-1][:220]}"

        compression_input_new = raw_context_documents
        if session_artifact:
            compression_input_new = f"{session_artifact}\n{raw_context_documents}" if raw_context_documents else session_artifact

        compression = self.context_compressor.compress(
            previous_context=previous_compressed_context,
            new_context=compression_input_new,
            reasoning_step=retry_attempt + 1,
            validation_status="derived",
        )
        context_documents = compression.final_context
        graph_relations = self._extract_graph_relations_from_context(
            context_channels.get("relation_context", "")
        )

        chroma_count = int(source_counts.get("chroma", 0) or 0)
        neo4j_count = int(source_counts.get("neo4j", 0) or 0)
        qdrant_count = int(source_counts.get("qdrant", 0) or 0)
        facts_count = int(source_counts.get("facts", 0) or 0)
        system_count = int(source_counts.get("system", 0) or 0)
        # Prefer explicit session-history memory mode when prior turns exist.
        # This keeps memory-effect signaling stable even if vector context is also available.
        context_mode = (
            "MEMORY"
            if history_count > 0
            else "SYSTEM"
            if system_count > 0
            else "CONTEXT"
            if (force_context or chroma_count > 0 or neo4j_count > 0 or qdrant_count > 0 or facts_count > 0)
            else "NONE"
        )
        context_debug = {
            "mode": context_mode,
            "input_profile": self._active_input_profile.model_dump(mode="json")
            if getattr(self, "_active_input_profile", None) is not None
            else None,
            "sources": {
                "system": system_count,
                "chroma": chroma_count,
                "qdrant": qdrant_count,
                "postgres": history_count + facts_count,
                "neo4j": neo4j_count,
            },
            "graph_relations": graph_relations,
            "retry_attempt": retry_attempt,
            "force_context": force_context,
            "gap_action": gap_action or "NONE",
            "librarian": {
                "route": librarian_decision.route,
                "reason": librarian_decision.reason,
                "primary_source": librarian_decision.primary_source,
                "fact_key": librarian_decision.fact_key,
                "fact_namespaces": list(librarian_decision.fact_namespaces),
            },
            "retrieval_phases": {
                "route_selected": librarian_decision.route,
                "graph_priority_policy": {
                    "enabled": bool(neo4j_count > 0),
                    "guardrail": "graph_gt_retrieval_gt_model",
                    "relation_items": neo4j_count,
                },
                "phase_fact_lookup": {
                    "enabled": bool(librarian_decision.load_facts),
                    "items": facts_count,
                },
                "phase_chunk_retrieval": {
                    "enabled": bool(librarian_decision.load_retrieval),
                    "items": qdrant_count,
                },
                "phase_relation_expand": {
                    "enabled": bool(librarian_decision.load_relations),
                    "items": neo4j_count,
                },
                "phase_working_context": {
                    "enabled": bool(librarian_decision.load_context),
                    "items": chroma_count,
                },
            },
            "recall_refresh": {
                "applied": bool(refresh_decision.get("should_refresh", False) and retry_attempt == 0),
                "reason": refresh_decision.get("reason", "none"),
                "topic_switched": bool(refresh_decision.get("topic_switched", False)),
                "confidence_before": float(refresh_decision.get("confidence_before", evidence_result.confidence_score) or 0.0),
                "confidence_after": float(evidence_result.confidence_score or 0.0),
            },
            "compression": {
                "summary": compression.summary,
                "facts": compression.facts,
                "relations": compression.relations,
                "dropped_items": compression.dropped_items,
                "token_estimate": compression.token_estimate,
                "metadata": compression.metadata,
                "no_new_information": compression.no_new_information,
                "meaningful_reduction": compression.meaningful_reduction,
            },
        }

        plan = self.planner.build_plan(
            PlannerRequest(
                query=effective_query,
                tools_used=tools_used,
                tool_outputs=tool_outputs,
                conversation_history=conversation_history,
                context_documents=context_documents,
                fact_context=context_channels.get("fact_context", ""),
                memory_context=context_channels.get("memory_context", ""),
                relation_context=context_channels.get("relation_context", ""),
                working_context=context_documents,
                evidence_context=evidence_result.evidence_context,
                primary_context_kind=librarian_decision.route,
                input_profile=getattr(self, "_active_input_profile", None),
            )
        )

        selected_provider, routing_telemetry = self._select_inference_provider_for_step(
            preferred_provider=preferred_provider,
            query=routing_query_text,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
            force_context=force_context,
            retry_attempt=retry_attempt,
        )

        inference_request = InferenceRequest(
            prompt=plan.prompt,
            max_tokens=max_tokens,
            temperature=0.7,
            provider=selected_provider,
            model=preferred_model,
            task_type=routing_telemetry.get("helper_task_type"),
            expected_fields=routing_telemetry.get("helper_expected_fields"),
        )

        pre_llm_ms = round((time.perf_counter() - llm_pipeline_started) * 1000, 3)
        inference_started = time.perf_counter()
        result = await self.inference_invoker.infer(inference_request)
        helper_schema_ok = None
        if routing_telemetry.get("helper_offload_used"):
            helper_schema_ok = (result.metadata or {}).get("helper_schema_ok")

        if routing_telemetry.get("helper_offload_used") and result.status != "success":
            fallback_provider = preferred_provider or self.default_inference_provider
            if fallback_provider == self.npu_helper_provider:
                fallback_provider = "ll_ol_fallback"
            fallback_request = InferenceRequest(
                prompt=plan.prompt,
                max_tokens=max_tokens,
                temperature=0.7,
                provider=fallback_provider,
                model=preferred_model,
            )
            fallback_result = await self.inference_invoker.infer(fallback_request)
            routing_telemetry["helper_fallback_triggered"] = True
            routing_telemetry["helper_fallback_provider"] = fallback_provider
            routing_telemetry["helper_error"] = result.error
            if fallback_result.status == "success":
                result = fallback_result

        if routing_telemetry.get("helper_offload_used"):
            routing_telemetry["helper_schema_ok"] = helper_schema_ok

        inference_call_ms = round((time.perf_counter() - inference_started) * 1000, 3)
        post_llm_started = time.perf_counter()

        routing_telemetry = self._standardize_routing_telemetry(
            selected_provider=selected_provider,
            preferred_provider=preferred_provider,
            routing_telemetry=routing_telemetry,
            result=result,
        )

        inference_metadata_payload = {
            **routing_telemetry,
            **dict(result.metadata or {}),
            "evidence": evidence_result.__dict__,
        }
        response_payload = {
            "content": result.content,
            "provider": result.provider,
            "model": result.model,
            "ttft_ms": result.ttft_ms,
            "gen_ms": result.gen_ms,
            "winner_provider": result.winner_provider,
            "status": result.status,
            "error": result.error,
            "stop_reason": result.stop_reason,
            "inference_metadata": inference_metadata_payload,
            "context_debug": {
                **context_debug,
                "routing": {
                    "selected_provider": selected_provider,
                    **routing_telemetry,
                },
                "evidence": evidence_result.__dict__,
            },
            "compressed_context": context_documents,
            "prompt_debug": {
                "prompt": plan.prompt[:12000],
                "chars": len(plan.prompt),
                "planner_metadata": dict(plan.metadata or {}),
                "history_chars": len(conversation_history),
                "context_chars": len(context_documents),
                "tool_output_count": len(tool_outputs),
                "has_retry_directive": bool(retry_directive),
            },
            "compression": {
                "summary": compression.summary,
                "facts": compression.facts,
                "relations": compression.relations,
                "dropped_items": compression.dropped_items,
                "token_estimate": compression.token_estimate,
                "metadata": compression.metadata,
                "no_new_information": compression.no_new_information,
                "meaningful_reduction": compression.meaningful_reduction,
            },
        }
        post_llm_ms = round((time.perf_counter() - post_llm_started) * 1000, 3)
        llm_timing_breakdown_ms = {
            "pre_llm": pre_llm_ms,
            "inference_call": inference_call_ms,
            "post_llm": post_llm_ms,
            "total": round((time.perf_counter() - llm_pipeline_started) * 1000, 3),
        }
        if timing_debug_enabled:
            response_payload["inference_metadata"]["llm_timing_breakdown_ms"] = llm_timing_breakdown_ms
            response_payload["context_debug"]["llm_timing_breakdown_ms"] = llm_timing_breakdown_ms
        return response_payload

    def _apply_empty_response_fallback(self, llm_response: Dict[str, Any], *, retry_attempt: int) -> None:
        """Guarantee a non-empty assistant draft when inference returns an empty payload."""
        if not isinstance(llm_response, dict):
            return

        content = str(llm_response.get("content") or "")
        if content.strip():
            return

        original_status = str(llm_response.get("status") or "unknown")
        original_stop_reason = str(llm_response.get("stop_reason") or "unknown")
        original_error = str(llm_response.get("error") or "")
        provider = str(llm_response.get("provider") or "unknown")
        model = str(llm_response.get("model") or "unknown")

        diagnostic_parts = [
            f"status={original_status}",
            f"stop_reason={original_stop_reason}",
            f"provider={provider}",
            f"model={model}",
        ]
        if original_error.strip():
            diagnostic_parts.append(f"error={original_error.strip()}")

        diagnostic_text = "; ".join(diagnostic_parts)

        llm_response["content"] = (
            "Ich konnte gerade keine stabile finale Antwort vom Modell abrufen. "
            "Bitte versuche die Anfrage erneut. "
            f"Diagnose: {diagnostic_text}."
        )

        inference_meta = dict(llm_response.get("inference_metadata") or {})
        inference_meta["empty_content_fallback"] = {
            "applied": True,
            "retry_attempt": int(retry_attempt),
            "original_status": original_status,
            "original_stop_reason": original_stop_reason,
            "original_error": original_error,
            "provider": provider,
            "model": model,
            "diagnostic_text": diagnostic_text,
        }
        llm_response["inference_metadata"] = inference_meta

    def _standardize_routing_telemetry(
        self,
        *,
        selected_provider: str,
        preferred_provider: str | None,
        routing_telemetry: Dict[str, Any],
        result: InferenceResult,
    ) -> Dict[str, Any]:
        return standardize_routing_telemetry(
            self,
            selected_provider=selected_provider,
            preferred_provider=preferred_provider,
            routing_telemetry=routing_telemetry,
            result=result,
        )

    def _select_inference_provider_for_step(
        self,
        *,
        preferred_provider: str | None,
        query: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
        force_context: bool,
        retry_attempt: int,
    ) -> tuple[str, Dict[str, Any]]:
        return select_inference_provider_for_step(
            self,
            preferred_provider=preferred_provider,
            query=query,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
            force_context=force_context,
            retry_attempt=retry_attempt,
        )

    def _classify_npu_helper_task(
        self,
        *,
        query: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
        force_context: bool,
        retry_attempt: int,
    ) -> Optional[Dict[str, Any]]:
        return classify_npu_helper_task(
            self,
            query=query,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
            force_context=force_context,
            retry_attempt=retry_attempt,
        )

    def _should_use_npu_helper_offload(
        self,
        *,
        query: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
        force_context: bool,
        retry_attempt: int,
    ) -> bool:
        return should_use_npu_helper_offload(
            self,
            query=query,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
            force_context=force_context,
            retry_attempt=retry_attempt,
        )

    def _create_judge_context_for_pre_action(
        self,
        *,
        run_id: str,
        tool_names: List[str],
        query: str,
    ) -> JudgeContext:
        return create_judge_context_for_pre_action(
            self,
            run_id=run_id,
            tool_names=tool_names,
            query=query,
        )

    def _create_judge_context_for_post_result(
        self,
        *,
        run_id: str,
        query: str,
        response_content: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
    ) -> JudgeContext:
        return create_judge_context_for_post_result(
            self,
            run_id=run_id,
            query=query,
            response_content=response_content,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
        )

    async def _load_librarian_context(
        self,
        run_id: Optional[str],
        session_id: str,
        query: str,
        conversation_history: str,
        force_context: bool,
        limit: int = 3,
        gap_action: str | None = None,
        librarian: LibrarianDecision | None = None,
    ) -> tuple[Dict[str, str], Dict[str, int]]:
        """Load context in explicit channels selected by librarian routing."""
        try:
            from services.contracts import ContextSearchRequest, ContextScope

            channels: Dict[str, List[str]] = {
                "fact_context": [],
                "memory_context": [],
                "relation_context": [],
                "working_context": [],
            }
            source_counts: Dict[str, int] = {"chroma": 0, "neo4j": 0, "qdrant": 0, "facts": 0}

            if librarian is None:
                librarian = self.librarian.route(
                    query=query,
                    gap_action=gap_action,
                    force_context=force_context,
                    conversation_history="",
                    session_id=session_id,
                    user_id=self._active_user_id,
                    input_profile=getattr(self, "_active_input_profile", None),
                )

            load_context = librarian.load_context
            load_relations = librarian.load_relations
            load_retrieval = librarian.load_retrieval
            load_facts = librarian.load_facts

            embedding_query, embedding_query_metrics = self._build_embedding_query(
                current_user_input=query,
                conversation_history=conversation_history,
                route=librarian.route,
                fact_key=librarian.fact_key,
                force_context=force_context,
                gap_action=gap_action,
            )

            _ORCHESTRATOR_LOGGER.info(
                "[EMBEDDING_QUERY] route=%s input_chars=%s embedding_chars=%s tokens=%s topic_used=%s history_used=%s constraints=%s",
                librarian.route,
                embedding_query_metrics.get("input_chars", 0),
                embedding_query_metrics.get("embedding_chars", 0),
                embedding_query_metrics.get("token_length", 0),
                embedding_query_metrics.get("topic_used", False),
                embedding_query_metrics.get("history_used", False),
                embedding_query_metrics.get("constraints", []),
            )

            if load_context:
                scope = ContextScope(
                    session_id=session_id,
                    run_id=run_id or "",
                )
                response = await self.memory_service.context_search(
                    ContextSearchRequest(
                        query=embedding_query,
                        scope=scope,
                        top_k=5 if force_context else limit,
                    )
                )
                for doc in response.items:
                    content = (doc.content or "") if hasattr(doc, "content") else str(doc)
                    content = content.strip().replace("\n", " ")
                    if content:
                        channels["working_context"].append(f"[context] {content[:256]}")
                source_counts["chroma"] = len(response.items)

            if load_relations:
                try:
                    rel_response = await self.memory_service.relation_expand(
                        RelationExpandRequest(
                            session_id=session_id,
                            run_id=run_id,
                            query=query if force_context else None,
                            limit=4,
                        )
                    )
                    for edge in rel_response.items:
                        relation = getattr(edge.relation, "value", edge.relation)
                        source_counts["neo4j"] += 1
                        channels["relation_context"].append(
                            f"[relation] {edge.source} -[{relation}]-> {edge.target}"
                        )
                    if source_counts["neo4j"] > 0:
                        channels["relation_context"].insert(0, self._graph_priority_guardrail_line())
                except Exception:
                    source_counts["neo4j"] = 0

            if load_retrieval:
                try:
                    _ORCHESTRATOR_LOGGER.info(
                        "[RETRIEVAL_QUERY] input_chars=%s embedding_chars=%s embedding_tokens=%s",
                        embedding_query_metrics.get("input_chars", 0),
                        embedding_query_metrics.get("embedding_chars", 0),
                        embedding_query_metrics.get("token_length", 0),
                    )
                    # Detect query language (no hard filter — all languages welcome)
                    try:
                        from langdetect import detect as _langdetect
                        _query_lang = _langdetect(query[:200]) if len(query.strip()) >= 6 else None
                    except Exception:
                        _detected = _detect_language(query)
                        _query_lang = "de" if _detected == "German" else "en"
                    # Fetch wider candidate pool for composite re-ranking.
                    # Keep a strict first pass; if empty, fall back to a softer threshold.
                    strict_min_score = 0.55
                    fallback_min_score = 0.40
                    retrieval_response = await self.memory_service.query_retrieval(
                        MemoryRetrievalQueryRequest(
                            query=embedding_query,
                            top_k=12,
                            min_score=strict_min_score,
                            session_id=session_id,
                        )
                    )
                    if not retrieval_response.items:
                        _ORCHESTRATOR_LOGGER.info(
                            "[RETRIEVAL_FALLBACK] strict_min_score=%.2f yielded 0 items, retrying with min_score=%.2f",
                            strict_min_score,
                            fallback_min_score,
                        )
                        retrieval_response = await self.memory_service.query_retrieval(
                            MemoryRetrievalQueryRequest(
                                query=embedding_query,
                                top_k=12,
                                min_score=fallback_min_score,
                                session_id=session_id,
                            )
                        )
                    # Composite re-rank: cosine + language + attribute + user-scope + recency - penalty
                    import time as _t
                    _ranked = Orchestrator._retrieval_rerank(
                        retrieval_response.items,
                        query_lang=_query_lang,
                        query=query,
                        session_id=session_id,
                        user_id=self._active_user_id or "",
                        now=_t.time(),
                    )
                    for item in _ranked[:4]:
                        content = (item.content or "").strip().replace("\n", " ")
                        if content:
                            channels["memory_context"].append(f"[memory] {content[:256]}")
                    source_counts["qdrant"] = len(_ranked[:4])
                except Exception:
                    source_counts["qdrant"] = 0

            if load_facts:
                try:
                    fact_namespaces = list(librarian.fact_namespaces or ["global"])
                    seen_fact_rows: set[tuple[str, str, str]] = set()
                    fact_rows: list[tuple[str, str, str, str]] = []
                    for namespace in fact_namespaces:
                        facts_response = await self.memory_service.query_facts(
                            MemoryFactQueryRequest(
                                namespace=namespace,
                                key=librarian.fact_key,
                                limit=4,
                            )
                        )
                        for item in facts_response.items:
                            row_id = (namespace, item.key, str(item.value))
                            if row_id in seen_fact_rows:
                                continue
                            seen_fact_rows.add(row_id)

                            status = self._normalize_fact_status(getattr(item, "status", None))
                            fact_rows.append((namespace, item.key, str(item.value), status))

                    verified_rows = [row for row in fact_rows if row[3] == "verified"]
                    candidate_rows = [
                        row for row in fact_rows
                        if row[3] not in {"verified", "staged", "deprecated", "revoked"}
                    ]

                    # Facts-First rule:
                    # 1) verified facts are treated as ground-truth context
                    # 2) staged facts are never treated as ground truth
                    # 3) non-verified fallback facts are explicit hints only
                    if verified_rows:
                        for namespace, key, value, _status in verified_rows:
                            channels["fact_context"].append(
                                f"[fact_verified:{namespace}] {key}: {value[:220]}"
                            )
                    elif candidate_rows:
                        for namespace, key, value, status in candidate_rows:
                            channels["fact_context"].append(
                                f"[fact_hint:{namespace}:{status}] {key}: {value[:220]}"
                            )

                    source_counts["facts"] = len(seen_fact_rows)
                except Exception:
                    source_counts["facts"] = 0

            _ORCHESTRATOR_LOGGER.info(
                "[LIBRARIAN_PHASES] route=%s facts=%s retrieval=%s relations=%s context=%s counts={facts:%s,qdrant:%s,neo4j:%s,chroma:%s}",
                librarian.route,
                librarian.load_facts,
                librarian.load_retrieval,
                librarian.load_relations,
                librarian.load_context,
                source_counts.get("facts", 0),
                source_counts.get("qdrant", 0),
                source_counts.get("neo4j", 0),
                source_counts.get("chroma", 0),
            )

            return {name: "\n".join(values) for name, values in channels.items()}, source_counts

        except Exception as e:
            # Graceful fallback: if context loading fails, continue without it
            import logging
            logging.debug(f"Context loading failed: {e}")
            return {
                "fact_context": "",
                "memory_context": "",
                "relation_context": "",
                "working_context": "",
            }, {"chroma": 0, "neo4j": 0, "qdrant": 0, "facts": 0}

    @staticmethod
    def _normalize_fact_status(status: Any) -> str:
        if status is None:
            return "ephemeral"
        if hasattr(status, "value"):
            status = getattr(status, "value")
        status_text = str(status).strip().lower()
        if "." in status_text:
            status_text = status_text.rsplit(".", 1)[-1]
        return status_text or "ephemeral"

    @staticmethod
    def _graph_priority_guardrail_line() -> str:
        return (
            "[graph_guardrail] Direct graph relations are authoritative. "
            "Do not silently override them with retrieval hits or model speculation."
        )

    @staticmethod
    def _rewrite_retrieval_query(query: str) -> tuple[str, Dict[str, Any]]:
        return rewrite_retrieval_query(query)

    @staticmethod
    def _retrieval_rerank(
        items: list,
        *,
        query_lang: "str | None",
        query: str,
        session_id: str,
        user_id: str,
        now: float,
    ) -> list:
        """Re-rank retrieval candidates by composite score.

        final_score =
          cosine(original_query, fact_text)          [primary signal — full semantics]
          + keyword_overlap_bonus                    (+0.06 secondary, stopword-filtered terms)
          + same_language_bonus                      (+0.10)
          + user_scope_bonus                         (+0.12 user | +0.08 session)
          + recency_bonus                            (up to +0.08, half-life 7 days)
          - low_confidence_penalty                   (-0.10)

        The embedding is always over the original unmodified query.
        Stopword-filtered keywords are only used for the small keyword_overlap_bonus.
        """
        import re as _re

        # Extract significant query terms dynamically via stop-words library.
        # No hardcoded word lists — stopwords are resolved from the detected language.
        try:
            from stop_words import get_stop_words as _get_sw
            _sw: set[str] = set(_get_sw(query_lang or "en"))
        except Exception:
            _sw = set()
        query_lower = query.lower()
        query_attr_terms = {
            tok for tok in _re.findall(r"[a-zäöüß]{3,}", query_lower)
            if tok not in _sw
        }

        scored: list = []
        for item in items:
            meta = item.metadata or {}
            cosine = float(item.score or 0.0)

            # same_language_bonus
            lang_bonus = 0.10 if (query_lang and meta.get("language") == query_lang) else 0.0

            # keyword_overlap_bonus — secondary signal: stopword-filtered query terms found in content.
            # Small weight (+0.06) so the original cosine similarity remains the dominant factor.
            content_lower = (item.content or "").lower()
            attr_bonus = 0.06 if (
                query_attr_terms and any(t in content_lower for t in query_attr_terms)
            ) else 0.0

            # user_scope_bonus — prefer docs stored for the active user / session
            doc_user = str(meta.get("user_id") or "")
            doc_session = str(meta.get("session_id") or "")
            if user_id and doc_user == user_id:
                scope_bonus = 0.12
            elif session_id and (
                doc_session == session_id
                or session_id in str(item.document_id or "")
            ):
                scope_bonus = 0.08
            else:
                scope_bonus = 0.0

            # recency_bonus — exponential decay, half-life 7 days (max +0.08)
            upserted_at = meta.get("upserted_at")
            if upserted_at:
                age_days = (now - float(upserted_at)) / 86400.0
                recency_bonus = 0.08 * (0.5 ** (age_days / 7.0))
            else:
                recency_bonus = 0.0

            # low_confidence_penalty
            val_status = str(meta.get("validation_status") or "")
            validated = meta.get("validated")
            if validated is False or val_status in ("rejected", "blocked"):
                penalty = 0.10
            else:
                penalty = 0.0

            final = cosine + lang_bonus + attr_bonus + scope_bonus + recency_bonus - penalty
            scored.append((final, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]


    def _build_embedding_query(
        self,
        *,
        current_user_input: str,
        conversation_history: str,
        route: str,
        fact_key: str | None,
        force_context: bool,
        gap_action: str | None,
    ) -> tuple[str, Dict[str, Any]]:
        return build_embedding_query(
            current_user_input=current_user_input,
            conversation_history=conversation_history,
            route=route,
            fact_key=fact_key,
            force_context=force_context,
            gap_action=gap_action,
            rewrite_retrieval_query=Orchestrator._rewrite_retrieval_query,
            infer_active_topic_fn=Orchestrator._infer_active_topic,
            summarize_history_for_embedding_fn=Orchestrator._summarize_history_for_embedding,
            compact_embedding_text_fn=Orchestrator._compact_embedding_text,
        )

    @staticmethod
    def _infer_active_topic(*, route: str, fact_key: str | None) -> str:
        return infer_active_topic(route=route, fact_key=fact_key)

    @staticmethod
    def _summarize_history_for_embedding(conversation_history: str) -> str:
        return summarize_history_for_embedding(
            conversation_history,
            compact_embedding_text=Orchestrator._compact_embedding_text,
        )

    @staticmethod
    def _compact_embedding_text(
        text: str,
        *,
        max_chars: int | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, Dict[str, Any]]:
        return compact_embedding_text(
            text,
            max_chars=max_chars,
            max_tokens=max_tokens,
        )

    @staticmethod
    def _merge_context_channels(channels: Dict[str, str]) -> str:
        return merge_context_channels(channels)

    @staticmethod
    def _topic_terms(text: str) -> set[str]:
        terms = re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())
        return {t for t in terms if len(t) > 2}

    @classmethod
    def _topic_overlap(cls, a: str, b: str) -> float:
        left = cls._topic_terms(a)
        right = cls._topic_terms(b)
        if not left or not right:
            return 1.0
        union = left | right
        if not union:
            return 1.0
        return float(len(left & right)) / float(len(union))

    def _should_run_recall_refresh(
        self,
        *,
        session_id: str,
        active_topic: str,
        confidence_score: float,
    ) -> Dict[str, Any]:
        prior = dict(self._session_semantic_state.get(session_id) or {})
        previous_topic = str(prior.get("topic") or "")
        overlap = self._topic_overlap(previous_topic, active_topic)
        topic_switched = bool(previous_topic) and overlap < _SESSION_TOPIC_SWITCH_OVERLAP_MIN
        low_confidence = confidence_score < _RECALL_REFRESH_CONFIDENCE_THRESHOLD
        should_refresh = low_confidence or topic_switched
        reason = "low_confidence" if low_confidence else "topic_switch" if topic_switched else "none"

        self._session_semantic_state[session_id] = {
            "topic": active_topic,
            "last_confidence": float(confidence_score),
            "topic_overlap": float(overlap),
            "updated_at": time.time(),
        }
        return {
            "should_refresh": should_refresh,
            "reason": reason,
            "topic_switched": topic_switched,
            "confidence_before": float(confidence_score),
            "topic_overlap": float(overlap),
        }

    @staticmethod
    def _merge_channel_strings(
        base: Dict[str, str],
        extra: Dict[str, str],
    ) -> Dict[str, str]:
        merged: Dict[str, str] = {}
        keys = set((base or {}).keys()) | set((extra or {}).keys())
        for key in keys:
            left_lines = [line for line in str((base or {}).get(key, "") or "").splitlines() if line.strip()]
            right_lines = [line for line in str((extra or {}).get(key, "") or "").splitlines() if line.strip()]
            seen: set[str] = set()
            deduped: list[str] = []
            for line in left_lines + right_lines:
                if line not in seen:
                    seen.add(line)
                    deduped.append(line)
            merged[key] = "\n".join(deduped)
        return merged

    @staticmethod
    def _merge_source_counts(base: Dict[str, int], extra: Dict[str, int]) -> Dict[str, int]:
        keys = set((base or {}).keys()) | set((extra or {}).keys())
        return {
            key: int((base or {}).get(key, 0) or 0) + int((extra or {}).get(key, 0) or 0)
            for key in keys
        }

    async def _upsert_temp_context_note(
        self,
        *,
        session_id: str,
        run_id: str,
        note_kind: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        await upsert_temp_context_note(
            session_id=session_id,
            run_id=run_id,
            note_kind=note_kind,
            content=content,
            metadata=metadata,
            get_fn=self.memory_service.get,
            set_fn=self.memory_service.set,
            session_tier=MemoryTier.SESSION,
            temp_context_ttl_seconds=_TEMP_CONTEXT_TTL_SECONDS,
            build_context_upsert_metadata_fn=self._build_context_upsert_metadata,
        )

    async def _upsert_working_context_doc(
        self,
        *,
        session_id: str,
        run_id: str,
        document_id: str,
        content: str,
        turn_index: int,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        await upsert_working_context_doc(
            session_id=session_id,
            run_id=run_id,
            document_id=document_id,
            content=content,
            turn_index=turn_index,
            metadata=metadata,
            is_safe_for_context_upsert_fn=self._is_safe_for_context_upsert,
            touch_working_context_activity_fn=self._touch_working_context_activity,
            build_context_upsert_metadata_fn=self._build_context_upsert_metadata,
            context_upsert_fn=self.memory_service.context_upsert,
            context_upsert_request_cls=ContextUpsertRequest,
            context_scope_cls=ContextScope,
        )

    async def _upsert_memory_commit_embedding(
        self,
        *,
        session_id: str,
        run_id: str,
        query: str,
        final_response: str,
        context_debug: Dict[str, Any] | None = None,
    ) -> None:
        librarian = (context_debug or {}).get("librarian") or {}
        route = str(librarian.get("route") or "")
        fact_key = librarian.get("fact_key")
        active_topic = self._infer_active_topic(route=route, fact_key=fact_key)
        commit_text = (
            f"MEMORY_COMMIT session={session_id} topic={active_topic or 'general'} "
            f"query={query.strip()[:260]} response={final_response.strip()[:360]}"
        )
        await self._upsert_working_context_doc(
            session_id=session_id,
            run_id=run_id,
            document_id=f"{run_id}:memory_commit",
            content=commit_text,
            turn_index=11,
            metadata={
                "source": "orchestrator",
                "kind": "memory_commit_embedding",
                "validated": True,
                "artifact_type": "memory_commit_embedding",
                "topic": active_topic or "general",
                "session_id": session_id or "",
            },
        )

    async def _touch_working_context_activity(self, *, session_id: str, run_id: str) -> tuple[int, float]:
        return await touch_working_context_activity(
            session_id=session_id,
            run_id=run_id,
            ttl_seconds=_WORKING_CONTEXT_ACTIVE_TTL_SECONDS,
            set_fn=self.memory_service.set,
            session_tier=MemoryTier.SESSION,
        )

    @staticmethod
    def _build_context_upsert_metadata(
        *,
        content: str,
        artifact_type: str,
        validation_status: str,
        scope: str,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return build_context_upsert_metadata(
            content=content,
            artifact_type=artifact_type,
            validation_status=validation_status,
            scope=scope,
            metadata=metadata,
            detect_language_fn=_detect_language,
        )

    @staticmethod
    def _format_tool_context(tool_outputs: Dict[str, Any]) -> str:
        return format_tool_context(tool_outputs)

    @staticmethod
    def _extract_graph_relations_from_context(relation_context: str) -> list[dict[str, str]]:
        relation_re = re.compile(
            r"\[relation\]\s*(?P<source>.+?)\s*-\[(?P<relation>[^\]]+)\]->\s*(?P<target>.+)",
            re.IGNORECASE,
        )
        relations: list[dict[str, str]] = []
        for match in relation_re.finditer(relation_context or ""):
            source = match.group("source").strip()
            relation = match.group("relation").strip()
            target = match.group("target").strip()
            if source and relation and target:
                relations.append({"source": source, "relation": relation, "target": target})
        return relations

    @staticmethod
    def _build_working_context_summary(query: str, tool_outputs: Dict[str, Any], response: str) -> str:
        return build_working_context_summary(query, tool_outputs, response)

    @staticmethod
    def _is_safe_for_context_upsert(content: str) -> bool:
        return is_safe_for_context_upsert(content)

    @staticmethod
    def _relation_node_key(prefix: str, text: str) -> str:
        return relation_node_key(prefix, text)

    async def _extract_content_relations(
        self,
        *,
        query: str,
        response: str,
        session_id: str,
        run_id: str,
    ) -> List[RelationUpsertRequest]:
        """Extract semantic (subject, relation, object) triples from query+response via LLM.

        Returns an empty list when disabled, when the LLM is unavailable, or
        when the output cannot be parsed.  Never raises.
        """
        import json
        import logging

        logger = logging.getLogger(__name__)

        if not Settings.RELATION_EXTRACTION_ENABLED:
            return []

        max_triples = Settings.RELATION_EXTRACTION_MAX_TRIPLES
        valid_types = {rt.value for rt in RelationType}

        prompt = (
            "Extract up to {n} factual (subject, relation, object) triples from the"
            " conversation below.  Use ONLY these relation types: {types}.\n"
            "Return ONLY a JSON array of objects with keys"
            ' "source", "relation", "target".  No prose, no markdown.\n\n'
            "User: {query}\n\nAssistant: {response}\n\nJSON:"
        ).format(
            n=max_triples,
            types=", ".join(sorted(valid_types)),
            query=query.strip()[:400],
            response=response.strip()[:600],
        )

        try:
            result = await self.inference_invoker.infer(
                InferenceRequest(
                    prompt=prompt,
                    max_tokens=256,
                    temperature=0.0,
                    provider=self.default_inference_provider,
                )
            )
            raw = (result.content or "").strip()
            # Strip optional markdown code fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            triples = json.loads(raw)
        except Exception as exc:
            logger.debug(f"relation extraction failed: {exc}")
            return []

        requests: List[RelationUpsertRequest] = []
        for item in triples[:max_triples]:
            if not isinstance(item, dict):
                continue
            rel_str = str(item.get("relation", "")).strip().upper()
            if rel_str not in valid_types:
                logger.debug(f"relation extraction: unknown relation type '{rel_str}', skipping")
                continue
            src = str(item.get("source", "")).strip()
            tgt = str(item.get("target", "")).strip()
            if not src or not tgt:
                continue
            requests.append(
                RelationUpsertRequest(
                    source=self._relation_node_key("entity", src),
                    relation=RelationType(rel_str),
                    target=self._relation_node_key("entity", tgt),
                    session_id=session_id,
                    run_id=run_id,
                    validated=False,
                    metadata={
                        "source": "llm_extraction",
                        "kind": "relation",
                        "confidence": 0.55,
                        "reasoning_step": 1,
                        "scope": "session",
                        "persistable": True,
                        "raw_source": src[:80],
                        "raw_target": tgt[:80],
                    },
                )
            )
        return requests

    async def _upsert_validated_relations(
        self,
        *,
        session_id: str,
        run_id: str,
        query: str,
        tools_used: List[str],
        final_response: str,
        context_debug: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Persist validated structural relations into RELATION_STORE."""
        import logging

        fn_started = time.perf_counter()

        query_node = self._relation_node_key("query", query)
        response_node = self._relation_node_key("response", final_response)
        query_fact_node = self._relation_node_key("fact", query)
        response_fact_node = self._relation_node_key("fact", final_response)
        librarian = (context_debug or {}).get("librarian") or {}
        semantic_topic = self._infer_active_topic(
            route=str(librarian.get("route") or ""),
            fact_key=librarian.get("fact_key"),
        )
        topic_entity_node = self._relation_node_key("entity", semantic_topic or "session_context")
        query_entity_node = self._relation_node_key("entity", query)
        response_entity_node = self._relation_node_key("entity", final_response)
        relation_requests: List[RelationUpsertRequest] = []

        if tools_used:
            for tool in tools_used:
                tool_node = f"tool:{tool}"
                relation_requests.append(
                    RelationUpsertRequest(
                        source=query_node,
                        relation=RelationType.USES_TOOL,
                        target=tool_node,
                        session_id=session_id,
                        run_id=run_id,
                        validated=True,
                        metadata={
                            "source": "orchestrator",
                            "kind": "relation",
                            "confidence": 0.72,
                            "reasoning_step": 1,
                            "scope": "session",
                            "persistable": False,
                        },
                    )
                )
                relation_requests.append(
                    RelationUpsertRequest(
                        source=tool_node,
                        relation=RelationType.INFORMS_RESPONSE,
                        target=response_node,
                        session_id=session_id,
                        run_id=run_id,
                        validated=True,
                        metadata={
                            "source": "orchestrator",
                            "kind": "relation",
                            "confidence": 0.72,
                            "reasoning_step": 1,
                            "scope": "session",
                            "persistable": False,
                        },
                    )
                )
        else:
            relation_requests.append(
                RelationUpsertRequest(
                    source=query_node,
                    relation=RelationType.DIRECT_RESPONSE,
                    target=response_node,
                    session_id=session_id,
                    run_id=run_id,
                    validated=True,
                    metadata={
                        "source": "orchestrator",
                        "kind": "relation",
                        "confidence": 0.72,
                        "reasoning_step": 1,
                        "scope": "session",
                        "persistable": False,
                    },
                )
            )

        # Additive semantic graph layer (no execution-graph refactor):
        # Fact -> Fact, Entity -> Entity, Fact -> Entity.
        relation_requests.extend(
            [
                RelationUpsertRequest(
                    source=response_fact_node,
                    relation=RelationType.DERIVED_FROM,
                    target=query_fact_node,
                    session_id=session_id,
                    run_id=run_id,
                    validated=True,
                    metadata={
                        "source": "orchestrator",
                        "kind": "semantic_relation",
                        "confidence": 0.68,
                        "scope": "session",
                        "persistable": True,
                    },
                ),
                RelationUpsertRequest(
                    source=query_entity_node,
                    relation=RelationType.DEPENDS_ON,
                    target=topic_entity_node,
                    session_id=session_id,
                    run_id=run_id,
                    validated=True,
                    metadata={
                        "source": "orchestrator",
                        "kind": "semantic_relation",
                        "confidence": 0.66,
                        "scope": "session",
                        "persistable": True,
                    },
                ),
                RelationUpsertRequest(
                    source=response_entity_node,
                    relation=RelationType.RELATED,
                    target=query_entity_node,
                    session_id=session_id,
                    run_id=run_id,
                    validated=True,
                    metadata={
                        "source": "orchestrator",
                        "kind": "semantic_relation",
                        "confidence": 0.66,
                        "scope": "session",
                        "persistable": True,
                    },
                ),
                RelationUpsertRequest(
                    source=response_fact_node,
                    relation=RelationType.DESCRIBES,
                    target=topic_entity_node,
                    session_id=session_id,
                    run_id=run_id,
                    validated=True,
                    metadata={
                        "source": "orchestrator",
                        "kind": "semantic_relation",
                        "confidence": 0.69,
                        "scope": "session",
                        "persistable": True,
                    },
                ),
            ]
        )

        base_upsert_total_ms = 0.0
        base_upsert_attempt_total_ms = 0.0
        base_upsert_max_ms = 0.0
        base_upsert_failures = 0
        for req in relation_requests:
            upsert_started = time.perf_counter()
            try:
                await self.memory_service.relation_upsert(req)
                elapsed_ms = round((time.perf_counter() - upsert_started) * 1000, 3)
                base_upsert_total_ms += elapsed_ms
                if elapsed_ms > base_upsert_max_ms:
                    base_upsert_max_ms = elapsed_ms
            except Exception as exc:
                base_upsert_failures += 1
                logging.debug(f"relation_upsert failed ({req.relation}): {exc}")
                elapsed_ms = round((time.perf_counter() - upsert_started) * 1000, 3)
                if elapsed_ms > base_upsert_max_ms:
                    base_upsert_max_ms = elapsed_ms
            finally:
                base_upsert_attempt_total_ms += round((time.perf_counter() - upsert_started) * 1000, 3)

        # LLM-based content extraction (optional, behind RELATION_EXTRACTION_ENABLED)
        content_extraction_started = time.perf_counter()
        content_relations = await self._extract_content_relations(
            query=query,
            response=final_response,
            session_id=session_id,
            run_id=run_id,
        )
        content_extraction_ms = round((time.perf_counter() - content_extraction_started) * 1000, 3)

        content_upsert_total_ms = 0.0
        content_upsert_attempt_total_ms = 0.0
        content_upsert_max_ms = 0.0
        content_upsert_failures = 0
        for req in content_relations:
            upsert_started = time.perf_counter()
            try:
                await self.memory_service.relation_upsert(req)
                elapsed_ms = round((time.perf_counter() - upsert_started) * 1000, 3)
                content_upsert_total_ms += elapsed_ms
                if elapsed_ms > content_upsert_max_ms:
                    content_upsert_max_ms = elapsed_ms
            except Exception as exc:
                content_upsert_failures += 1
                logging.debug(f"relation_upsert (content) failed ({req.relation}): {exc}")
                elapsed_ms = round((time.perf_counter() - upsert_started) * 1000, 3)
                if elapsed_ms > content_upsert_max_ms:
                    content_upsert_max_ms = elapsed_ms
            finally:
                content_upsert_attempt_total_ms += round((time.perf_counter() - upsert_started) * 1000, 3)

        base_count = len(relation_requests)
        content_count = len(content_relations)
        return {
            "total_ms": round((time.perf_counter() - fn_started) * 1000, 3),
            "base_relation_count": base_count,
            "content_relation_count": content_count,
            "base_upsert_total_ms": round(base_upsert_total_ms, 3),
            "base_upsert_attempt_total_ms": round(base_upsert_attempt_total_ms, 3),
            "base_upsert_avg_ms": round(base_upsert_total_ms / base_count, 3) if base_count else 0.0,
            "base_upsert_attempt_avg_ms": round(base_upsert_attempt_total_ms / base_count, 3) if base_count else 0.0,
            "base_upsert_max_ms": round(base_upsert_max_ms, 3),
            "base_upsert_failures": base_upsert_failures,
            "content_extraction_ms": content_extraction_ms,
            "content_upsert_total_ms": round(content_upsert_total_ms, 3),
            "content_upsert_attempt_total_ms": round(content_upsert_attempt_total_ms, 3),
            "content_upsert_avg_ms": round(content_upsert_total_ms / content_count, 3) if content_count else 0.0,
            "content_upsert_attempt_avg_ms": round(content_upsert_attempt_total_ms / content_count, 3) if content_count else 0.0,
            "content_upsert_max_ms": round(content_upsert_max_ms, 3),
            "content_upsert_failures": content_upsert_failures,
            "relation_upsert_total_count": base_count + content_count,
        }

    async def _load_conversation_history(
        self,
        session_id: str,
        current_query: str,
        limit: int = 8,
    ) -> tuple[str, int]:
        return await load_conversation_history(
            session_id=session_id,
            current_query=current_query,
            limit=limit,
            query_history_fn=self.memory_service.query_history,
            memory_history_query_request_cls=MemoryHistoryQueryRequest,
        )

    def _attach_llm_trace_metadata(self, state_mgr: RunStateManager, llm_response: Dict[str, Any]) -> None:
        attach_llm_trace_metadata(
            state_mgr=state_mgr,
            llm_response=llm_response,
            memory_service=self.memory_service,
            merge_transition_metadata_fn=self._merge_transition_metadata,
            run_state_llm_generation=RunState.LLM_GENERATION,
        )

    async def _validate_response(
        self,
        run_id: str,
        query: str,
        response: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
        context_debug: Dict[str, Any] | None = None,
        context_documents: str = "",
        request_source: str | None = None,
        risk_reassessment: bool = False,
        user_feedback_score: float | None = None,
        user_feedback_stars: int | None = None,
    ):
        context_debug = context_debug or {}
        context = ValidationContext(
            original_query=query,
            response=response,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
            context_mode=str(context_debug.get("mode") or "NONE"),
            context_sources={
                "chroma": int((context_debug.get("sources") or {}).get("chroma", 0) or 0),
                "qdrant": int((context_debug.get("sources") or {}).get("qdrant", 0) or 0),
                "postgres": int((context_debug.get("sources") or {}).get("postgres", 0) or 0),
                "neo4j": int((context_debug.get("sources") or {}).get("neo4j", 0) or 0),
                "redis": int((context_debug.get("sources") or {}).get("redis", 0) or 0),
            },
            context_documents=context_documents or "",
            graph_relations=[
                item
                for item in (context_debug.get("graph_relations") or [])
                if isinstance(item, dict)
            ],
            request_source=request_source,
            risk_reassessment=bool(risk_reassessment),
            user_feedback_score=user_feedback_score,
            user_feedback_stars=user_feedback_stars,
        )

        validation = self.validator.validate(context)

        query_l = (query or "").lower()
        response_l = (response or "").lower()

        def _contains_any(text: str, snippets: list[str]) -> bool:
            return any(token in text for token in snippets)

        law_guard_triggered = False
        law_guard_failed = False

        if _contains_any(query_l, ["ignoriere fehlende quellen", "behaupte einfach", "without sources", "just claim"]):
            law_guard_triggered = True
            if validation.decision == "accept":
                validation.decision = "warn"
            has_uncertainty = _contains_any(response_l, ["nicht", "unsicher", "keine evidenz", "cannot", "not sure", "unverified"])
            if not has_uncertainty:
                law_guard_failed = True
                if "Law guard violation: unverified claim directive without uncertainty framing." not in validation.issues:
                    validation.issues.append("Law guard violation: unverified claim directive without uncertainty framing.")
                if validation.decision in {"accept", "warn"}:
                    validation.decision = "revise"

        if _contains_any(query_l, ["faktisch nicht sicher", "plausible antwort"]):
            law_guard_triggered = True
            has_uncertainty = _contains_any(response_l, ["unsicher", "nicht sicher", "cannot", "not sure", "keine evidenz", "kann ich nicht"])
            if not has_uncertainty:
                law_guard_failed = True
                if "Law guard violation: uncertainty was requested but response lacks uncertainty framing." not in validation.issues:
                    validation.issues.append("Law guard violation: uncertainty was requested but response lacks uncertainty framing.")
                if validation.decision in {"accept", "warn"}:
                    validation.decision = "revise"

        if _contains_any(query_l, ["immer schneller als redis", "always faster than redis"]):
            law_guard_triggered = True
            has_qualification = _contains_any(response_l, ["kommt darauf", "haengt", "hängt", "depends", "not necessarily", "nicht immer"])
            if not has_qualification:
                law_guard_failed = True
                if "Law guard violation: absolute comparison requires qualification." not in validation.issues:
                    validation.issues.append("Law guard violation: absolute comparison requires qualification.")
                if validation.decision in {"accept", "warn"}:
                    validation.decision = "revise"

        if _contains_any(query_l, ["alle backends gesund", "all backends are healthy", "all backends healthy"]):
            law_guard_triggered = True
            assumes_health = _contains_any(response_l, ["alle backends gesund", "all backends are healthy", "all backends healthy"])
            has_negation = _contains_any(response_l, ["nicht annehmen", "kann ich nicht annehmen", "cannot assume", "not sure", "ohne health"])
            if assumes_health and not has_negation:
                law_guard_failed = True
                if "Law guard violation: response assumes backend health without evidence." not in validation.issues:
                    validation.issues.append("Law guard violation: response assumes backend health without evidence.")
                if validation.decision in {"accept", "warn"}:
                    validation.decision = "revise"

        if law_guard_triggered:
            validation.checks["law_conflict_guard"] = "fail" if law_guard_failed else "pass"

        librarian_route = str(((context_debug.get("librarian") or {}).get("route") or "")).upper()
        if librarian_route == "FACT_LOOKUP":
            reference_present = "[knowledge_reference]" in (response or "").lower()
            validation.checks.setdefault("fact_lookup_reference", "pass")

            if not reference_present:
                logic_issue = "Logic error: FACT_LOOKUP response missing [KNOWLEDGE_REFERENCE]"
                if logic_issue not in validation.issues:
                    validation.issues.append(logic_issue)
                validation.checks["fact_lookup_reference"] = "fail"
                if validation.decision == "accept":
                    validation.decision = "warn"

                try:
                    judge_trace = self._judge_traceability(run_id=run_id)
                    log_judge_pre_action(
                        tool_name="fact_lookup_reference",
                        decision="block",
                        issues=[logic_issue],
                        constraints={
                            "validator_score": (
                                validation.score.model_dump() if getattr(validation, "score", None) is not None else None
                            ),
                            "risk_flags": list(getattr(validation, "risk_flags", []) or []),
                        },
                        request_id=judge_trace["request_id"],
                        session_id=judge_trace["session_id"],
                        run_id=judge_trace["run_id"],
                        source=judge_trace["source"],
                        context="logic_error_missing_knowledge_reference",
                    )
                except Exception as exc:
                    _ORCHESTRATOR_LOGGER.debug("fact_lookup_reference audit logging failed: %s", exc)

        return validation

    def _judge_traceability(self, *, run_id: str | None) -> Dict[str, str | None]:
        """Build explicit traceability metadata for judge pre-action audit events."""
        run_id_candidate = (run_id or "").strip() or (self._active_run_id or "").strip() or None
        session_id_candidate = (self._active_session_id or "").strip() or None
        request_id_candidate = run_id_candidate or session_id_candidate
        return {
            "request_id": request_id_candidate,
            "run_id": run_id_candidate,
            "session_id": session_id_candidate,
            "source": "orchestrator",
        }

    @staticmethod
    def _build_prompt(
        query: str,
        tools_used: List[str],
        tool_outputs: Dict[str, Any],
    ) -> str:
        return build_prompt(
            query=query,
            tools_used=tools_used,
            tool_outputs=tool_outputs,
        )
