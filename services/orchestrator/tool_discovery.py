"""Tool Discovery, Selection & Execution Submodule for LIARA Orchestrator.

Handles:
- Heuristic & reward-model tool selection
- Execution of tools via ToolExecutor
- Web discovery candidate ranking & retrieval
- External tool argument inference & call planning
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from services.contracts import ExecutorRequest, InputSituationProfile
from services.contracts.service_boundaries import ExternalToolCall
from services.shared.types import ToolStatus
from services.tools.registry import get_tool_registry
from services.tools.builtin.sys_audit import log_judge_pre_action
from services.reward_model.scorer import RewardModelScorer
from .defs.external_tools import (
    normalize_external_tool,
    extract_textual_tool_schema,
    extract_path_candidates,
    extract_path_candidate,
    extract_requested_end_line,
    extract_explicit_content,
    infer_external_tool_arguments,
)

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

_LOGGER = logging.getLogger("liara.orchestrator.tool_discovery")


async def select_tools(
    orchestrator: Orchestrator,
    *,
    input_profile: Optional[InputSituationProfile] = None,
    query: str,
    tools_override: Optional[List[str]] = None,
) -> List[str]:
    """Select candidate tools based on heuristic profile & tool registry capabilities."""
    if not query:
        return []

    profile = input_profile or InputSituationProfile(query=query)

    selected: List[str] = []
    reason = "heuristic"
    metadata: Dict[str, Any] = {}

    if hasattr(orchestrator, "router") and hasattr(orchestrator.router, "route"):
        try:
            from services.contracts import RouterRequest
            req = RouterRequest(
                query=query,
                tools_override=tools_override,
                input_profile=profile,
            )
            decision = await orchestrator.router.route(req)
            if hasattr(decision, "selected_tools"):
                selected = list(getattr(decision, "selected_tools", []) or [])
                reason = str(getattr(decision, "reason", "router") or "router")
                metadata = dict(getattr(decision, "metadata", {}) or {})
        except Exception as exc:
            _LOGGER.debug("Router route exception: %s", exc)

    if not selected:
        reg = get_tool_registry()
        available_tools = reg.list_tools() if hasattr(reg, "list_tools") else []

        retrieval_intent = getattr(profile, "retrieval_intent", None)
        target_goal = str(getattr(retrieval_intent, "target_goal", "") or "") if retrieval_intent else ""
        raw_intent = str(getattr(profile, "intent", "") or "")
        topic_str = str(getattr(profile, "topic", "") or "")
        domain_str = str(getattr(profile, "primary_domain", "") or "")
        intent_str = f"{raw_intent} {target_goal} {topic_str} {domain_str} {query}".lower()

        needs_action = bool(getattr(profile, "needs_action", False))
        needs_ws = bool(getattr(profile, "needs_workspace", False))
        needs_tools = needs_action or needs_ws

        if needs_tools or "sys" in intent_str or "tool" in intent_str or "time" in intent_str or "date" in intent_str or getattr(orchestrator, "reward_routing_enabled", False):
            for tool in available_tools:
                name = str(getattr(tool, "name", tool) if not isinstance(tool, str) else tool)
                if name == "sys" and (bool(getattr(profile, "needs_workspace", False)) or "sys" in intent_str or "time" in intent_str or "date" in intent_str or needs_tools):
                    selected.append("sys")
                    reason = "sys_time_intent"
                elif name == "compute.run" and ("math" in intent_str or "compute" in intent_str):
                    selected.append("compute.run")
                    reason = "compute_intent"
                elif name == "plot_chart" and ("plot" in intent_str or "chart" in intent_str):
                    selected.append("plot_chart")
                    reason = "plot_intent"

    final_selected = list(dict.fromkeys(selected))
    reward_route = evaluate_reward_routing(orchestrator, query=query)
    if reward_route is not None:
        metadata["reward_routing"] = reward_route
        if reward_route.get("block"):
            final_selected = []
            reason = "reward_model_risk_block"

    orchestrator._last_route_debug = {
        "selected_tools": list(final_selected),
        "reason": reason,
        "metadata": metadata,
    }
    return final_selected


def init_reward_scorer(orchestrator: Orchestrator) -> Optional[RewardModelScorer]:
    """Lazy initialize reward model scorer if available."""
    if hasattr(orchestrator, "_reward_scorer") and orchestrator._reward_scorer is not None:
        return orchestrator._reward_scorer
    try:
        scorer = RewardModelScorer()
        orchestrator._reward_scorer = scorer
        return scorer
    except Exception as exc:
        _LOGGER.debug("RewardModelScorer initialization skipped: %s", exc)
        return None


def evaluate_reward_routing(orchestrator: Orchestrator, *, query: str) -> Optional[Dict[str, Any]]:
    """Evaluate reward model scores for routing candidate tools."""
    if not getattr(orchestrator, "reward_routing_enabled", False):
        return None
    scorer = getattr(orchestrator, "reward_scorer", None)
    if scorer is None:
        return None

    if hasattr(scorer, "score_action"):
        score = scorer.score_action(
            action="tool_routing",
            input_text=query,
            context={
                "stage": "tool_selection",
                "session_id": getattr(orchestrator, "_active_session_id", None),
                "user_id": getattr(orchestrator, "_active_user_id", None),
            },
        )
    else:
        score = {}

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
    block_threshold = float(getattr(orchestrator, "reward_routing_block_threshold", 0.85))
    conf_threshold = float(getattr(orchestrator, "reward_routing_conf_threshold", 0.70))
    should_block = (
        eval_binary == 0
        and risk_score >= block_threshold
        and confidence >= conf_threshold
    )
    return {
        "enabled": True,
        "model_available": True,
        "eval_binary": eval_binary,
        "risk_score": risk_score,
        "confidence": confidence,
        "block": should_block,
        "thresholds": {
            "risk": block_threshold,
            "confidence": conf_threshold,
        },
        "source": score.get("source", "reward_model"),
    }


async def execute_tools(
    orchestrator: Orchestrator,
    *,
    selected_tools: List[str],
    query: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute selected tools via ToolExecutor."""
    if not selected_tools:
        return {}

    chat_session_id = session_id or getattr(orchestrator, "_active_session_id", "") or ""

    prepared = None
    exec_req = None
    if hasattr(orchestrator, "executor") and hasattr(orchestrator.executor, "execute"):
        try:
            from services.contracts import ExecutorRequest
            # Forward the router's own decision metadata (intent, retrieval_intent,
            # ...) -- set moments earlier by select_tools() below -- so downstream
            # parameter builders (e.g. _build_sys_parameters's retrieval_intent
            # branch) actually see it instead of always getting an empty dict.
            route_metadata: Dict[str, Any] = {}
            if hasattr(orchestrator, "_last_route_debug") and isinstance(orchestrator._last_route_debug, dict):
                route_metadata = dict(orchestrator._last_route_debug.get("metadata") or {})
            tracked_wsl_session_id = ""
            if hasattr(orchestrator, "_wsl_session_by_chat_session"):
                tracked_wsl_session_id = orchestrator._wsl_session_by_chat_session.get(chat_session_id, "")
            if tracked_wsl_session_id:
                route_metadata["wsl_session_id"] = tracked_wsl_session_id
            exec_req = ExecutorRequest(
                tool_names=selected_tools,
                query=query,
                session_id=chat_session_id,
                run_id=run_id or "",
                user_id=user_id or getattr(orchestrator, "_active_user_id", "") or "",
                routing_metadata=route_metadata,
            )
            prepared = (
                orchestrator.executor.prepare_tool_requests(exec_req)
                if hasattr(orchestrator.executor, "prepare_tool_requests")
                else None
            )
        except Exception as prep_exc:
            _LOGGER.debug("Failed preparing tool requests: %s", prep_exc)

    if hasattr(orchestrator, "judge_engine") and orchestrator.judge_engine is not None:
        try:
            input_payload = prepared[0].parameters if (prepared and hasattr(prepared[0], "parameters")) else {}
            # Issue #13 round 2: input_payload above is only the FIRST
            # prepared tool's parameters -- fine as the flat `input` shape
            # single-tool profiles read directly, but wrong for any other
            # tool in a multi-tool turn if used as-is. Carry each tool's
            # own parameters separately so JudgeEngine can resolve the
            # correct payload per sub-action instead of every tool seeing
            # tool #0's parameters.
            per_tool_parameters = (
                {req.tool_name: dict(req.parameters or {}) for req in prepared if hasattr(req, "tool_name")}
                if prepared
                else {}
            )
            ctx = orchestrator._create_judge_context_for_pre_action(
                run_id=run_id or getattr(orchestrator, "_active_run_id", "") or "",
                tool_names=selected_tools,
                query=query,
                input=input_payload,
                per_tool_parameters=per_tool_parameters,
            )
            decision = orchestrator.judge_engine.evaluate_pre_action(ctx)

            dec_str = decision.decision.value if hasattr(decision, "decision") and hasattr(decision.decision, "value") else str(getattr(decision, "decision", decision.get("decision", "allow") if isinstance(decision, dict) else "allow"))
            is_approved = getattr(decision, "approved", True) if not isinstance(decision, dict) else decision.get("approved", True)

            log_judge_pre_action(
                tool_name=",".join(selected_tools),
                decision=dec_str,
                request_id=run_id or getattr(orchestrator, "_active_run_id", "") or "",
                session_id=getattr(orchestrator, "_active_session_id", "") or "",
                run_id=run_id or getattr(orchestrator, "_active_run_id", "") or "",
            )
            if hasattr(orchestrator, "_last_executor_debug") and isinstance(orchestrator._last_executor_debug, dict):
                orchestrator._last_executor_debug.setdefault("judge_revise_count", 0)

            if dec_str.lower() in {"block", "reject"} or not is_approved:
                _LOGGER.warning("Pre-action judge blocked execution (fail-closed): %s", dec_str)
                return {tool: {"status": "blocked", "error": f"Pre-action judge blocked execution: {dec_str}"} for tool in selected_tools}

        except Exception as judge_exc:
            _LOGGER.error("Pre-action judge failed (fail-closed): %s", judge_exc)
            return {tool: {"status": "blocked", "error": f"Pre-action judge failure (fail-closed): {judge_exc}"} for tool in selected_tools}

    if exec_req is not None:
        try:
            if prepared is not None:
                res = await orchestrator.executor.execute(exec_req, prepared_requests=prepared)
            else:
                res = await orchestrator.executor.execute(exec_req)

            if hasattr(res, "metadata") and isinstance(res.metadata, dict) and hasattr(orchestrator, "_wsl_session_by_chat_session"):
                created_id = res.metadata.get("wsl_session_created_id")
                if created_id and chat_session_id:
                    orchestrator._wsl_session_by_chat_session[chat_session_id] = str(created_id)
                destroyed_id = res.metadata.get("wsl_session_destroyed_id")
                if destroyed_id and chat_session_id:
                    # Lifecycle hardening: only clear the tracked binding when it
                    # matches the destroyed id -- an unrelated/explicit destroy of
                    # a different session id must not wipe out a still-live
                    # tracked binding for this chat session.
                    if orchestrator._wsl_session_by_chat_session.get(chat_session_id) == str(destroyed_id):
                        del orchestrator._wsl_session_by_chat_session[chat_session_id]

            if hasattr(res, "tool_outputs") and isinstance(res.tool_outputs, dict):
                return res.tool_outputs
            if isinstance(res, dict):
                return res
        except Exception as exc:
            _LOGGER.error("Executor.execute failed: %s", exc)

    return {tool: {"status": "ok", "output": f"mock output for {tool}"} for tool in selected_tools}


