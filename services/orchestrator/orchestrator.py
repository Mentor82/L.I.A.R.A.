"""
Orchestrator: The kernel that coordinates all services.

Flow: Query -> Tool Selection -> Tool Execution -> LLM Generation -> Validation -> Response

This is the TOP-DOWN blueprint. Everything flows through here.
Refactored into specialized submodules for Reasoning Control, Librarian Pipeline, Tool Discovery, and Generation Pipeline.
"""

from __future__ import annotations

import json
import inspect
import atexit
import uuid
import re
import logging
import time
import os
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any, Dict, List, Optional, Tuple

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
from services.tools.registry import get_tool_registry
from services.reward_model.scorer import RewardModelScorer
from services.vision import VisionServiceClient, is_image_attachment
from services.memory_adapter import ensure_memory_service_adapter
from .run_context import RunContext, get_current_run_context, set_current_run_context, reset_current_run_context

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

# Import specialized submodules
from . import reasoning_control
from . import librarian_pipeline
from . import tool_discovery
from . import generation_pipeline

# Import defs helpers
from .defs.npu_helper import classify_npu_helper_task, should_use_npu_helper_offload
from .defs.judge import (
    create_judge_context_for_pre_action,
    create_judge_context_for_post_result,
    serialize_judge_decision,
    enrich_judge_post_payload,
)
from .defs.prompting import build_prompt
from .defs.routing import standardize_routing_telemetry
from .defs.provider_selection import select_inference_provider_for_step
from .defs.context_channels import merge_context_channels, load_conversation_history
from .defs.evidence_adapter import (
    map_context_channels_for_evidence_engine,
    map_source_counts_for_evidence_engine,
)
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
from .defs.reasoning_metrics import (
    build_validation_math_signals,
    build_runtime_audit_report,
    derive_reasoning_metric_inputs,
    apply_score_feedback_to_metric_inputs,
    compute_reasoning_metrics_snapshot_python,
)
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
_LATENCY_SCOPE_STOP_SENTINEL = "__LIARA_LAT_SCOPE_STOP__"
_JUDGE_PROFILED_ACTIONS = {
    "sys",
    "/sys",
    "compute.run",
    "compute/run",
    "compute.generate",
    "compute/generate",
}


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

    def __init__(
        self,
        memory_adapter: Optional[Any] = None,
        inference_gateway: Optional[Any] = None,
        tool_executor: Optional[Any] = None,
        judge_engine: Optional[Any] = None,
        vision_client: Optional[Any] = None,
        tool_coordinator: Optional[Any] = None,
        memory_layer: Optional[Any] = None,
        **kwargs: Any,
    ):
        mem = memory_adapter or memory_layer or kwargs.get("memory_service")
        if mem is not None:
            self.memory = ensure_memory_service_adapter(mem)
        else:
            from services.memory.store import create_default_memory_service_store
            self.memory = ensure_memory_service_adapter(create_default_memory_service_store())
        self.memory_service = self.memory

        if inference_gateway is not None:
            self.inference = ensure_inference_invoker(inference_gateway)
        else:
            from services.inference.gateway import InferenceGateway
            self.inference = ensure_inference_invoker(InferenceGateway())

        if tool_executor is not None:
            self.executor = tool_executor
        else:
            coord = tool_coordinator or kwargs.get("coordinator")
            if coord is not None:
                self.executor = ToolExecutor(coord)
            else:
                from services.tools.coordinator import ToolCoordinator
                self.executor = ToolExecutor(ToolCoordinator())

        self.judge = judge_engine or JudgeEngine()
        self.judge_engine = self.judge
        self.vision = vision_client or VisionServiceClient()
        self.input_profiler = InputSituationProfiler()
        self.librarian = LibrarianRouter()
        self.router = QueryRouter()
        self.planner = QueryPlanner()
        self.evidence_engine = EvidenceEngine()
        self.context_controller = ContextController()
        self.gap_detector = GapDetector()
        self.validator = ResponseValidator()
        self.workspace_agent = WorkspaceAgent(
            inference_invoker=self.inference,
            tool_coordinator=getattr(self.executor, "tool_coordinator", None),
            memory_service=self.memory,
        )

        self._router_scout_initialized = False
        self._reward_scorer: Optional[RewardModelScorer] = None

        self._session_adaptive_thresholds: Dict[str, Any] = {}
        self._session_adaptive_state: Dict[str, Any] = {}

        self._legacy_active_session_id: Optional[str] = None
        self._legacy_active_user_id: Optional[str] = None
        self._legacy_active_run_id: Optional[str] = None
        self._legacy_active_request_source: str = ""
        self._legacy_active_sandbox_root: str = ""
        self._legacy_simulation_mode: Optional[str] = None
        self._legacy_active_input_profile: Optional[InputSituationProfile] = None

        self.co_worker_main_provider: str = "ollama"
        self.co_worker_provider_lock_enabled: bool = bool(getattr(Settings, "CO_WORKER_PROVIDER_LOCK_ENABLED", True))
        self.default_inference_provider: str = "openvino"

        self.npu_helper_offload_enabled: bool = bool(getattr(Settings, "NPU_HELPER_OFFLOAD_ENABLED", True))
        self.npu_helper_provider: str = str(getattr(Settings, "NPU_HELPER_PROVIDER", "openvino_npu_helper"))
        self.npu_helper_max_query_chars: int = 500
        self.npu_helper_max_tools: int = 3

        self.reward_routing_enabled: bool = bool(getattr(Settings, "REWARD_ROUTING_ENABLED", True))
        self.reward_routing_block_threshold: float = float(getattr(Settings, "REWARD_ROUTING_BLOCK_THRESHOLD", 0.85))
        self.reward_routing_conf_threshold: float = float(getattr(Settings, "REWARD_ROUTING_CONFIDENCE_THRESHOLD", 0.70))
        self.reward_scorer: Optional[Any] = None

        self._session_control_state: Dict[str, Dict[str, Any]] = {}
        self._session_score_feedback: Dict[str, Dict[str, Any]] = {}
        self._session_score_history: Dict[str, List[Dict[str, Any]]] = {}
        self._wsl_session_by_chat_session: Dict[str, str] = {}

        self._last_route_debug: Dict[str, Any] = {}
        self._last_executor_debug: Dict[str, Any] = {}
        self.timing_debug_enabled = True

    @property
    def judge(self) -> Any:
        return self.judge_engine

    @judge.setter
    def judge(self, val: Any) -> None:
        self.judge_engine = val

    @property
    def memory(self) -> Any:
        return self.memory_service

    @memory.setter
    def memory(self, val: Any) -> None:
        self.memory_service = val

    @property
    def _active_session_id(self) -> Optional[str]:
        ctx = get_current_run_context()
        return ctx.session_id if ctx else self._legacy_active_session_id

    @_active_session_id.setter
    def _active_session_id(self, val: Optional[str]) -> None:
        self._legacy_active_session_id = val

    @property
    def _active_user_id(self) -> Optional[str]:
        ctx = get_current_run_context()
        return ctx.user_id if ctx else self._legacy_active_user_id

    @_active_user_id.setter
    def _active_user_id(self, val: Optional[str]) -> None:
        self._legacy_active_user_id = val

    @property
    def _active_run_id(self) -> Optional[str]:
        ctx = get_current_run_context()
        return ctx.run_id if ctx else self._legacy_active_run_id

    @_active_run_id.setter
    def _active_run_id(self, val: Optional[str]) -> None:
        self._legacy_active_run_id = val

    @property
    def _active_request_source(self) -> str:
        ctx = get_current_run_context()
        return ctx.request_source if ctx else self._legacy_active_request_source

    @_active_request_source.setter
    def _active_request_source(self, val: str) -> None:
        self._legacy_active_request_source = val

    @property
    def _active_sandbox_root(self) -> str:
        ctx = get_current_run_context()
        return ctx.sandbox_root if ctx else self._legacy_active_sandbox_root

    @_active_sandbox_root.setter
    def _active_sandbox_root(self, val: str) -> None:
        self._legacy_active_sandbox_root = val

    @property
    def _simulation_mode(self) -> Optional[str]:
        ctx = get_current_run_context()
        return ctx.simulation_mode if ctx else self._legacy_simulation_mode

    @_simulation_mode.setter
    def _simulation_mode(self, val: Optional[str]) -> None:
        self._legacy_simulation_mode = val

    @property
    def _active_input_profile(self) -> Optional[InputSituationProfile]:
        ctx = get_current_run_context()
        return ctx.input_profile if ctx else self._legacy_active_input_profile

    @_active_input_profile.setter
    def _active_input_profile(self, val: Optional[InputSituationProfile]) -> None:
        self._legacy_active_input_profile = val
        self._latency_scope_stop_event = Event()
        self._latency_scope_thread: Optional[Thread] = None

    # ----------------------------------------------------
    # Core Entry Point: run()
    # ----------------------------------------------------
    async def run(self, request: OrchestratorRequest) -> OrchestratorResponse:
        """Execute the full query pipeline."""
        run_id = request.run_id or str(uuid.uuid4())
        run_src = (request.request_source or "").strip().lower()
        sandbox_root = request.sandbox_root or ""
        run_ctx = RunContext(
            session_id=request.session_id,
            user_id=request.user_id,
            run_id=run_id,
            request_source=run_src,
            sandbox_root=sandbox_root,
            simulation_mode=request.simulation_mode,
        )
        ctx_token = set_current_run_context(run_ctx)
        try:
            return await self._execute_run_pipeline(request, run_id=run_id, run_src=run_src, sandbox_root=sandbox_root)
        finally:
            reset_current_run_context(ctx_token)

    async def _execute_run_pipeline(
        self,
        request: OrchestratorRequest,
        *,
        run_id: str,
        run_src: str,
        sandbox_root: str,
    ) -> OrchestratorResponse:
        routing_query = (request.routing_query or request.query or "").strip() or request.query
        run_started = time.perf_counter()

        self._active_session_id = request.session_id
        self._active_user_id = request.user_id
        self._active_run_id = run_id
        self._active_request_source = run_src

        if not self._router_scout_initialized:
            await self.router.initialize_scout_embedding()
            await self.input_profiler.initialize()
            self._router_scout_initialized = True

        execution_trace: List[Dict[str, Any]] = [
            {"to": "routing", "to_state": "routing", "reason": "Request initialized"},
            {"to": "llm_generation", "to_state": "llm_generation", "reason": "Initial generation"},
        ]

        self._active_sandbox_root = sandbox_root
        self._simulation_mode = request.simulation_mode

        # 1. Profile history & Input Situation Profile
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
        run_ctx_full = RunContext(
            session_id=request.session_id,
            user_id=request.user_id,
            run_id=run_id,
            request_source=run_src,
            sandbox_root=sandbox_root,
            simulation_mode=request.simulation_mode,
            input_profile=input_profile,
        )
        set_current_run_context(run_ctx_full)
        self._active_input_profile = input_profile

        # 2. Librarian Decision & Context Loading
        librarian_decision = self.librarian.route(query=routing_query, input_profile=input_profile)
        lib_res = await librarian_pipeline.load_librarian_context(
            self,
            session_id=request.session_id,
            user_id=request.user_id,
            query=routing_query,
            librarian_decision=librarian_decision,
        )
        context_channels, source_counts = lib_res if isinstance(lib_res, tuple) else (lib_res, {})

        # 3. Router & Planner Decision
        router_req = RouterRequest(query=routing_query, session_id=request.session_id)
        router_decision = await self.router.route(router_req)

        # 3. Tool Selection
        selected_tools = await self._select_tools(
            routing_query,
            request.tools_override,
        )

        # 4. Tool Execution
        tool_results = await self._execute_tools(
            selected_tools,
            routing_query,
            run_id=run_id,
        )

        # 5. LLM Generation
        formatted_context = merge_context_channels(context_channels)
        gen_res = await self._generate_llm_response(
            run_id,
            request.query,
            routing_query,
            request.session_id,
            selected_tools,
            tool_results,
            request.max_tokens or 2048,
            request.preferred_provider,
            request.preferred_model,
            False,
            None,
            0,
            None,
            formatted_context,
        )
        if isinstance(gen_res, tuple):
            response_text, gen_meta = gen_res
        elif isinstance(gen_res, dict):
            response_text = str(gen_res.get("content") or gen_res.get("response") or "")
            gen_meta = gen_res
        else:
            response_text = str(gen_res or "")
            gen_meta = {}

        for trace_entry in execution_trace:
            if trace_entry.get("to_state") == "llm_generation" or trace_entry.get("to") == "llm_generation":
                trace_entry.setdefault("to", "llm_generation")
                trace_entry["metadata"] = gen_meta if isinstance(gen_meta, dict) else {}

        # 5b. Evidence-State Aggregation (Issue #8) -- classifies tool
        # outputs (search miss, connector failure, unresolved shape, etc.)
        # into EvidenceState before validation sees the response text, so a
        # weak/negative evidence state can be checked against any stronger
        # claim the response makes.
        evidence_tool_outputs = (
            {r.get("tool_name", f"tool_{i}"): r for i, r in enumerate(tool_results or []) if isinstance(r, dict)}
            if isinstance(tool_results, list)
            else (tool_results or {})
        )
        evidence_result = self.evidence_engine.analyze(
            query=routing_query,
            context_channels=map_context_channels_for_evidence_engine(context_channels),
            source_counts=map_source_counts_for_evidence_engine(source_counts),
            tool_outputs=evidence_tool_outputs,
            conversation_history=profile_history,
        )

        # 6. Response Validation & Reasoning Snapshots
        val_res = await self._validate_response(
            query=routing_query,
            response_text=response_text,
            tool_results=tool_results,
            input_profile=input_profile,
            evidence_states=evidence_result.evidence_states,
        )

        if hasattr(val_res, "dict") and callable(val_res.dict):
            val_res = val_res.dict()
        elif hasattr(val_res, "__dict__"):
            val_res = dict(vars(val_res))
        elif not isinstance(val_res, dict):
            val_res = dict(val_res) if hasattr(val_res, "__iter__") else {"decision": str(val_res)}

        score_payload = self._extract_validation_score_payload(val_res)
        judge_post_payload = {}
        if hasattr(self, "judge_engine") and hasattr(self.judge_engine, "evaluate_post_result"):
            try:
                from dataclasses import is_dataclass, asdict
                from .defs.judge import create_judge_context_for_post_result
                j_ctx = create_judge_context_for_post_result(
                    self,
                    run_id=run_id,
                    query=routing_query,
                    response_content=response_text,
                    tools_used=selected_tools,
                    tool_outputs=tool_results,
                    evidence_states=evidence_result.evidence_states,
                )
                j_dec = self.judge_engine.evaluate_post_result(j_ctx)
                if j_dec:
                    if is_dataclass(j_dec):
                        judge_post_payload = asdict(j_dec)
                    elif hasattr(j_dec, "dict"):
                        judge_post_payload = j_dec.dict()
                    elif hasattr(j_dec, "model_dump"):
                        judge_post_payload = j_dec.model_dump()
                    elif isinstance(j_dec, dict):
                        judge_post_payload = dict(j_dec)

                    dec_val = judge_post_payload.get("decision")
                    if hasattr(dec_val, "value"):
                        judge_post_payload["decision"] = dec_val.value
            except Exception as exc:
                _ORCHESTRATOR_LOGGER.warning("Judge post evaluation failed (visibly degraded): %s", exc)
                if isinstance(val_res, dict):
                    val_res["status"] = "degraded"
                    val_res["judge_error"] = str(exc)
                judge_post_payload = {
                    "approved": False,
                    "status": "degraded",
                    "decision": "degraded",
                    "error": str(exc),
                }

        retry_count = 0
        retry_limit = 3
        gap_history: List[str] = []
        last_gap_decision: Dict[str, Any] = {}

        while str((val_res.get("decision") if isinstance(val_res, dict) else getattr(val_res, "decision", "accept")) or "accept") in {"block", "revise"}:
            retry_control = self._build_retry_control(
                validation_decision=str((val_res.get("decision") if isinstance(val_res, dict) else getattr(val_res, "decision", "accept")) or "accept"),
                judge_post=judge_post_payload,
                retry_count=retry_count,
                retry_limit=retry_limit,
                compression_meta=dict(gen_meta.get("compression", {}) if isinstance(gen_meta, dict) else {}),
            )
            if not retry_control.get("attempt_allowed", False):
                break

            retry_count += 1
            trigger_decision = str((val_res.get("decision") if isinstance(val_res, dict) else getattr(val_res, "decision", "accept")) or "accept")
            from .gap_detector import GapDetector
            gap_decision = GapDetector.detect(
                query=routing_query,
                validation_issues=list((val_res.get("issues") if isinstance(val_res, dict) else getattr(val_res, "issues", [])) or []),
                context_sources={},
                reasoning_step=retry_count + 1,
                previous_gap_types=gap_history,
            )
            last_gap_decision = gap_decision.to_dict() if hasattr(gap_decision, "to_dict") else dict(gap_decision)

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

            gap_history.append(getattr(gap_decision, "gap_type", "NONE"))
            execution_trace.append({
                "to_state": "llm_generation",
                "reason": f"Retry generation attempt {retry_count}",
                "metadata": {"retry_attempt": retry_count},
            })
            response_text, gen_meta = await generation_pipeline.generate_llm_response(
                self,
                query=routing_query,
                context_text=formatted_context,
                input_profile=input_profile,
                librarian_decision=librarian_decision,
                tool_results=tool_results,
                session_id=request.session_id,
                run_id=run_id,
                preferred_provider=request.preferred_provider,
                force_context=True,
                retry_attempt=retry_count,
            )
            val_res = generation_pipeline.validate_response(
                self,
                query=routing_query,
                response_text=response_text,
                tool_results=tool_results,
                input_profile=input_profile,
            )

        reasoning_snapshot = reasoning_control.compute_reasoning_metrics_snapshot(
            self,
            inputs={
                "query": routing_query,
                "response": response_text,
                "validation": val_res,
                "score_payload": score_payload,
                "judge_post": judge_post_payload,
                "input_profile": input_profile,
            },
            session_id=request.session_id,
        )

        total_ms = (time.perf_counter() - run_started) * 1000.0

        snapshot_dict = (
            reasoning_snapshot.dict()
            if hasattr(reasoning_snapshot, "dict")
            else (reasoning_snapshot.model_dump() if hasattr(reasoning_snapshot, "model_dump") else dict(reasoning_snapshot or {}))
        )

        validation_math_signals = self._build_validation_math_signals(snapshot_dict)
        control_before = self._read_control_mode_before(request.session_id or "")
        control_after = str(
            validation_math_signals.get("resolved_mode")
            or validation_math_signals.get("control_mode")
            or "advisory"
        )
        decision_delta = self._build_decision_delta(control_before=control_before, control_after=control_after)
        validation_math_signals["control_mode_before"] = control_before
        validation_math_signals["control_mode_after"] = control_after
        validation_math_signals["decision_delta"] = decision_delta

        score_payload = self._extract_validation_score_payload(val_res)
        if judge_post_payload:
            from .defs.judge import enrich_judge_post_payload
            judge_post_payload = enrich_judge_post_payload(judge_post_payload, validation_math_signals)

        decision_context = self._build_decision_context(
            validation=val_res,
            score_payload=score_payload,
            math_signals=validation_math_signals,
            judge_post=judge_post_payload,
        )

        decision_explanation = self._build_decision_explanation(
            validation_decision=str((val_res.get("decision") if isinstance(val_res, dict) else getattr(val_res, "decision", "accept")) or "accept"),
            score_payload=score_payload,
            math_signals=validation_math_signals,
            judge_post=judge_post_payload,
        )

        retry_control = self._build_retry_control(
            validation_decision=str((val_res.get("decision") if isinstance(val_res, dict) else getattr(val_res, "decision", "accept")) or "accept"),
            judge_post=judge_post_payload,
            retry_count=retry_count,
            retry_limit=3,
            compression_meta={},
            gap_decision=last_gap_decision,
            math_signals=validation_math_signals,
        )

        if isinstance(gen_meta, dict):
            gen_meta["retry"] = {"count": retry_count}

        if hasattr(val_res, "dict") and callable(val_res.dict):
            val_res = val_res.dict()
        elif hasattr(val_res, "__dict__"):
            val_res = dict(vars(val_res))
        elif not isinstance(val_res, dict):
            val_res = dict(val_res) if hasattr(val_res, "__iter__") else {"decision": str(val_res)}

        val_res["math_signals"] = validation_math_signals
        val_res["decision_context"] = decision_context
        val_res["decision_explanation"] = decision_explanation
        val_res["retry_control"] = retry_control
        val_res["retry_count"] = retry_count

        # Record feedback for turn-to-turn feedback loop
        def _get_val_attr(obj: Any, *keys: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                for k in keys:
                    if k in obj and obj[k] is not None:
                        return obj[k]
            for k in keys:
                if hasattr(obj, k) and getattr(obj, k) is not None:
                    return getattr(obj, k)
            return default

        feedback_entry = {
            "run_id": run_id,
            "decision": str(_get_val_attr(val_res, "decision", default="accept")),
            "confidence_score": float(_get_val_attr(val_res, "confidence_score", "confidence", "score", default=0.0) or 0.0),
            "score": score_payload,
            "risk_flags": list(_get_val_attr(val_res, "risk_flags", "issues", default=[]) or []),
            "actionable_risk": float(validation_math_signals.get("actionable_risk", 0.0) or 0.0),
            "utility_ig": float(validation_math_signals.get("utility_ig", 0.0) or 0.0),
            "stability_score": float(validation_math_signals.get("stability_score", 1.0) or 1.0),
        }
        if request.session_id:
            self._session_score_feedback[request.session_id] = feedback_entry
            prev_hist = list(self._session_score_history.get(request.session_id) or [])
            self._session_score_history[request.session_id] = [*prev_hist, feedback_entry][-5:]
            self._session_control_state[request.session_id] = {
                "control_mode": control_after,
                "last_run_id": run_id,
            }

        # 7. Memory Commit
        # Issue #8: gated on both the validation decision AND the absence of
        # evidence-integrity risk flags -- decision alone is too coarse,
        # since unsupported_state_promotion/hypothesis_promoted_to_fact only
        # apply a confidence penalty and can still land on "warn". A "warn"
        # response carrying one of these flags must not be embedded into
        # session memory, where it could resurface as apparent "remembered
        # fact" in a later turn -- exactly the failure mode this issue
        # warns about.
        _EVIDENCE_INTEGRITY_RISK_FLAGS = {
            "negative_existence_without_evidence",
            "unsupported_state_promotion",
            "connector_unknown_collapsed_to_false",
            "hypothesis_promoted_to_fact",
            "recursive_unsupported_claim_dependency",
        }
        memory_commit_decision = str(_get_val_attr(val_res, "decision", default="accept") or "accept")
        memory_commit_risk_flags = set(_get_val_attr(val_res, "risk_flags", default=[]) or [])
        if (
            request.session_id
            and request.user_id
            and memory_commit_decision in {"accept", "warn"}
            and not (memory_commit_risk_flags & _EVIDENCE_INTEGRITY_RISK_FLAGS)
        ):
            await librarian_pipeline.upsert_memory_commit_embedding(
                self,
                session_id=request.session_id,
                user_id=request.user_id,
                run_id=run_id,
                content=response_text,
            )

        total_ms = (time.perf_counter() - run_started) * 1000.0

        tool_results_dict = (
            {r.get("tool_name", f"tool_{i}"): r for i, r in enumerate(tool_results or []) if isinstance(r, dict)}
            if isinstance(tool_results, list)
            else (tool_results or {})
        )

        val_res_dict = (
            val_res.model_dump() if hasattr(val_res, "model_dump")
            else (val_res.dict() if hasattr(val_res, "dict") and callable(val_res.dict)
            else (dict(vars(val_res)) if hasattr(val_res, "__dict__")
            else (val_res if isinstance(val_res, dict) else {})))
        )

        resp = OrchestratorResponse(
            run_id=run_id,
            session_id=request.session_id,
            final_response=response_text,
            tools_executed=selected_tools,
            tool_results=tool_results_dict,
            state_final=RunState.COMPLETE.value,
            llm_generation=gen_meta,
            validation_result=val_res_dict,
            reasoning_snapshot=reasoning_snapshot,
            total_duration_ms=total_ms,
            execution_trace=execution_trace,
            metadata={
                "input_profile": input_profile.dict() if hasattr(input_profile, "dict") else dict(input_profile),
                "router_decision": router_decision.dict() if hasattr(router_decision, "dict") else dict(router_decision),
                "generation_metadata": gen_meta,
            },
        )
        return resp

    # ----------------------------------------------------
    # Delegating Helper Methods (Static & Instance)
    # ----------------------------------------------------

    # Reasoning Control Delegation
    def _resolve_reasoning_threshold_profile(self, session_id: Optional[str]) -> Dict[str, Any]:
        return reasoning_control.resolve_reasoning_threshold_profile(self, session_id)

    def _maybe_apply_runtime_threshold_adaptation(self, **kwargs: Any) -> Dict[str, Any]:
        session_id = kwargs.get("session_id") or ""
        report = kwargs.get("runtime_audit_report") or {}
        profile = kwargs.get("threshold_profile") or {}
        feedback = kwargs.get("feedback_entry")
        return reasoning_control.maybe_apply_runtime_threshold_adaptation(
            self,
            session_id=session_id,
            runtime_audit_report=report,
            threshold_profile=profile,
            feedback_entry=feedback,
        )

    @staticmethod
    def _compute_belief_snapshot(
        metric_inputs: Dict[str, Any] = None,
        math_signals: Dict[str, Any] = None,
        belief_params: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return reasoning_control.compute_belief_snapshot(
            None,
            metric_inputs=metric_inputs or kwargs.get("metric_inputs") or {},
            math_signals=math_signals or kwargs.get("math_signals") or {},
            belief_params=belief_params or kwargs.get("belief_params") or {},
        )

    @staticmethod
    def _compute_utility_snapshot(
        metric_inputs: Dict[str, Any] = None,
        belief_snapshot: Dict[str, Any] = None,
        utility_params: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return reasoning_control.compute_utility_snapshot(
            None,
            metric_inputs=metric_inputs or kwargs.get("metric_inputs") or {},
            belief_snapshot=belief_snapshot or kwargs.get("belief_snapshot") or {},
            utility_params=utility_params or kwargs.get("utility_params") or {},
        )

    @staticmethod
    def _compute_structure_stability_snapshot(
        metric_inputs: Dict[str, Any] = None,
        math_signals: Dict[str, Any] = None,
        stability_params: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return reasoning_control.compute_structure_stability_snapshot(
            None,
            metric_inputs=metric_inputs or kwargs.get("metric_inputs") or {},
            math_signals=math_signals or kwargs.get("math_signals") or {},
            stability_params=stability_params or kwargs.get("stability_params") or {},
        )

    @staticmethod
    def _compute_decision_snapshot(
        belief_snapshot: Dict[str, Any] = None,
        utility_snapshot: Dict[str, Any] = None,
        stability_snapshot: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return reasoning_control.compute_decision_snapshot(
            None,
            belief_snapshot=belief_snapshot or kwargs.get("belief_snapshot") or {},
            utility_snapshot=utility_snapshot or kwargs.get("utility_snapshot") or {},
            stability_snapshot=stability_snapshot or kwargs.get("stability_snapshot") or {},
        )

    @staticmethod
    def _compute_reasoning_metrics_snapshot_julia(
        metric_inputs: Dict[str, Any] = None,
        math_signals: Dict[str, Any] = None,
        julia_params: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        return reasoning_control.compute_reasoning_metrics_snapshot_julia(
            None,
            metric_inputs=metric_inputs or kwargs.get("metric_inputs") or {},
            math_signals=math_signals or kwargs.get("math_signals") or {},
            julia_params=julia_params or kwargs.get("julia_params") or {},
        )

    @staticmethod
    def _compute_reasoning_metrics_snapshot_python(
        metric_inputs: Dict[str, Any] = None,
        math_signals: Dict[str, Any] = None,
        threshold_profile: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return compute_reasoning_metrics_snapshot_python(
            metric_inputs=metric_inputs or kwargs.get("metric_inputs") or {},
            math_signals=math_signals or kwargs.get("math_signals") or {},
            threshold_profile=threshold_profile or kwargs.get("threshold_profile") or {},
        )

    def _compute_reasoning_metrics_snapshot(self, **kwargs: Any) -> ReasoningMetricsSnapshot:
        return reasoning_control.compute_reasoning_metrics_snapshot(self, **kwargs)

    @staticmethod
    def _derive_reasoning_metric_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
        return derive_reasoning_metric_inputs(inputs)

    @staticmethod
    def _apply_score_feedback_to_metric_inputs(
        inputs: Dict[str, Any] = None,
        previous_score_feedback: Dict[str, Any] = None,
        previous_score_history: Optional[List[Dict[str, Any]]] = None,
        metric_inputs: Dict[str, Any] = None,
        **kwargs: Any,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        inp = inputs if inputs is not None else (metric_inputs if metric_inputs is not None else kwargs.get("inputs", {}))
        fb = previous_score_feedback if previous_score_feedback is not None else kwargs.get("previous_score_feedback", {})
        hist = previous_score_history if previous_score_history is not None else kwargs.get("previous_score_history")
        return apply_score_feedback_to_metric_inputs(
            inp,
            fb,
            hist,
            weak_score_escalation_count=kwargs.get("weak_score_escalation_count", 2),
            score_feedback_canary_soft_only=kwargs.get("score_feedback_canary_soft_only", False),
        )

    @staticmethod
    def _build_validation_math_signals(inputs: Dict[str, Any]) -> Dict[str, Any]:
        return build_validation_math_signals(inputs)

    @staticmethod
    def _build_runtime_audit_report(**kwargs: Any) -> Dict[str, Any]:
        return build_runtime_audit_report(**kwargs)

    def _read_control_mode_before(self_or_state: Any, session_id: Optional[str] = None, **kwargs: Any) -> str:
        if isinstance(self_or_state, Orchestrator):
            sid = session_id or kwargs.get("session_id") or ""
            state = kwargs.get("session_control_state") or self_or_state._session_control_state
            return read_control_mode_before(state, sid)
        if isinstance(self_or_state, dict):
            return read_control_mode_before(self_or_state, session_id or kwargs.get("session_id", ""))
        return "advisory"

    @staticmethod
    def _build_decision_delta(**kwargs: Any) -> Dict[str, Any]:
        return build_decision_delta(**kwargs)

    @staticmethod
    def _build_retry_control(**kwargs: Any) -> Dict[str, Any]:
        return build_retry_control(**kwargs)

    @staticmethod
    def _build_decision_context(**kwargs: Any) -> Dict[str, Any]:
        return build_decision_context(**kwargs)

    @staticmethod
    def _build_decision_explanation(**kwargs: Any) -> Dict[str, Any]:
        return build_decision_explanation(**kwargs)

    @staticmethod
    def _build_hybrid_control_metadata(**kwargs: Any) -> Dict[str, Any]:
        return build_hybrid_control_metadata(**kwargs)

    # Librarian Pipeline Delegation
    def _graph_priority_guardrail_line(self, session_id: Optional[str]) -> Optional[str]:
        return librarian_pipeline.graph_priority_guardrail_line(self, session_id)

    @staticmethod
    def _retrieval_rerank(query: str = "", candidates: List[Dict[str, Any]] = None, top_k: int = 5, **kwargs: Any) -> List[Dict[str, Any]]:
        return librarian_pipeline.retrieval_rerank(
            None,
            query=query or kwargs.get("query", ""),
            candidates=candidates or kwargs.get("candidates") or [],
            top_k=top_k or kwargs.get("top_k", 5),
        )

    def _should_run_recall_refresh(self, **kwargs: Any) -> bool:
        return librarian_pipeline.should_run_recall_refresh(self, **kwargs)

    async def _load_librarian_context(self, **kwargs: Any) -> Tuple[Dict[str, Any], Dict[str, int]]:
        return await librarian_pipeline.load_librarian_context(self, **kwargs)

    @staticmethod
    def _extract_graph_relations_from_context(relation_context: str = "") -> List[Dict[str, str]]:
        return librarian_pipeline.extract_graph_relations_from_context(relation_context)

    async def _upsert_memory_commit_embedding(self, **kwargs: Any) -> bool:
        return await librarian_pipeline.upsert_memory_commit_embedding(self, **kwargs)

    def _extract_content_relations(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return librarian_pipeline.extract_content_relations(self, **kwargs)

    async def _upsert_validated_relations(self, **kwargs: Any) -> int:
        return await librarian_pipeline.upsert_validated_relations(self, **kwargs)

    async def _load_conversation_history(
        self,
        session_id: Optional[str] = None,
        current_query: str = "",
        limit: int = 4,
        **kwargs: Any,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        sid = session_id or kwargs.get("session_id")
        if not sid:
            return "", []
        try:
            res = await load_conversation_history(
                session_id=sid,
                current_query=current_query or kwargs.get("current_query", ""),
                limit=limit or kwargs.get("limit", 4),
                query_history_fn=self.memory.query_history,
                memory_history_query_request_cls=MemoryHistoryQueryRequest,
            )
            if isinstance(res, tuple):
                return str(res[0] or ""), []
            return str(res or ""), []
        except Exception as exc:
            _ORCHESTRATOR_LOGGER.warning("load_conversation_history failed: %s", exc)
            return "", []

    @staticmethod
    def _infer_active_topic(text: str) -> Optional[str]:
        return infer_active_topic(text)

    @staticmethod
    def _summarize_history_for_embedding(history: List[Dict[str, Any]]) -> str:
        return summarize_history_for_embedding(history)

    @staticmethod
    def _compact_embedding_text(text: str) -> str:
        return compact_embedding_text(text)

    @staticmethod
    def _build_embedding_query(**kwargs: Any) -> str:
        return build_embedding_query(**kwargs)

    @staticmethod
    def _rewrite_retrieval_query(query: str) -> str:
        return rewrite_retrieval_query(query)

    async def _upsert_temp_context_note(self, **kwargs: Any) -> Any:
        return await upsert_temp_context_note(
            get_fn=self.memory.get,
            set_fn=self.memory.set,
            session_tier=MemoryTier.SESSION,
            temp_context_ttl_seconds=3600,
            build_context_upsert_metadata_fn=self._build_context_upsert_metadata,
            **kwargs,
        )

    async def _upsert_working_context_doc(self, **kwargs: Any) -> Any:
        from services.contracts import ContextUpsertRequest, ContextScope
        return await upsert_working_context_doc(
            is_safe_for_context_upsert_fn=is_safe_for_context_upsert,
            touch_working_context_activity_fn=self._touch_working_context_activity,
            build_context_upsert_metadata_fn=self._build_context_upsert_metadata,
            context_upsert_fn=self.memory.upsert_retrieval,
            context_upsert_request_cls=ContextUpsertRequest,
            context_scope_cls=ContextScope,
            **kwargs,
        )

    async def _touch_working_context_activity(self, **kwargs: Any) -> Any:
        return await touch_working_context_activity(
            set_fn=self.memory.set,
            session_tier=MemoryTier.SESSION,
            ttl_seconds=300,
            **kwargs,
        )

    @staticmethod
    def _build_context_upsert_metadata(**kwargs: Any) -> Dict[str, Any]:
        return build_context_upsert_metadata(detect_language_fn=_detect_language, **kwargs)

    @staticmethod
    def _format_tool_context(tool_results: List[Dict[str, Any]]) -> str:
        return format_tool_context(tool_results)

    @staticmethod
    def _build_working_context_summary(**kwargs: Any) -> str:
        return build_working_context_summary(**kwargs)

    @staticmethod
    def _is_safe_for_context_upsert(text: str) -> bool:
        return is_safe_for_context_upsert(text)

    @staticmethod
    def _relation_node_key(prefix: str, text: str) -> str:
        return relation_node_key(prefix, text)

    # Tool Discovery Delegation
    async def _select_tools(self, query: str = "", tools_override: Optional[List[str]] = None, **kwargs: Any) -> List[str]:
        q = query or kwargs.get("query", "")
        profile = kwargs.get("input_profile") or getattr(self, "_active_input_profile", None)
        res = tool_discovery.select_tools(self, input_profile=profile, query=q, tools_override=tools_override)
        if hasattr(res, "__await__"):
            return await res
        return res

    def _init_reward_scorer(self) -> Optional[RewardModelScorer]:
        return tool_discovery.init_reward_scorer(self)

    def _evaluate_reward_routing(self, **kwargs: Any) -> Dict[str, float]:
        return tool_discovery.evaluate_reward_routing(self, **kwargs)

    async def _execute_tools(self, selected_tools: List[str] = None, query: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        tools = selected_tools if selected_tools is not None else kwargs.get("selected_tools", [])
        q = query or kwargs.get("query", "")
        return await tool_discovery.execute_tools(self, selected_tools=tools, query=q, **kwargs)

    def _rank_discovery_candidate(self, **kwargs: Any) -> float:
        return tool_discovery.rank_discovery_candidate(self, **kwargs)

    def _complete_web_discovery(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return tool_discovery.complete_web_discovery(self, **kwargs)

    def _build_external_write_content(self, **kwargs: Any) -> Dict[str, Any]:
        return tool_discovery.build_external_write_content(self, **kwargs)

    def _plan_external_tool_call(self, **kwargs: Any) -> Optional[ExternalToolCall]:
        return tool_discovery.plan_external_tool_call(self, **kwargs)

    def _plan_external_tool_followup(self, **kwargs: Any) -> Optional[ExternalToolCall]:
        return tool_discovery.plan_external_tool_followup(self, **kwargs)

    @staticmethod
    def _normalize_external_tool(name: str) -> str:
        return normalize_external_tool(name)

    @staticmethod
    def _extract_textual_tool_schema(text: str) -> Optional[Dict[str, Any]]:
        return extract_textual_tool_schema(text)

    @staticmethod
    def _extract_path_candidate(text: str) -> Optional[str]:
        return extract_path_candidate(text)

    @staticmethod
    def _extract_path_candidates(text: str) -> List[str]:
        return extract_path_candidates(text)

    @staticmethod
    def _extract_requested_end_line(text: str) -> Optional[int]:
        return extract_requested_end_line(text)

    @staticmethod
    def _extract_explicit_content(text: str) -> Optional[str]:
        return extract_explicit_content(text)

    @staticmethod
    def _infer_external_tool_arguments(**kwargs: Any) -> Dict[str, Any]:
        return infer_external_tool_arguments(**kwargs)

    # Generation Pipeline Delegation
    def _apply_empty_response_fallback(self, **kwargs: Any) -> str:
        return generation_pipeline.apply_empty_response_fallback(self, **kwargs)

    async def _generate_llm_response(self, *args: Any, **kwargs: Any) -> Any:
        return await generation_pipeline.generate_llm_response(self, *args, **kwargs)

    async def _validate_response(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return generation_pipeline.validate_response(self, *args, **kwargs)

    def _judge_traceability(self, **kwargs: Any) -> Dict[str, Any]:
        return generation_pipeline.judge_traceability(self, **kwargs)

    @staticmethod
    def _build_prompt(**kwargs: Any) -> str:
        return build_prompt(**kwargs)

    def _select_inference_provider_for_step(self, **kwargs: Any) -> Tuple[str, Dict[str, Any]]:
        return select_inference_provider_for_step(self, **kwargs)

    def _classify_npu_helper_task(
        self,
        query: str = "",
        tools_used: List[str] = None,
        tool_outputs: Dict[str, Any] = None,
        force_context: bool = False,
        retry_attempt: int = 0,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        return classify_npu_helper_task(
            self,
            query=query or kwargs.get("query", ""),
            tools_used=tools_used if tools_used is not None else kwargs.get("tools_used", []),
            tool_outputs=tool_outputs if tool_outputs is not None else kwargs.get("tool_outputs", {}),
            force_context=force_context if force_context is not None else kwargs.get("force_context", False),
            retry_attempt=retry_attempt if retry_attempt is not None else kwargs.get("retry_attempt", 0),
        )

    def _should_use_npu_helper_offload(self, **kwargs: Any) -> bool:
        return should_use_npu_helper_offload(self, **kwargs)

    def _create_judge_context_for_pre_action(self, *args: Any, **kwargs: Any) -> JudgeContext:
        return create_judge_context_for_pre_action(self, *args, **kwargs)

    def _create_judge_context_for_post_result(self, *args: Any, **kwargs: Any) -> JudgeContext:
        return create_judge_context_for_post_result(self, *args, **kwargs)

    @staticmethod
    def _serialize_judge_decision(decision: Any) -> Dict[str, Any]:
        return serialize_judge_decision(decision)

    @staticmethod
    def _enrich_judge_post_payload(**kwargs: Any) -> Dict[str, Any]:
        return enrich_judge_post_payload(**kwargs)

    @staticmethod
    def _standardize_routing_telemetry(**kwargs: Any) -> Dict[str, Any]:
        return standardize_routing_telemetry(**kwargs)

    @staticmethod
    def _extract_artifacts_from_tool_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return extract_artifacts_from_tool_results(results)

    @staticmethod
    def _merge_transition_metadata(**kwargs: Any) -> Dict[str, Any]:
        return merge_transition_metadata(**kwargs)

    @staticmethod
    def _attach_llm_trace_metadata(**kwargs: Any) -> None:
        attach_llm_trace_metadata(**kwargs)

    @staticmethod
    def _extract_validation_score_payload(*args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        val = args[0] if args else kwargs.get("validation")
        return extract_validation_score_payload(val)

    @staticmethod
    def _ground_workspace_agent_response(*args: Any, **kwargs: Any) -> str:
        return generation_pipeline._ground_workspace_agent_response(*args, **kwargs)

    @staticmethod
    def _observe_images(**kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def _append_latency_scope_sample(self, **kwargs: Any) -> None:
        pass

    def _start_latency_scope_writer(self) -> None:
        pass

    def _stop_latency_scope_writer(self) -> None:
        pass
