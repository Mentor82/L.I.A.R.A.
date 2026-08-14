"""FastAPI router for chat, streaming, session management and history endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from services.api.deps import get_memory_adapter, get_orchestrator
from services.api.models import SessionResponse, SessionUpdateRequest
from services.contracts import (
    ChatArtifact,
    ChatAttachment,
    ChatRequest,
    ChatResponse,
    MemoryFactUpsertRequest,
    MemoryHistoryAppendRequest,
    MemoryHistoryQueryRequest,
    MemoryHistoryResponse,
    OrchestratorRequest,
)
from services.memory.store import SessionStore
from services.shared.types import MemoryTier
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator import Orchestrator
from services.shared.attachment_security import scan_attachment_bytes
from services.shared.output_sanitizer import OutputSanitizer
from services.shared.sandboxing import (
    canonicalize_sandbox_root,
    get_global_sandbox_root,
    get_sandbox_mode,
    get_wsl_distro,
    is_wsl_sandbox_enabled,
    resolve_sandbox_root,
)
from services.tools.builtin.sys_audit import log_judge_pre_action
from services.vision import normalize_image_attachments


router = APIRouter(tags=["chat"])
_CHAT_STREAM_LOGGER = logging.getLogger("liara.api.chat.stream")
_CHAT_RUN_LOGGER = logging.getLogger("liara.api.chat.run")
_ARTIFACT_LOGGER = logging.getLogger("liara.api.chat.artifact")

_HARMFUL_QUERY_PATTERNS = (
    re.compile(r"\b(malware|ransomware|trojan|worm)\b", re.IGNORECASE),
    re.compile(r"\b(verbreit|spread|deploy|write|entwickl|build)\b.*\b(malware|ransomware|trojan|worm)\b", re.IGNORECASE),
    re.compile(r"\b(bypass|umgeh|evad|unentdeckt|undetected)\b.*\b(sicherheits|security|kontroll|detection)\b", re.IGNORECASE),
    re.compile(r"\bsicherheitskontrollen?\b.*\bumgeh\w*\b", re.IGNORECASE),
    re.compile(r"\bumgeh\w*\b.*\bschaden\b", re.IGNORECASE),
    re.compile(r"\bunbemerkt\b.*\bschaden\b", re.IGNORECASE),
    re.compile(r"\bschaden\s+anzuricht\w*\b", re.IGNORECASE),
    re.compile(r"\b(step[- ]by[- ]step|schritt[- ]f[üu]r[- ]schritt|konkret(e|en)? anleitung)\b.*\b(hack|malware|exploit|angriff|attack)\b", re.IGNORECASE),
)

_HARMFUL_RESPONSE_MARKERS = (
    "step-by-step",
    "schritt-fur-schritt",
    "anleitung",
    "installiere",
    "verbreite malware",
    "spread malware",
    "bypass security",
)





def _is_harmful_user_query(text: str) -> bool:
    query = (text or "").strip()
    if not query:
        return False
    return any(pattern.search(query) for pattern in _HARMFUL_QUERY_PATTERNS)


def _looks_like_actionable_harmful_response(text: str) -> bool:
    response = (text or "").lower()
    if not response:
        return False
    normalized = response.replace("ü", "u").replace("ö", "o").replace("ä", "a")
    if "malware" not in normalized and "exploit" not in normalized and "angriff" not in normalized and "attack" not in normalized:
        return False
    return any(marker in normalized for marker in _HARMFUL_RESPONSE_MARKERS)


def _safety_refusal_text(query: str) -> str:
    q = (query or "").lower()
    looks_german = any(token in q for token in ("bitte", "kannst", "wie", "schritt", "sicher"))
    if looks_german:
        return (
            "Dabei kann ich nicht helfen. "
            "Ich kann aber bei defensiver IT-Sicherheit unterstuetzen, "
            "zum Beispiel mit Erkennung, Härtung und Incident Response."
        )
    return (
        "I can't help with that. "
        "I can help with defensive cybersecurity instead, "
        "for example detection, hardening, and incident response."
    )


def _normalize_display_name(value: str | None) -> str | None:
    candidate = (value or "").strip()
    return candidate or None


def _extract_identity_display_name(request: ChatRequest, snapshot: dict[str, Any]) -> str | None:
    raw_name = getattr(request, "user_display_name", getattr(request, "display_name", None))
    display_name = _normalize_display_name(raw_name)
    if display_name:
        return display_name
    metadata = snapshot.get("metadata") or {}
    if isinstance(metadata, dict):
        candidate = _normalize_display_name(metadata.get("user_display_name") or metadata.get("display_name"))
        if candidate:
            return candidate
    return None


def _sanitize_public_error_message(exc: Exception) -> str:
    """Ensure raw exception details, tracebacks, SQL statements, and paths do not leak to HTTP clients."""
    if isinstance(exc, HTTPException):
        return str(exc.detail) if isinstance(exc.detail, str) else "Request validation or routing error."
    err_str = str(exc)
    if "postgresql" in err_str.lower() or "sql" in err_str.lower() or "database" in err_str.lower():
        return "A database or persistence error occurred."
    if "path" in err_str.lower() or "c:" in err_str.lower() or "/" in err_str:
        return "An internal storage access error occurred."
    return "An error occurred while processing the request."


def _identity_prompt_block(*, user_id: str, display_name: str | None) -> str:
    clean_name = _normalize_display_name(display_name)
    if clean_name:
        clean_id = (user_id or "user").strip()
        return (
            "[SYSTEM IDENTITY NOTICE]\n"
            f"The user's display name is '{clean_name}' (user_id: '{clean_id}').\n"
            f"Address them naturally as {clean_name} when appropriate."
        )
    return ""


def _build_effective_chat_query(message: str, attachments: list[ChatAttachment], identity_prompt_block: str | None = None) -> str:
    parts = []
    if identity_prompt_block and identity_prompt_block.strip():
        parts.append(identity_prompt_block.strip())
    parts.append((message or "").strip())
    text_attachments = [a for a in attachments if a.text_content and a.text_content.strip()]
    if text_attachments:
        parts.append("\n\n--- Bereitgestellte Dateien/Anhänge ---")
        for a in text_attachments:
            parts.append(f"\n[{a.name}] ({a.media_type or 'text/plain'}):\n{a.text_content.strip()}")
    return "\n\n".join(p for p in parts if p)


def _attachment_history_metadata(attachments: list[ChatAttachment]) -> dict[str, Any]:
    if not attachments:
        return {}

    items: list[dict[str, Any]] = []
    for attachment in attachments:
        items.append(
            {
                "name": attachment.name,
                "media_type": attachment.media_type,
                "size_bytes": attachment.size_bytes,
                "source": attachment.source,
                "has_text_content": bool((attachment.text_content or "").strip()),
                "has_content_url": bool(attachment.content_url),
                "scan": dict((attachment.metadata or {}).get("scan") or {}),
                "metadata": dict(attachment.metadata or {}),
            }
        )

    return {
        "attachment_count": len(items),
        "attachments": items,
    }


def _ensure_artifact_urls(artifacts: list[dict[str, Any]], *, session_id: str | None = None) -> None:
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("url"):
            continue
        metadata = artifact.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            artifact["metadata"] = metadata


def _extract_chat_artifacts(orchestrator_result: Any, *, session_id: str | None = None) -> list[dict[str, Any]] | None:
    artifacts = getattr(orchestrator_result, "artifacts", None)
    if isinstance(artifacts, list):
        normalized: list[dict[str, Any]] = []
        for artifact in artifacts:
            if isinstance(artifact, dict):
                if artifact.get("kind"):
                    normalized.append(dict(artifact))
            elif hasattr(artifact, "model_dump"):
                payload = artifact.model_dump()
                if payload.get("kind"):
                    normalized.append(payload)
        if normalized:
            _ARTIFACT_LOGGER.debug(f"Extracted {len(normalized)} artifact(s) from orchestrator result")
            _ensure_artifact_urls(normalized, session_id=session_id)
            return normalized

    tool_results = getattr(orchestrator_result, "tool_results", {})
    if not isinstance(tool_results, dict):
        return None

    extracted: list[dict[str, Any]] = []
    for tool_name, output in tool_results.items():
        if not isinstance(output, dict):
            continue
        output_artifacts = output.get("artifacts")
        if isinstance(output_artifacts, list):
            for entry in output_artifacts:
                if not isinstance(entry, dict) or not entry.get("kind"):
                    continue
                normalized_entry = dict(entry)
                normalized_entry.setdefault("source_tool", str(tool_name))
                normalized_entry.setdefault("metadata", {})
                extracted.append(normalized_entry)
    if extracted:
        _ARTIFACT_LOGGER.info(f"Extracted {len(extracted)} artifact(s) from tool_results (tools: {', '.join(set(entry.get('source_tool', 'unknown') for entry in extracted))})")
        _ensure_artifact_urls(extracted, session_id=session_id)
    return extracted or None


async def _get_session_snapshot_best_effort(
    adapter: Any,
    session_id: str,
) -> dict[str, Any]:
    try:
        snapshot = await adapter.get(MemoryTier.SESSION, f"session:{session_id}", default={})
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


def _resolve_effective_sandbox_root(explicit: str | None, snapshot: dict[str, Any]) -> str:
    candidate = explicit
    if not candidate:
        metadata = snapshot.get("metadata") or {}
        candidate = metadata.get("sandbox_root") if isinstance(metadata, dict) else None
    candidate = candidate or get_global_sandbox_root()
    return canonicalize_sandbox_root(candidate)


def _build_session_metadata(existing: dict[str, Any] | None, sandbox_root: str | None) -> dict[str, Any]:
    metadata = dict(existing or {})
    if sandbox_root:
        canonical_root = canonicalize_sandbox_root(sandbox_root)
        metadata["sandbox_root"] = canonical_root
        metadata["sandbox_root_mode"] = get_sandbox_mode()
        if is_wsl_sandbox_enabled():
            metadata["sandbox_root_local"] = str(resolve_sandbox_root(canonical_root, get_global_sandbox_root()))
            metadata["sandbox_root_distro"] = get_wsl_distro()
    return metadata


def _build_session_response(
    session_id: str,
    user_id: str,
    snapshot: dict[str, Any],
    memory_status: str,
    message_count: int,
) -> SessionResponse:
    metadata = dict(snapshot.get("metadata") or {})
    metadata["memory_status"] = memory_status
    return SessionResponse(
        session_id=session_id,
        user_id=user_id,
        message_count=message_count,
        last_run_id=snapshot.get("last_run_id"),
        summary=snapshot.get("summary"),
        last_accessed=snapshot.get("last_accessed") or snapshot.get("updated_at"),
        created_at=snapshot.get("created_at"),
        metadata=metadata,
    )


async def _write_session_snapshot(
    adapter: Any,
    session_id: str,
    user_id: str,
    now: str,
    *,
    last_run_id: str | None = None,
    sandbox_root: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    snapshot = await _get_session_snapshot_best_effort(adapter, session_id)
    merged_metadata = _build_session_metadata(snapshot.get("metadata"), sandbox_root)
    if extra_metadata:
        merged_metadata.update(extra_metadata)

    try:
        await adapter.set(
            MemoryTier.SESSION,
            f"session:{session_id}",
            {
                "session_id": session_id,
                "user_id": user_id,
                "last_run_id": last_run_id or snapshot.get("last_run_id"),
                "updated_at": now,
                "metadata": merged_metadata,
            },
            ttl_seconds=getattr(SessionStore, "DEFAULT_TTL_SECONDS", 86400 * 30),
        )
    except Exception:
        pass


async def _emit_stream_progress(
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    *,
    stage: str,
    message: str,
    run_id: str,
    session_id: str,
    user_id: str,
    **metadata: Any,
) -> None:
    payload = {
        "stage": stage,
        "message": message,
        "run_id": run_id,
        "session_id": session_id,
        "user_id": user_id,
        "ts": datetime.now(UTC).isoformat(),
        "metadata": metadata,
    }
    _CHAT_STREAM_LOGGER.info(
        "stream_progress stage=%s run_id=%s session_id=%s message=%s metadata=%s",
        stage,
        run_id,
        session_id,
        message,
        metadata,
    )
    if progress_callback is not None:
        await progress_callback(payload)


def _build_public_stream_final_payload(chat_response: ChatResponse) -> dict[str, Any]:
    raw = chat_response.model_dump(mode="json")
    metadata = dict(raw.get("metadata") or {})
    sanitized_metadata = {
        k: v for k, v in metadata.items()
        if not k.startswith("internal_")
    }
    raw["metadata"] = sanitized_metadata
    return raw


async def _run_chat(
    request: ChatRequest,
    adapter: MemoryServiceAdapter,
    orch: Orchestrator,
    *,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, ChatResponse]:
    run_started = time.perf_counter()
    run_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    api_timings_ms: dict[str, float] = {}
    attachments = list(request.attachments or [])
    snapshot = await _get_session_snapshot_best_effort(adapter, request.session_id)
    identity_display_name = _extract_identity_display_name(request, snapshot)
    output_sanitizer = OutputSanitizer()

    await _emit_stream_progress(
        progress_callback,
        stage="accepted",
        message="Chat request accepted",
        run_id=run_id,
        session_id=request.session_id,
        user_id=request.user_id,
        sandbox_root=request.sandbox_root,
        attachment_count=len(attachments),
    )

    if identity_display_name:
        try:
            await adapter.upsert_fact(
                MemoryFactUpsertRequest(
                    namespace=f"user:{request.user_id}:profile",
                    key="display_name",
                    value=identity_display_name,
                    source="liara-api-chat",
                    confidence=1.0,
                    tags=["identity", "profile", "display_name"],
                    metadata={
                        "session_id": request.session_id,
                        "user_id": request.user_id,
                    },
                )
            )
        except Exception:
            pass

    try:
        sandbox_started = time.perf_counter()
        effective_sandbox_root = _resolve_effective_sandbox_root(request.sandbox_root, snapshot)
        api_timings_ms["sandbox_resolution"] = round((time.perf_counter() - sandbox_started) * 1000, 3)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        local_sandbox_root = resolve_sandbox_root(effective_sandbox_root, get_global_sandbox_root())
        attachments, image_scan_inputs = normalize_image_attachments(
            attachments,
            sandbox_root=local_sandbox_root,
            max_bytes=max(1024, int(os.getenv("LIARA_UPLOAD_MAX_BYTES", str(5 * 1024 * 1024)))),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc)}) from exc

    attachment_scan_results: list[dict[str, Any]] = []
    for attachment_index, attachment in enumerate(attachments):
        scan_input = image_scan_inputs.get(
            attachment_index,
            (attachment.text_content or "").encode("utf-8"),
        )
        scan_result = scan_attachment_bytes(scan_input)
        attachment.metadata = dict(attachment.metadata or {})
        attachment.metadata["scan"] = scan_result.to_metadata()
        attachment_scan_results.append(
            {
                "name": attachment.name,
                "media_type": attachment.media_type,
                **scan_result.to_metadata(),
            }
        )
        if scan_result.status == "blocked":
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Attachment blocked by malware scanner.",
                    "attachment": attachment.name,
                    "scan": scan_result.to_metadata(),
                },
            )

    effective_query = _build_effective_chat_query(
        request.message,
        attachments,
        identity_prompt_block=_identity_prompt_block(
            user_id=request.user_id,
            display_name=identity_display_name,
        ),
    )
    is_harmful_query = _is_harmful_user_query(request.message)
    history_user_content = request.message
    history_user_metadata: dict[str, Any] = {
        "source": "liara-api",
        **_attachment_history_metadata(attachments),
    }
    if identity_display_name:
        history_user_metadata["display_name"] = identity_display_name
    if is_harmful_query:
        history_user_content = "[SAFETY_BLOCKED_USER_QUERY]"
        history_user_metadata["safety_blocked"] = True
        history_user_metadata["safety_block_stage"] = "pre_generation"
        history_user_metadata["safety_user_query_redacted"] = True

    history_write_started = time.perf_counter()
    await adapter.append_history(
        MemoryHistoryAppendRequest(
            session_id=request.session_id,
            run_id=run_id,
            user_id=request.user_id,
            role="user",
            content=history_user_content,
            metadata=history_user_metadata,
        )
    )
    api_timings_ms["history_user_write"] = round((time.perf_counter() - history_write_started) * 1000, 3)
    await _emit_stream_progress(
        progress_callback,
        stage="history_user_written",
        message="User message stored in session history",
        run_id=run_id,
        session_id=request.session_id,
        user_id=request.user_id,
    )

    if is_harmful_query:
        refusal_text = _safety_refusal_text(request.message)
        log_judge_pre_action(
            tool_name="chat_safety_pre",
            decision="block",
            issues=["Unsafe user request blocked before generation."],
            constraints={
                "risk_flags": ["policy_safety_violation"],
                "validator_decision": "block",
            },
            request_id=run_id,
            session_id=request.session_id,
            run_id=run_id,
            source="api",
            context="chat_safety_pre_block",
        )
        await _emit_stream_progress(
            progress_callback,
            stage="orchestration_skipped_safety",
            message="Unsafe request blocked before orchestration",
            run_id=run_id,
            session_id=request.session_id,
            user_id=request.user_id,
        )

        history_write_started = time.perf_counter()
        await adapter.append_history(
            MemoryHistoryAppendRequest(
                session_id=request.session_id,
                run_id=run_id,
                user_id=request.user_id,
                role="assistant",
                content=refusal_text,
                metadata={"source": "liara-api", "safety_blocked": True},
            )
        )
        api_timings_ms["history_assistant_write"] = round((time.perf_counter() - history_write_started) * 1000, 3)

        try:
            snapshot_write_started = time.perf_counter()
            await _write_session_snapshot(
                adapter,
                request.session_id,
                request.user_id,
                now,
                last_run_id=run_id,
                sandbox_root=effective_sandbox_root,
            )
            api_timings_ms["session_snapshot_write"] = round((time.perf_counter() - snapshot_write_started) * 1000, 3)
        except Exception:
            pass

        api_timings_ms["total"] = round((time.perf_counter() - run_started) * 1000, 3)
        metadata = {
            "state_final": "complete",
            "validation": {
                "passed": False,
                "decision": "block",
                "checks": {"safety": "fail"},
                "issues": ["Unsafe request blocked before generation."],
                "confidence_score": 1.0,
                "risk_flags": ["policy_safety_violation"],
            },
            "context_debug": {"mode": "NONE", "sources": {}},
            "debug_run": {
                "api_timings_ms": api_timings_ms,
                "selected_tools": [],
                "failed_tools": [],
            },
            "attachment_count": len(attachments),
            "attachments": _attachment_history_metadata(attachments).get("attachments", []),
            "attachment_scan_results": attachment_scan_results,
            "safety_blocked": True,
            "safety_block_stage": "pre_generation",
        }
        chat_response = ChatResponse(
            run_id=run_id,
            response=refusal_text,
            tools_used=[],
            tool_outputs={},
            llm_provider="safety_guard",
            llm_model="deterministic",
            ttft_ms=0.0,
            gen_ms=0.0,
            validation_passed=False,
            metadata=metadata,
            artifacts=None,
        )
        return run_id, chat_response

    await _emit_stream_progress(
        progress_callback,
        stage="orchestration_started",
        message="Orchestrator is gathering context and generating a response",
        run_id=run_id,
        session_id=request.session_id,
        user_id=request.user_id,
    )

    orchestration_started = time.perf_counter()
    llm_provider = getattr(request, "llm_provider", getattr(request, "preferred_provider", None))
    simulation_mode = getattr(request, "simulation_mode", False)
    max_reasoning_steps = getattr(request, "max_reasoning_steps", None)

    orch_request = OrchestratorRequest(
        session_id=request.session_id,
        run_id=run_id,
        user_id=request.user_id,
        display_name=identity_display_name,
        query=effective_query,
        routing_query=request.message,
        attachments=attachments,
        tools_override=request.tools_override,
        available_tools=request.available_tools,
        allow_external_tool_calls=request.allow_external_tool_calls,
        tool_results=request.tool_results,
        max_tokens=request.max_tokens,
        preferred_provider=llm_provider,
        preferred_model=request.preferred_model,
        request_source=request.request_source,
        risk_reassessment=request.risk_reassessment,
        sandbox_root=effective_sandbox_root,
        user_feedback_score=request.user_feedback_score,
        user_feedback_stars=request.user_feedback_stars,
    )

    run_fn = getattr(orch, "run", getattr(orch, "process", None))
    if run_fn is None:
        raise HTTPException(status_code=500, detail="Orchestrator has no run or process method")

    try:
        orchestrator_result = await run_fn(orch_request)
    except TypeError:
        orchestrator_result = await run_fn(orch_request)

    api_timings_ms["orchestration"] = round((time.perf_counter() - orchestration_started) * 1000, 3)

    raw_response_text = getattr(orchestrator_result, "final_response", getattr(orchestrator_result, "response", ""))
    llm_gen = getattr(orchestrator_result, "llm_generation", {}) if isinstance(getattr(orchestrator_result, "llm_generation", None), dict) else {}
    context_debug = llm_gen.get("context_debug", {})

    sanitization_started = time.perf_counter()
    sanitization = output_sanitizer.sanitize(raw_response_text)
    public_response_text = sanitization.text
    api_timings_ms["output_sanitization"] = round((time.perf_counter() - sanitization_started) * 1000, 3)

    validation_result = dict(getattr(orchestrator_result, "validation_result", {}) or {})
    validation_decision = str(validation_result.get("decision") or "").strip().lower()
    validation_risk_flags = list(validation_result.get("risk_flags") or [])
    safety_blocked_post = False
    query_harmful = is_harmful_query
    response_harmful = _looks_like_actionable_harmful_response(public_response_text)
    validator_safety_block = (
        validation_decision == "block"
        and "policy_safety_violation" in validation_risk_flags
        and (query_harmful or response_harmful)
    )
    if validator_safety_block or response_harmful:
        safety_blocked_post = True
        public_response_text = _safety_refusal_text(request.message)
        validation_result["passed"] = False
        validation_result["decision"] = "block"
        checks = dict(validation_result.get("checks") or {})
        checks["safety"] = "fail"
        validation_result["checks"] = checks
        issues = list(validation_result.get("issues") or [])
        if "Unsafe response blocked after generation." not in issues:
            issues.append("Unsafe response blocked after generation.")
        validation_result["issues"] = issues
        risk_flags = set(validation_result.get("risk_flags") or [])
        risk_flags.add("policy_safety_violation")
        validation_result["risk_flags"] = sorted(risk_flags)
        log_judge_pre_action(
            tool_name="chat_safety_post",
            decision="block",
            issues=issues,
            constraints={
                "risk_flags": validation_result.get("risk_flags"),
                "validator_decision": "block",
            },
            request_id=run_id,
            session_id=request.session_id,
            run_id=run_id,
            source="api",
            context="chat_safety_post_block",
        )

    validation_checks = dict(validation_result.get("checks") or {})
    graph_priority_blocked = validation_checks.get("graph_priority") == "fail"
    if graph_priority_blocked and not safety_blocked_post:
        issues = list(validation_result.get("issues") or [])
        relation_text = None
        for issue in issues:
            match = re.search(r"authoritative graph relation\s+(.+)$", str(issue))
            if match:
                relation_text = match.group(1).strip()
                break
        if relation_text:
            public_response_text = (
                "Die Antwort wurde blockiert, weil sie einer belastbaren Graph-Beziehung widerspricht. "
                f"Belastbare Graph-Beziehung: {relation_text}."
            )
        else:
            public_response_text = (
                "Die Antwort wurde blockiert, weil sie einer belastbaren Graph-Beziehung widerspricht."
            )
        validation_result["passed"] = False
        validation_result["decision"] = "block"
        validation_checks["graph_priority"] = "fail"
        validation_result["checks"] = validation_checks
        risk_flags = set(validation_result.get("risk_flags") or [])
        risk_flags.add("graph_priority_violation")
        validation_result["risk_flags"] = sorted(risk_flags)
        validation_result["graph_priority_blocked"] = True

    tools_executed = list(getattr(orchestrator_result, "tools_executed", getattr(orchestrator_result, "tools_used", [])) or [])

    await _emit_stream_progress(
        progress_callback,
        stage="orchestration_complete",
        message="Response generated and validated",
        run_id=run_id,
        session_id=request.session_id,
        user_id=request.user_id,
        context_mode=context_debug.get("mode"),
        context_sources=context_debug.get("sources", {}),
        tools_used=tools_executed,
        validation_decision=validation_result.get("decision"),
        output_sanitized=sanitization.changed,
        output_sanitizer_rules=sanitization.applied_rules,
        safety_blocked=safety_blocked_post,
        attachment_count=len(attachments),
        attachment_scan_results=attachment_scan_results,
    )

    history_write_started = time.perf_counter()
    await adapter.append_history(
        MemoryHistoryAppendRequest(
            session_id=request.session_id,
            run_id=run_id,
            user_id=request.user_id,
            role="assistant",
            content=public_response_text,
            metadata={"source": "liara-api"},
        )
    )
    api_timings_ms["history_assistant_write"] = round((time.perf_counter() - history_write_started) * 1000, 3)
    await _emit_stream_progress(
        progress_callback,
        stage="history_assistant_written",
        message="Assistant response stored in session history",
        run_id=run_id,
        session_id=request.session_id,
        user_id=request.user_id,
    )

    session_snapshot_ms = None
    try:
        snapshot_write_started = time.perf_counter()
        await _write_session_snapshot(
            adapter,
            request.session_id,
            request.user_id,
            now,
            last_run_id=run_id,
            sandbox_root=effective_sandbox_root,
        )
        session_snapshot_ms = round((time.perf_counter() - snapshot_write_started) * 1000, 3)
        api_timings_ms["session_snapshot_write"] = session_snapshot_ms
        await _emit_stream_progress(
            progress_callback,
            stage="session_snapshot_written",
            message="Session snapshot updated",
            run_id=run_id,
            session_id=request.session_id,
            user_id=request.user_id,
        )
    except Exception:
        pass

    if context_debug.get("mode") == "MEMORY":
        await _emit_stream_progress(
            progress_callback,
            stage="memory_effect_detected",
            message="Earlier session context influenced this answer",
            run_id=run_id,
            session_id=request.session_id,
            user_id=request.user_id,
            context_mode=context_debug.get("mode"),
            context_sources=context_debug.get("sources", {}),
        )

    total_ms = round((time.perf_counter() - run_started) * 1000, 3)
    api_timings_ms["total"] = total_ms

    run_debug: dict[str, Any] = {
        "api_timings_ms": api_timings_ms,
        "selected_tools": list(tools_executed),
        "failed_tools": [],
    }

    execution_trace = list(getattr(orchestrator_result, "execution_trace", []) or [])
    for transition in execution_trace:
        if not isinstance(transition, dict):
            continue
        to_state = transition.get("to")
        metadata = transition.get("metadata") or {}
        if to_state == "tool_execution":
            run_debug["executor_debug"] = dict(metadata.get("executor_debug") or {})
            run_debug["failed_tools"] = list((metadata.get("executor_debug") or {}).get("failed_tools", []))
        elif to_state == "tool_selection":
            run_debug["route_debug"] = dict(metadata.get("route_debug") or {})
            route_metadata = dict((metadata.get("route_debug") or {}).get("metadata") or {})
            if route_metadata.get("semantic_routing"):
                run_debug["semantic_route"] = {
                    "intent": route_metadata.get("semantic_intent"),
                    "score": route_metadata.get("semantic_score"),
                    "scores": dict(route_metadata.get("semantic_scores") or {}),
                    "thresholds": dict(route_metadata.get("semantic_thresholds") or {}),
                }
        elif to_state == "llm_generation":
            run_debug["prompt_debug"] = dict(metadata.get("prompt_debug") or {})
            run_debug["context_debug"] = dict(metadata.get("context_debug") or context_debug)
        elif to_state == "validation":
            run_debug["validation_trace"] = {
                "decision": metadata.get("decision"),
                "issues": metadata.get("issues", []),
                "timing_ms": metadata.get("timing_ms"),
            }
        elif to_state == "complete":
            run_debug["completion"] = dict(metadata)
            if isinstance(metadata.get("reasoning_metrics"), dict):
                run_debug["reasoning_metrics"] = dict(metadata.get("reasoning_metrics") or {})

    if "context_debug" not in run_debug:
        run_debug["context_debug"] = dict(context_debug)

    artifacts_payload = _extract_chat_artifacts(orchestrator_result, session_id=request.session_id)
    typed_artifacts: list[ChatArtifact] | None = None
    if isinstance(artifacts_payload, list):
        typed_artifacts = []
        for artifact in artifacts_payload:
            if isinstance(artifact, ChatArtifact):
                typed_artifacts.append(artifact)
                continue
            if isinstance(artifact, dict):
                try:
                    typed_artifacts.append(ChatArtifact(**artifact))
                except Exception:
                    _ARTIFACT_LOGGER.warning("Skipping invalid artifact payload during ChatResponse cast")
        if not typed_artifacts:
            typed_artifacts = None

    run_debug["sanitizer"] = {
        "changed": sanitization.changed,
        "removed_fragments": sanitization.removed_fragments,
        "applied_rules": sanitization.applied_rules,
    }

    tool_outputs = dict(getattr(orchestrator_result, "tool_results", getattr(orchestrator_result, "tool_outputs", {})) or {})
    state_final = str(getattr(orchestrator_result, "state_final", "complete"))
    ttft_ms = llm_gen.get("ttft_ms") or getattr(orchestrator_result, "ttft_ms", None)
    gen_ms = llm_gen.get("gen_ms") or getattr(orchestrator_result, "gen_ms", None)
    val_passed = bool(validation_result.get("passed", getattr(orchestrator_result, "validation_passed", True)))

    combined_metadata: dict[str, Any] = {
        "state_final": state_final,
        "execution_trace": execution_trace,
        "validation": validation_result,
        "context_debug": llm_gen.get("context_debug", {}),
        "inference_metadata": llm_gen.get("inference_metadata", {}),
        "debug_run": run_debug,
        "reasoning_metrics": dict(run_debug.get("reasoning_metrics") or {}),
        "sanitizer": {
            "changed": sanitization.changed,
            "removed_fragments": sanitization.removed_fragments,
            "applied_rules": sanitization.applied_rules,
        },
        "attachment_count": len(attachments),
        "attachments": _attachment_history_metadata(attachments).get("attachments", []),
        "attachment_scan_results": attachment_scan_results,
    }
    if safety_blocked_post:
        combined_metadata["safety_blocked"] = True
        combined_metadata["safety_block_stage"] = "post_generation"

    chat_response = ChatResponse(
        run_id=run_id,
        response=public_response_text,
        tools_used=tools_executed,
        tool_outputs=tool_outputs,
        llm_provider=str(llm_gen.get("provider") or getattr(orchestrator_result, "llm_provider", "unknown")),
        llm_model=str(llm_gen.get("model") or getattr(orchestrator_result, "llm_model", "unknown")),
        ttft_ms=ttft_ms,
        gen_ms=gen_ms,
        validation_passed=val_passed,
        metadata=combined_metadata,
        artifacts=typed_artifacts,
    )

    await _emit_stream_progress(
        progress_callback,
        stage="completed",
        message="Chat run completed successfully",
        run_id=run_id,
        session_id=request.session_id,
        user_id=request.user_id,
        timings_ms=api_timings_ms,
    )

    return run_id, chat_response


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request_body: ChatRequest,
    response: Response,
    adapter: MemoryServiceAdapter = Depends(get_memory_adapter),
    orch: Orchestrator = Depends(get_orchestrator),
) -> ChatResponse:
    response.headers["Cache-Control"] = "no-store"
    _run_id, chat_response = await _run_chat(request_body, adapter, orch)
    return chat_response


@router.post("/chat/stream")
async def chat_stream(
    request_body: ChatRequest,
    adapter: MemoryServiceAdapter = Depends(get_memory_adapter),
    orch: Orchestrator = Depends(get_orchestrator),
) -> StreamingResponse:
    async def _event_stream():
        heartbeat_seconds = max(0.1, float(os.getenv("LIARA_STREAM_HEARTBEAT_SECONDS", "12")))
        started_at = asyncio.get_running_loop().time()
        progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        latest_progress: dict[str, Any] = {
            "stage": "starting",
            "message": "Preparing chat run",
            "ts": datetime.now(UTC).isoformat(),
        }

        async def _progress_callback(payload: dict[str, Any]) -> None:
            await progress_queue.put(payload)

        run_task = None
        progress_task = None
        try:
            run_task = asyncio.create_task(_run_chat(request_body, adapter, orch, progress_callback=_progress_callback))
            progress_task = asyncio.create_task(progress_queue.get())

            while True:
                done, _pending = await asyncio.wait(
                    {run_task, progress_task},
                    timeout=heartbeat_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if progress_task in done:
                    latest_progress = progress_task.result()
                    yield (
                        "event: progress\n"
                        f"data: {json.dumps(latest_progress)}\n\n"
                    )
                    progress_task = asyncio.create_task(progress_queue.get())

                if run_task in done:
                    break

                if not done:
                    yield (
                        "event: heartbeat\n"
                        f"data: {json.dumps({'ts': datetime.now(UTC).isoformat(), 'stage': latest_progress.get('stage', 'running'), 'elapsed_ms': int((asyncio.get_running_loop().time() - started_at) * 1000)})}\n\n"
                    )

            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except (asyncio.CancelledError, Exception):
                    pass

            while not progress_queue.empty():
                latest_progress = progress_queue.get_nowait()
                yield (
                    "event: progress\n"
                    f"data: {json.dumps(latest_progress)}\n\n"
                )

            try:
                run_id, chat_response = await run_task
            except Exception as exc:
                sanitized_msg = _sanitize_public_error_message(exc)
                yield (
                    "event: error\n"
                    f"data: {json.dumps({'code': 'STREAM_EXECUTION_ERROR', 'message': sanitized_msg, 'ts': datetime.now(UTC).isoformat()})}\n\n"
                )
                yield "event: done\ndata: {}\n\n"
                return

            text = chat_response.response
            chunk_size = 120

            for index in range(0, max(1, len(text)), chunk_size):
                chunk = text[index:index + chunk_size]
                yield (
                    "event: chunk\n"
                    f"data: {json.dumps({'run_id': run_id, 'index': index // chunk_size, 'text': chunk})}\n\n"
                )

            artifacts = chat_response.artifacts or []
            for artifact_index, artifact in enumerate(artifacts):
                artifact_payload = artifact.model_dump() if hasattr(artifact, "model_dump") else artifact
                payload = {
                    "run_id": run_id,
                    "index": artifact_index,
                    "artifact": artifact_payload,
                }
                yield (
                    "event: artifact\n"
                    f"data: {json.dumps(payload)}\n\n"
                )

            yield (
                "event: final\n"
                f"data: {json.dumps(_build_public_stream_final_payload(chat_response), ensure_ascii=False)}\n\n"
            )
            yield "event: done\ndata: {}\n\n"
        finally:
            tasks_to_cleanup = [t for t in (run_task, progress_task) if t is not None and not t.done()]
            for t in tasks_to_cleanup:
                t.cancel()
            if tasks_to_cleanup:
                await asyncio.gather(*tasks_to_cleanup, return_exceptions=True)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/history", response_model=MemoryHistoryResponse)
async def history(
    response: Response,
    session_id: str = Query(...),
    run_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    include_tool_messages: bool = Query(default=True),
    adapter: MemoryServiceAdapter = Depends(get_memory_adapter),
) -> MemoryHistoryResponse:
    response.headers["Cache-Control"] = "private, no-store"
    return await adapter.query_history(
        MemoryHistoryQueryRequest(
            session_id=session_id,
            run_id=run_id,
            limit=limit,
            include_tool_messages=include_tool_messages,
        )
    )


@router.get("/session", response_model=SessionResponse)
async def session(
    response: Response,
    session_id: str = Query(...),
    user_id: str = Query(...),
    adapter: MemoryServiceAdapter = Depends(get_memory_adapter),
) -> SessionResponse:
    response.headers["Cache-Control"] = "private, no-store"
    snapshot = await _get_session_snapshot_best_effort(adapter, session_id)
    history_response = await adapter.query_history(
        MemoryHistoryQueryRequest(
            session_id=session_id,
            limit=500,
            include_tool_messages=True,
        )
    )
    return _build_session_response(
        session_id,
        user_id,
        snapshot,
        history_response.status.status,
        len(history_response.items),
    )


@router.post("/session", response_model=SessionResponse)
async def upsert_session(
    request_body: SessionUpdateRequest,
    response: Response,
    adapter: MemoryServiceAdapter = Depends(get_memory_adapter),
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC).isoformat()
    existing_snapshot = await _get_session_snapshot_best_effort(adapter, request_body.session_id)

    try:
        effective_sandbox_root = _resolve_effective_sandbox_root(request_body.sandbox_root, existing_snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _write_session_snapshot(
        adapter,
        request_body.session_id,
        request_body.user_id,
        now,
        sandbox_root=effective_sandbox_root,
        extra_metadata=request_body.metadata,
    )

    snapshot = await _get_session_snapshot_best_effort(adapter, request_body.session_id)
    history_response = await adapter.query_history(
        MemoryHistoryQueryRequest(
            session_id=request_body.session_id,
            limit=500,
            include_tool_messages=True,
        )
    )
    return _build_session_response(
        request_body.session_id,
        request_body.user_id,
        snapshot,
        history_response.status.status,
        len(history_response.items),
    )