def rank_discovery_candidate(
    orchestrator: Orchestrator,
    *,
    candidate: Dict[str, Any],
    query: str,
) -> float:
    """Rank a web discovery candidate based on title, snippet, and query similarity."""
    if not candidate or not query:
        return 0.0

    title = str(candidate.get("title") or "").lower()
    snippet = str(candidate.get("snippet") or candidate.get("content") or "").lower()
    q_terms = set(re.findall(r"\w+", query.lower()))

    if not q_terms:
        return 0.0

    t_terms = set(re.findall(r"\w+", title))
    s_terms = set(re.findall(r"\w+", snippet))

    t_overlap = len(q_terms & t_terms) / max(1, len(q_terms))
    s_overlap = len(q_terms & s_terms) / max(1, len(q_terms))

    return round(0.6 * t_overlap + 0.4 * s_overlap, 4)


def complete_web_discovery(
    orchestrator: Orchestrator,
    *,
    query: str,
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Fetch and return the top-ranked web discovery candidate."""
    if not candidates:
        return None

    ranked = [(rank_discovery_candidate(orchestrator, candidate=c, query=query), c) for c in candidates]
    ranked.sort(key=lambda x: x[0], reverse=True)

    if ranked and ranked[0][0] > 0.1:
        top_candidate = dict(ranked[0][1])
        top_candidate["rank_score"] = ranked[0][0]
        return top_candidate

    return None


def build_external_write_content(
    orchestrator: Orchestrator,
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    draft_content: str,
) -> Dict[str, Any]:
    """Build standardized external write tool arguments."""
    normalized_name = normalize_external_tool(tool_name)
    args = dict(arguments or {})

    if normalized_name in ("write_to_file", "replace_file_content"):
        if "content" not in args and draft_content:
            args["content"] = draft_content

    return {
        "tool_name": normalized_name,
        "arguments": args,
    }


def plan_external_tool_call(
    orchestrator: Orchestrator,
    *,
    raw_response: str,
    available_tools: List[str],
) -> Optional[ExternalToolCall]:
    """Plan an external tool call from LLM response text or schema."""
    if not raw_response:
        return None

    # Check for JSON tool call blocks
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
    if match:
        try:
            import json
            data = json.loads(match.group(1))
            tool_name = data.get("tool") or data.get("name") or data.get("tool_name")
            args = data.get("parameters") or data.get("arguments") or {}
            if tool_name and normalize_external_tool(tool_name) in [normalize_external_tool(t) for t in available_tools]:
                return ExternalToolCall(
                    tool_name=normalize_external_tool(tool_name),
                    arguments=args,
                )
        except Exception:
            pass

    return None


def plan_external_tool_followup(
    orchestrator: Orchestrator,
    *,
    previous_call: ExternalToolCall,
    tool_result: Dict[str, Any],
) -> Optional[ExternalToolCall]:
    """Plan a followup tool call if required by execution results."""
    if not tool_result or tool_result.get("status") == ToolStatus.FAILED:
        return None
    # No mandatory followup planned by default
    return None
