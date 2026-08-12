"""FastAPI app for the liara-api service."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import mimetypes
import os
import re
import time
import httpx
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from services.config import Settings
from services.contracts import (
    ChatArtifact,
    ChatAttachment,
    ChatRequest,
    ChatResponse,
    MemoryDreamingProposalListRequest,
    MemoryFactUpsertRequest,
    MemoryHealthResponse,
    MemoryHistoryAppendRequest,
    MemoryHistoryQueryRequest,
    MemoryHistoryResponse,
    MemoryServiceStatus,
    RelationCleanupExpiredRequest,
    RelationCleanupExpiredResponse,
    GraphSubgraphRequest,
    GraphSubgraphResponse,
    HeartbeatOperationsResponse,
    HeartbeatSnapshot,
    OrchestratorRequest,
    StateCurve,
    SelfObserverOperationsResponse,
    SelfInspectionDecision,
    SystemStateEnvelope,
    ToolExecutionRequest,
    ToolExecutionResult,
    TtsGenerationRequest,
    TtsHealthResponse,
)
from services.inference.gateway import InferenceGateway
from services.inference.llama_cpp_server import LlamaCppServerManager
from services.inference.audio_streaming import (
    AudioStreamEncodingError,
    codec_media_type,
    codec_sample_rate,
    encode_audio_stream,
    resolve_ffmpeg_path,
)
from services.inference.tts_adapter import (
    TtsAdapterError,
    TtsServiceAdapter,
    prepare_pcm_stream_artifact,
)
from services.memory.store import BackedMemoryServiceStore, EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import FactStore, GraphStore, MemoryLayer, RetrievalIndex, SessionStore
from services.memory_adapter import InProcessMemoryAdapter, MemoryServiceAdapter, RemoteMemoryAdapter
from services.orchestrator import Orchestrator
from services.shared.sandboxing import canonicalize_sandbox_root, ensure_within_boundary, get_global_sandbox_root, get_sandbox_mode, get_wsl_distro, is_wsl_sandbox_enabled, resolve_sandbox_root
from services.shared.attachment_security import extract_text_preview, scan_attachment_bytes
from services.shared.exceptions import MemoryError
from services.shared.output_sanitizer import OutputSanitizer
from services.shared.types import MemoryTier
from services.tools.coordinator import ToolCoordinator
from services.tools.governance import (
    create_pending_sys_governance_proposal,
    load_sys_governance_proposals,
    sys_governance_mode,
)
from services.tools.registry import get_tool_registry
from services.tools.builtin.sys_audit import (
    count_entries as count_sys_audit_entries,
    filter_entries as filter_sys_audit_entries,
    find_suspicious_entries,
    log_judge_pre_action,
    load_entries as load_sys_audit_entries,
    summarize_entries as summarize_sys_audit_entries,
)
from services.workspace import (
    get_workspace_status,
    list_workspace_artifacts,
    persist_governance_decision,
)
from services.vision import normalize_image_attachments

_CHAT_STREAM_LOGGER = logging.getLogger("liara.api.chat_stream")
_CHAT_RUN_LOGGER = logging.getLogger("liara.api.chat_run")
_ARTIFACT_LOGGER = logging.getLogger("liara.api.artifacts")


async def _fetch_heartbeat_operations(
    *,
    base_url: str,
    window_seconds: int,
    timeout_seconds: float,
) -> HeartbeatOperationsResponse:
    """Fetch the independent heartbeat instance without exposing its port to clients."""
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        health_response, heartbeat_response, curve_response = await asyncio.gather(
            client.get("/health"),
            client.get("/v1/heartbeat"),
            client.get("/v1/curve", params={"window_seconds": window_seconds}),
        )
    health_response.raise_for_status()
    heartbeat_response.raise_for_status()
    curve_response.raise_for_status()
    return HeartbeatOperationsResponse(
        status="success",
        service_health=health_response.json(),
        heartbeat=HeartbeatSnapshot.model_validate(heartbeat_response.json()),
        curve=StateCurve.model_validate(curve_response.json()),
    )


async def _fetch_self_observer_operations(
    *,
    base_url: str,
    history_limit: int,
    timeout_seconds: float,
) -> SelfObserverOperationsResponse:
    """Fetch the independent observer without granting it API mutation rights."""
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        health_response, state_response, history_response, inspection_response = await asyncio.gather(
            client.get("/health"),
            client.get("/v1/state"),
            client.get("/v1/history", params={"limit": history_limit}),
            client.get("/v1/inspection"),
        )
    health_response.raise_for_status()
    state_response.raise_for_status()
    history_response.raise_for_status()
    inspection_response.raise_for_status()
    return SelfObserverOperationsResponse(
        status="success",
        service_health=health_response.json(),
        state=SystemStateEnvelope.model_validate(state_response.json()),
        history=[SystemStateEnvelope.model_validate(item) for item in history_response.json()],
        inspection=SelfInspectionDecision.model_validate(inspection_response.json()),
    )

_NON_PUBLIC_TOOL_NAMES = frozenset({
    "compute.run",
    "compute.generate",
    "read_file",
    "list_files",
    "web_search",
})

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


def _is_public_tool_name(tool_name: str) -> bool:
    return str(tool_name or "").strip() not in _NON_PUBLIC_TOOL_NAMES


class SessionResponse(BaseModel):
    """Session snapshot exposed by GET /session."""

    session_id: str
    user_id: str
    message_count: int
    last_run_id: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolInvokeRequest(BaseModel):
    """Payload for manual tool invocation endpoints."""

    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    simulation_mode: bool = False


class SysToolProposalRequest(BaseModel):
    """Submit a governance proposal for a sys tool invocation."""

    command: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    capability: str | None = None
    rationale: str | None = None
    requested_by: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    source: str | None = None
    context: str | None = None
    max_invocations: int = Field(default=1, ge=1, le=10)


class SysToolProposalDecisionRequest(BaseModel):
    """Approve or reject a pending sys governance proposal."""

    proposal_id: str
    decision: Literal["approved", "rejected"]
    decided_by: str
    decision_reason: str
    request_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    source: str | None = None
    context: str | None = None


class SysToolProposalActionRequest(BaseModel):
    """Apply an approved proposal or compensate one reversible mutation."""

    proposal_id: str
    action: Literal["apply", "rollback"]
    acted_by: str
    action_reason: str
    request_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    source: str | None = None
    context: str | None = None


def _policy_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _evaluate_sys_policy(command: str) -> dict[str, Any]:
    normalized = (command or "").strip().lower()
    blocked_tokens = ("rm", "del", "remove-item", "shutdown", "reboot", "format")
    network_tokens = ("curl", "invoke-webrequest", "wget")
    mutation_tokens = ("tee", "mkdir", "touch", "cp", "mv", "venv-pip")

    reasons: list[str] = []
    allowed = True
    risk_level = "low"

    if any(token in normalized for token in blocked_tokens):
        allowed = False
        risk_level = "high"
        reasons.append("blocked_command_family")
    elif any(token in normalized for token in mutation_tokens):
        risk_level = "high"
        reasons.append("mutation_requires_review")
    elif any(token in normalized for token in network_tokens):
        risk_level = "medium"
        reasons.append("network_command_requires_review")

    return {
        "allowed": allowed,
        "risk_level": risk_level,
        "reasons": reasons,
    }


def _sys_governance_store_path() -> Path:
    configured = str(os.getenv("LIARA_SYS_GOVERNANCE_STORE_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "services" / "sys_governance_proposals.json"


def _load_sys_governance_proposals(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        proposal_id = str(value.get("proposal_id") or key).strip()
        if not proposal_id:
            continue
        normalized[proposal_id] = value
    return normalized


def _persist_sys_governance_proposals(path: Path, proposals: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(proposals, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _sys_governance_events_path() -> Path:
    configured = str(os.getenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "services" / "sys_governance_events.jsonl"


def _sys_governance_rollback_path(proposals_path: Path, proposal_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", proposal_id).strip("._")
    if not safe_id:
        raise ValueError("rollback proposal id is invalid")
    return proposals_path.parent / "sys_governance_rollback" / f"{safe_id}.json"


def _persist_sys_governance_rollback_snapshot(
    proposals_path: Path,
    proposal_id: str,
    *,
    target_path: str,
    content: str,
) -> dict[str, Any]:
    payload_bytes = content.encode("utf-8")
    max_bytes = max(1, int(os.getenv("LIARA_SYS_ROLLBACK_MAX_BYTES", str(64 * 1024))))
    if len(payload_bytes) > max_bytes:
        raise ValueError(f"rollback snapshot exceeds {max_bytes} bytes")
    artifact_path = _sys_governance_rollback_path(proposals_path, proposal_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "target_path": target_path,
        "content": content,
        "size_bytes": len(payload_bytes),
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    temporary = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(artifact_path)
    return {
        "schema_version": 1,
        "artifact_path": str(artifact_path),
        "target_path": target_path,
        "size_bytes": payload["size_bytes"],
        "sha256": payload["sha256"],
        "captured_at": payload["captured_at"],
    }


def _load_sys_governance_rollback_snapshot(
    proposals_path: Path,
    proposal_id: str,
    reference: dict[str, Any],
) -> dict[str, Any]:
    artifact_path = Path(str(reference.get("artifact_path") or ""))
    expected_path = _sys_governance_rollback_path(proposals_path, proposal_id)
    if artifact_path.resolve() != expected_path.resolve():
        raise ValueError("rollback snapshot artifact path mismatch")
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported rollback snapshot schema")
    content = str(payload.get("content") or "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if digest != str(reference.get("sha256") or "") or digest != str(payload.get("sha256") or ""):
        raise ValueError("rollback snapshot digest mismatch")
    if str(payload.get("target_path") or "") != str(reference.get("target_path") or ""):
        raise ValueError("rollback snapshot target mismatch")
    return payload


def _reversible_sys_target(parameters: dict[str, Any]) -> tuple[str | None, str]:
    command = str(parameters.get("command") or "").strip().lower()
    if command != "tee":
        return None, "only tee overwrite of an existing workspace file is reversible"
    if str(parameters.get("write_mode") or "overwrite").strip().lower() == "append":
        return None, "append mutations are not reversible"
    target_path = str(parameters.get("target_path") or "").strip()
    workspace_root = os.getenv("LIARA_AGENT_WORKSPACE_ROOT", "/home/liara/workspace").rstrip("/")
    try:
        target = PurePosixPath(target_path)
        root = PurePosixPath(workspace_root)
        target.relative_to(root)
    except (TypeError, ValueError):
        return None, "target is outside the managed workspace root"
    if target == root:
        return None, "workspace root is not a reversible file target"
    if str(parameters.get("storage_scope") or "") != "wsl_workspace":
        return None, "rollback requires storage_scope=wsl_workspace"
    return str(target), ""


def _append_sys_governance_event(path: Path, event: dict[str, Any]) -> None:
    payload = dict(event)
    payload.setdefault("timestamp", datetime.now(UTC).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        handle.write("\n")


def _load_sys_governance_events(path: Path, *, proposal_id: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-5000:]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if proposal_id and str(event.get("proposal_id") or "") != proposal_id:
            continue
        events.append(event)
    return events


class SessionUpdateRequest(BaseModel):
    """Payload for creating or updating session-scoped metadata."""

    session_id: str
    user_id: str
    sandbox_root: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileUploadResponse(BaseModel):
    """Response for uploaded files that can be attached to later chat turns."""

    attachment: dict[str, Any]
    scan: dict[str, Any]


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


def _attachment_storage_root(sandbox_root: str, session_id: str) -> Path:
    return Path(sandbox_root) / ".liara_uploads" / session_id


def _sanitize_upload_name(name: str) -> str:
    candidate = Path(name or "upload.bin").name.strip()
    return candidate or "upload.bin"


def _sanitize_session_segment(value: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "session").strip())
    return candidate.strip("._") or "session"


def _resolve_effective_sandbox_root(requested_root: str | None, snapshot: dict[str, Any]) -> str:
    existing_root = snapshot.get("metadata", {}).get("sandbox_root")
    candidate = requested_root if requested_root is not None else existing_root
    return canonicalize_sandbox_root(candidate)


class SpeechGenerationRequest(BaseModel):
    """Browser-facing request for a session-scoped speech artifact."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)
    sandbox_root: str | None = None
    speaker_profile: str = Field(default="gentle-feminine-v1", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    max_audio_tokens: int = Field(default=100, ge=25, le=400)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class SpeechStreamRequest(SpeechGenerationRequest):
    """Browser-facing request for negotiated, cancellable speech delivery."""

    codec: Literal["pcm_s16le", "webm_opus", "ogg_opus"] = "webm_opus"
    persist_artifact: bool = False


def _normalize_display_name(value: str | None) -> str | None:
    candidate = (value or "").strip()
    return candidate or None


def _extract_identity_display_name(request: ChatRequest, snapshot: dict[str, Any]) -> str | None:
    from_request = _normalize_display_name(request.display_name)
    if from_request:
        return from_request
    metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else None
    if isinstance(metadata, dict):
        return _normalize_display_name(str(metadata.get("display_name") or ""))
    return None


def _identity_prompt_block(*, user_id: str, display_name: str | None) -> str:
    if not display_name:
        return ""
    return (
        "Identity context (trusted session metadata):\n"
        f"- user_id: {user_id}\n"
        f"- display_name: {display_name}"
    )


def _attachment_prompt_block(attachments: list[ChatAttachment]) -> str:
    if not attachments:
        return ""

    max_chars = max(200, int(os.getenv("LIARA_ATTACHMENT_TEXT_CHAR_LIMIT", "12000")))
    chunks: list[str] = []
    remaining = max_chars

    for index, attachment in enumerate(attachments, start=1):
        details: list[str] = []
        if attachment.name:
            details.append(f"name={attachment.name}")
        if attachment.media_type:
            details.append(f"media_type={attachment.media_type}")
        if attachment.size_bytes is not None:
            details.append(f"size_bytes={attachment.size_bytes}")
        if attachment.source:
            details.append(f"source={attachment.source}")

        header = f"[Attachment {index}"
        if details:
            header += f": {', '.join(details)}"
        header += "]"

        text_content = (attachment.text_content or "").strip()
        if text_content and remaining > 0:
            excerpt = text_content[:remaining]
            remaining -= len(excerpt)
            if excerpt != text_content:
                excerpt += "\n[attachment text truncated]"
            chunks.append(f"{header}\n{excerpt}")
        elif str(attachment.media_type or "").startswith("image/") and attachment.content_base64:
            chunks.append(f"{header}\nImage payload provided to the canonical vision path; binary omitted from prompt.")
        elif attachment.content_url:
            chunks.append(f"{header}\nBinary or remote attachment provided; content URL omitted from prompt.")
        else:
            chunks.append(f"{header}\nAttachment metadata provided without inline text content.")

        if remaining <= 0:
            break

    return "\n\nBereitgestellte Dateien/Anhänge:\n" + "\n\n".join(chunks)


def _build_effective_chat_query(
    message: str,
    attachments: list[ChatAttachment],
    *,
    identity_prompt_block: str = "",
) -> str:
    message_text = (message or "").strip()
    attachment_block = _attachment_prompt_block(attachments)
    sections: list[str] = []
    identity_text = (identity_prompt_block or "").strip()
    if identity_text:
        sections.append(identity_text)
    if message_text:
        sections.append(message_text)
    if attachment_block:
        sections.append(attachment_block.strip())
    return "\n\n".join(sections).strip()


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


def _extract_chat_artifacts(orchestrator_result: Any, *, session_id: str | None = None) -> list[dict[str, Any]] | None:
    """Return normalized artifacts list from orchestrator result.

    Preference order:
    1) `orchestrator_result.artifacts`
    2) fallback extraction from `tool_results`
    """
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

        stored_path = str(metadata.get("stored_path") or "").strip()
        if not stored_path:
            continue

        effective_session = str(metadata.get("session_id") or session_id or "").strip()
        if not effective_session:
            continue

        artifact["url"] = (
            f"/files/artifact?session_id={quote(effective_session, safe='')}&path={quote(stored_path, safe='')}"
        )
        _ARTIFACT_LOGGER.debug(f"Generated artifact URL: kind={artifact.get('kind')}, title={artifact.get('title')}, url_path={stored_path}")


def _dreaming_assurance_projection(proposal: dict[str, Any]) -> dict[str, Any]:
    metadata = proposal.get("metadata") if isinstance(proposal.get("metadata"), dict) else {}
    validator_evidence = next(
        (
            item
            for item in reversed(list(proposal.get("evidence") or []))
            if isinstance(item, dict) and item.get("source") == "validator_report"
        ),
        None,
    )
    evidence_metadata = (
        validator_evidence.get("metadata")
        if isinstance(validator_evidence, dict) and isinstance(validator_evidence.get("metadata"), dict)
        else {}
    )
    verdict = str(metadata.get("assurance_verdict") or evidence_metadata.get("verdict") or "pending")
    if verdict not in {"pending", "passed", "attention", "failed"}:
        verdict = "pending"
    artifacts = metadata.get("assurance_artifacts") or evidence_metadata.get("artifacts") or []
    artifact_refs = [
        {"path": str(path), "kind": "validator_report"}
        for path in dict.fromkeys(str(path) for path in artifacts if str(path).strip())
    ]
    required = bool(metadata.get("assurance_required", False))
    decision = str(proposal.get("decision") or "pending")
    return {
        "required": required,
        "verdict": verdict,
        "blocked": required and decision == "pending" and verdict != "passed",
        "validator_job_id": metadata.get("assurance_job_id") or (
            validator_evidence.get("reference") if isinstance(validator_evidence, dict) else None
        ),
        "findings_count": int(metadata.get("assurance_findings_count") or evidence_metadata.get("findings_count") or 0),
        "highest_severity": evidence_metadata.get("highest_severity") or "none",
        "artifacts": artifact_refs,
        "audit_reference": {
            "operation": "dreaming_attach_assurance",
            "proposal_id": proposal.get("proposal_id"),
        },
    }


_SYS_GOVERNANCE_RUNTIME_PARAMETER_KEYS = {
    "proposal_id",
    "request_id",
    "run_id",
    "session_id",
    "source",
    "context",
}


def _sys_governed_parameters(command: str, parameters: dict[str, Any]) -> dict[str, Any]:
    governed = {
        str(key): value
        for key, value in dict(parameters or {}).items()
        if str(key) not in _SYS_GOVERNANCE_RUNTIME_PARAMETER_KEYS and str(key) != "command"
    }
    return {"command": str(command or "").strip(), **governed}


def _sys_governance_invocation_digest(command: str, parameters: dict[str, Any]) -> str:
    payload = _sys_governed_parameters(command, parameters)
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dreaming_quality_projection(proposal: dict[str, Any]) -> dict[str, Any]:
    quality_evidence = next(
        (
            item
            for item in reversed(list(proposal.get("evidence") or []))
            if isinstance(item, dict) and item.get("source") == "proposal_quality_signals"
        ),
        None,
    )
    metadata = (
        quality_evidence.get("metadata")
        if isinstance(quality_evidence, dict) and isinstance(quality_evidence.get("metadata"), dict)
        else {}
    )
    complexity = metadata.get("complexity") if isinstance(metadata.get("complexity"), dict) else {}
    coverage = metadata.get("coverage") if isinstance(metadata.get("coverage"), dict) else {}
    level = str(complexity.get("level") or "unavailable")
    if level not in {"low", "moderate", "high"}:
        level = "unavailable"
    coverage_status = str(coverage.get("status") or "unavailable")
    if coverage_status not in {"measured", "not_applicable"}:
        coverage_status = "unavailable"

    def _optional_ratio(value: Any) -> float | None:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            return None
        return round(max(0.0, min(ratio, 1.0)), 3)

    def _non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    return {
        "available": quality_evidence is not None,
        "schema_version": _non_negative_int(metadata.get("schema_version")) or None,
        "interpretation": str(metadata.get("interpretation") or "validator_evidence_only"),
        "complexity": {
            "score": _optional_ratio(complexity.get("score")),
            "level": level,
            "character_count": _non_negative_int(complexity.get("character_count")),
            "line_count": _non_negative_int(complexity.get("line_count")),
            "declared_source_count": _non_negative_int(complexity.get("declared_source_count")),
            "evidence_count": _non_negative_int(complexity.get("evidence_count")),
            "accepted_relation_count": _non_negative_int(complexity.get("accepted_relation_count")),
        },
        "coverage": {
            "status": coverage_status,
            "source_coverage_ratio": _optional_ratio(coverage.get("source_coverage_ratio")),
            "relation_coverage_ratio": _optional_ratio(coverage.get("relation_coverage_ratio")),
            "uncovered_source_ids": [str(value) for value in list(coverage.get("uncovered_source_ids") or [])],
            "relation_uncovered_source_ids": [
                str(value) for value in list(coverage.get("relation_uncovered_source_ids") or [])
            ],
        },
    }


def _build_public_stream_final_payload(chat_response: ChatResponse) -> dict[str, Any]:
    """Build a public-safe final payload for SSE clients.

    Keeps user-relevant metadata while omitting internal debug blocks that are
    useful for operators but should not be streamed to clients.
    """
    meta = dict(chat_response.metadata or {})
    public_meta: dict[str, Any] = {}

    for key in (
        "state_final",
        "validation",
        "context_debug",
        "reasoning_metrics",
        "attachment_count",
        "attachments",
        "attachment_scan_results",
        "sanitizer",
    ):
        if key in meta:
            public_meta[key] = meta[key]

    # Strip verbose/internal fields from context debug even in public metadata.
    # Keep compression metadata for control strategy measurement.
    context_debug = public_meta.get("context_debug")
    if isinstance(context_debug, dict):
        public_meta["context_debug"] = {
            "mode": context_debug.get("mode"),
            "sources": context_debug.get("sources", {}),
            "compression": context_debug.get("compression", {}),
        }

    payload: dict[str, Any] = {
        "run_id": chat_response.run_id,
        "response": chat_response.response,
        "tools_used": list(chat_response.tools_used or []),
        "tool_outputs": dict(chat_response.tool_outputs or {}),
        "llm_provider": chat_response.llm_provider,
        "llm_model": chat_response.llm_model,
        "ttft_ms": chat_response.ttft_ms,
        "gen_ms": chat_response.gen_ms,
        "validation_passed": bool(chat_response.validation_passed),
        "metadata": public_meta,
    }

    if chat_response.artifacts is not None:
        payload["artifacts"] = [
            artifact.model_dump() if hasattr(artifact, "model_dump") else artifact
            for artifact in chat_response.artifacts
        ]

    if chat_response.pending_tool_calls is not None:
        payload["pending_tool_calls"] = [
            call.model_dump() if hasattr(call, "model_dump") else call
            for call in chat_response.pending_tool_calls
        ]

    return payload


async def _get_session_snapshot_best_effort(
    adapter: MemoryServiceAdapter,
    session_id: str,
) -> dict[str, Any]:
    try:
        snapshot = await adapter.get(MemoryTier.SESSION, f"session:{session_id}", default={})
    except (MemoryError, NotImplementedError):
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


async def _write_session_snapshot(
    adapter: MemoryServiceAdapter,
    session_id: str,
    user_id: str,
    updated_at: str,
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
                "updated_at": updated_at,
                "metadata": merged_metadata,
            },
            ttl_seconds=SessionStore.DEFAULT_TTL_SECONDS,
        )
    except (MemoryError, NotImplementedError):
        pass  # session snapshot is best-effort; not supported by RemoteMemoryAdapter


def _build_session_response(
    session_id: str,
    user_id: str,
    snapshot: dict[str, Any],
    history_status: str,
    message_count: int,
) -> SessionResponse:
    metadata = dict(snapshot.get("metadata") or {})
    metadata["history_status"] = history_status
    return SessionResponse(
        session_id=session_id,
        user_id=snapshot.get("user_id") or user_id,
        message_count=message_count,
        last_run_id=snapshot.get("last_run_id"),
        updated_at=snapshot.get("updated_at"),
        metadata=metadata,
    )


def _json_etag(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    return f'W/"{digest}"'


def _cacheable_json_response(
    payload: Any,
    request: Request,
    *,
    cache_control: str,
) -> Response:
    etag = _json_etag(payload)
    headers = {
        "Cache-Control": cache_control,
        "ETag": etag,
        "Vary": "Accept",
    }

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    return JSONResponse(content=payload, headers=headers)


def _cors_allowed_origins() -> list[str]:
    configured = os.getenv("LIARA_API_CORS_ALLOW_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]

    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]


def _safe_store(factory, fallback):
    """Build a store and degrade gracefully when local services are unavailable."""
    try:
        return factory()
    except Exception:
        return fallback


def _python_dependency_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _redis_backend_usable() -> bool:
    return bool(Settings.REDIS_URL) and _python_dependency_available("redis")


def _postgres_backend_usable() -> bool:
    return bool(Settings.POSTGRES_URL) and _python_dependency_available("psycopg2")


def _qdrant_backend_usable() -> bool:
    return bool(Settings.QDRANT_URL) and _python_dependency_available("qdrant_client")


def _neo4j_backend_usable() -> bool:
    return bool(Settings.NEO4J_URL) and _python_dependency_available("neo4j")


def create_default_memory_adapter() -> MemoryServiceAdapter:
    """Create memory adapter according to MEMORY_MODE with safe local fallbacks.
    
    Priority:
    1. service mode if MEMORY_SERVICE_BASE_URL is set
    2. postgres mode: Use Postgres + Redis + Qdrant if URLs are configured
    3. in_process fallback: RAM-only EphemeralMemoryStore for all tiers
    """
    selected_mode = (getattr(Settings, "MEMORY_MODE", "in_process") or "in_process").strip().lower()
    
    # Remote service adapter (highest priority)
    if selected_mode == "service" and Settings.MEMORY_SERVICE_BASE_URL:
        return RemoteMemoryAdapter(
            Settings.MEMORY_SERVICE_BASE_URL,
            timeout_seconds=Settings.MEMORY_SERVICE_TIMEOUT_SECONDS,
        )

    # Postgres mode: Use real storage backends (Postgres/Redis/Qdrant)
    if selected_mode == "postgres":
        # SESSION tier: Redis (fallback to ephemeral if not configured)
        session_store = EphemeralMemoryStore()
        if _redis_backend_usable():
            session_store = _safe_store(SessionStore, session_store)
        
        # PERSISTENT tier: Postgres (fallback to ephemeral if not configured)
        fact_store = EphemeralMemoryStore()
        if _postgres_backend_usable():
            fact_store = _safe_store(FactStore, fact_store)
        
        # RETRIEVAL tier: Qdrant (fallback to ephemeral if not configured)
        retrieval_store = EphemeralMemoryStore()
        if _qdrant_backend_usable():
            retrieval_store = _safe_store(RetrievalIndex, retrieval_store)

        # PATTERN tier: Neo4j graph store (fallback to null if not configured)
        graph_store = NullMemoryStore()
        if _neo4j_backend_usable():
            graph_store = _safe_store(GraphStore, graph_store)
        
        memory_layer = MemoryLayer(
            session_store=session_store,
            fact_store=fact_store,
            retrieval_index=retrieval_store,
            graph_store=graph_store,
        )
        return InProcessMemoryAdapter(memory_layer)

    # Default in_process mode: RAM-only ephemeral stores for all tiers
    session_store = EphemeralMemoryStore()
    fact_store = EphemeralMemoryStore()
    retrieval_store = EphemeralMemoryStore()

    memory_layer = MemoryLayer(
        session_store=session_store,
        fact_store=fact_store,
        retrieval_index=retrieval_store,
        graph_store=NullMemoryStore(),
    )
    return InProcessMemoryAdapter(memory_layer)


def create_default_orchestrator(memory_adapter: MemoryServiceAdapter) -> Orchestrator:
    """Create orchestrator with production defaults."""
    return Orchestrator(
        tool_coordinator=ToolCoordinator(),
        inference_gateway=InferenceGateway(),
        memory_layer=memory_adapter,
    )


async def _maybe_close(value: Any) -> None:
    close_fn = getattr(value, "close", None)
    if not callable(close_fn):
        return
    result = close_fn()
    if inspect.isawaitable(result):
        await result


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


def create_api_app(
    orchestrator: Any | None = None,
    memory_adapter: MemoryServiceAdapter | None = None,
    tts_adapter: TtsServiceAdapter | None = None,
) -> FastAPI:
    """Build FastAPI app with /chat, /history and /session endpoints."""
    adapter = memory_adapter or create_default_memory_adapter()
    orch = orchestrator or create_default_orchestrator(adapter)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        logger = logging.getLogger("liara.api.startup")
        manager = None
        if Settings.LLAMA_CPP_MANAGED_BY_API:
            from services.inference.llama_cpp_server import get_llama_cpp_server_manager

            manager = get_llama_cpp_server_manager()
            try:
                logger.info("[API STARTUP] Starting llama-server for ll_ol_fallback provider...")
                logger.info("[API STARTUP] (Large models may take 1-2+ minutes to load into GPU memory)")
                started = await manager.start(verbose=True)
                if started:
                    logger.info("[API STARTUP] ✓ llama-server ready (primary ll_ol_fallback active)")
                else:
                    logger.warning("[API STARTUP] ⚠ llama-server startup timeout (ollama fallback will handle requests)")
            except Exception as e:
                logger.error(f"[API STARTUP] ✗ llama-server startup error: {e}", exc_info=True)
        else:
            logger.info("[API STARTUP] llama-server lifecycle management disabled")
        
        # Yield to run the app
        yield
        
        if manager is not None:
            try:
                logger.info("[API SHUTDOWN] Stopping llama-server...")
                await manager.stop(verbose=True)
                logger.info("[API SHUTDOWN] ✓ llama-server stopped")
            except Exception as e:
                logger.error(f"[API SHUTDOWN] llama-server stop error: {e}", exc_info=True)
        
        await _maybe_close(adapter)

    app = FastAPI(title="liara-api", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.memory_adapter = adapter
    app.state.orchestrator = orch
    app.state.tts_adapter = tts_adapter or TtsServiceAdapter(
        timeout_seconds=float(os.getenv("LIARA_TTS_TIMEOUT_SECONDS", "360"))
    )
    app.state.sys_tool_proposals_path = _sys_governance_store_path()
    app.state.sys_tool_events_path = _sys_governance_events_path()
    app.state.sys_tool_proposals = _load_sys_governance_proposals(app.state.sys_tool_proposals_path)
    app.state.sys_tool_governance_lock = asyncio.Lock()
    tool_registry = get_tool_registry()
    tool_coordinator = ToolCoordinator()
    output_sanitizer = OutputSanitizer()

    def _tts_http_exception(exc: TtsAdapterError) -> HTTPException:
        status_code = exc.status_code if exc.status_code and 400 <= exc.status_code < 600 else 502
        return HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        )

    @app.get("/speech/health", response_model=TtsHealthResponse)
    async def speech_health() -> TtsHealthResponse:
        try:
            return await app.state.tts_adapter.health()
        except TtsAdapterError as exc:
            raise _tts_http_exception(exc) from exc

    @app.post("/speech/generate", response_model=ChatArtifact)
    async def generate_speech(request: SpeechGenerationRequest) -> ChatArtifact:
        snapshot = await _get_session_snapshot_best_effort(adapter, request.session_id)
        sandbox_root = _resolve_effective_sandbox_root(request.sandbox_root, snapshot)
        generation_request = TtsGenerationRequest(
            text=request.text,
            speaker_profile=request.speaker_profile,
            max_audio_tokens=request.max_audio_tokens,
            seed=request.seed,
        )
        try:
            return await app.state.tts_adapter.generate_artifact(
                generation_request,
                session_id=request.session_id,
                sandbox_root=sandbox_root,
                title="LIARA response",
            )
        except TtsAdapterError as exc:
            raise _tts_http_exception(exc) from exc

    @app.post("/speech/stream")
    async def stream_speech(request: SpeechStreamRequest) -> StreamingResponse:
        generation_request = TtsGenerationRequest(
            text=request.text,
            speaker_profile=request.speaker_profile,
            max_audio_tokens=request.max_audio_tokens,
            seed=request.seed,
        )
        encoder_path = None
        if request.codec != "pcm_s16le":
            try:
                encoder_path = resolve_ffmpeg_path()
            except AudioStreamEncodingError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "speech_codec_unavailable",
                        "message": str(exc),
                        "retryable": False,
                    },
                ) from exc
        artifact_sink = None
        if request.persist_artifact:
            snapshot = await _get_session_snapshot_best_effort(adapter, request.session_id)
            sandbox_root = _resolve_effective_sandbox_root(request.sandbox_root, snapshot)
            artifact_sink = prepare_pcm_stream_artifact(
                session_id=request.session_id,
                sandbox_root=sandbox_root,
            )
        try:
            upstream = await app.state.tts_adapter.open_stream(generation_request)
        except TtsAdapterError as exc:
            raise _tts_http_exception(exc) from exc

        if request.codec == "pcm_s16le" and artifact_sink is None:
            async def direct_pcm_body():
                try:
                    async for chunk in upstream.iter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()

            return StreamingResponse(
                direct_pcm_body(),
                media_type=codec_media_type(request.codec),
                headers={
                    "X-Liara-TTS-Request-Id": upstream.request_id,
                    "X-Liara-TTS-Stream-Contract": "audio_stream/v1",
                    "X-Liara-TTS-Codec": request.codec,
                    "X-Liara-TTS-Source-Sample-Rate": str(upstream.sample_rate),
                    "X-Liara-TTS-Sample-Rate": str(upstream.sample_rate),
                    "X-Liara-TTS-Channels": str(upstream.channels),
                    "X-Liara-TTS-Mode": upstream.mode,
                    "Cache-Control": "private, no-store",
                },
            )

        pcm_complete = False

        async def pcm_source():
            nonlocal pcm_complete
            try:
                async for chunk in upstream.iter_bytes():
                    if artifact_sink is not None:
                        artifact_sink.write(chunk)
                    yield chunk
                pcm_complete = True
            except BaseException:
                raise

        encoded_stream = encode_audio_stream(
            pcm_source(),
            codec=request.codec,
            ffmpeg_path=encoder_path,
        )
        response_complete = False

        async def encoded_body():
            nonlocal response_complete
            try:
                async for chunk in encoded_stream:
                    yield chunk
                response_complete = True
            finally:
                await encoded_stream.aclose()
                await upstream.aclose()
                if artifact_sink is not None:
                    if pcm_complete and response_complete:
                        try:
                            artifact_sink.commit()
                        except Exception as exc:
                            _ARTIFACT_LOGGER.error(
                                "Speech stream artifact commit failed: %s", type(exc).__name__
                            )
                    else:
                        artifact_sink.abort()

        headers = {
            "X-Liara-TTS-Request-Id": upstream.request_id,
            "X-Liara-TTS-Stream-Contract": "audio_stream/v1",
            "X-Liara-TTS-Codec": request.codec,
            "X-Liara-TTS-Source-Sample-Rate": str(upstream.sample_rate),
            "X-Liara-TTS-Sample-Rate": str(codec_sample_rate(request.codec)),
            "X-Liara-TTS-Channels": str(upstream.channels),
            "X-Liara-TTS-Mode": upstream.mode,
            "Cache-Control": "private, no-store",
        }
        if artifact_sink is not None:
            headers.update(
                {
                    "X-Liara-TTS-Artifact-URL": artifact_sink.url,
                    "X-Liara-TTS-Artifact-Format": "wav",
                    "X-Liara-TTS-Artifact-Commit": "on-complete",
                }
            )

        return StreamingResponse(
            encoded_body(),
            media_type=codec_media_type(request.codec),
            headers=headers,
        )

    def _sync_sys_governance_store() -> dict[str, dict[str, Any]]:
        proposals = load_sys_governance_proposals(app.state.sys_tool_proposals_path)
        app.state.sys_tool_proposals = proposals
        return proposals

    async def _run_chat(
        request: ChatRequest,
        *,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ):
        run_started = time.perf_counter()
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        api_timings_ms: dict[str, float] = {}
        attachments = list(request.attachments or [])
        snapshot = await _get_session_snapshot_best_effort(adapter, request.session_id)
        identity_display_name = _extract_identity_display_name(request, snapshot)
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
        is_harmful_user_query = _is_harmful_user_query(request.message)
        history_user_content = request.message
        history_user_metadata: dict[str, Any] = {
            "source": "liara-api",
            **_attachment_history_metadata(attachments),
        }
        if identity_display_name:
            history_user_metadata["display_name"] = identity_display_name
        if is_harmful_user_query:
            # Avoid storing raw harmful prompts in session history to reduce recall contamination.
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

        if is_harmful_user_query:
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
                pending_tool_calls=None,
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
        orchestrator_result = await orch.run(
            OrchestratorRequest(
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
                preferred_provider=request.preferred_provider,
                preferred_model=request.preferred_model,
                request_source=request.request_source,
                risk_reassessment=request.risk_reassessment,
                sandbox_root=effective_sandbox_root,
                user_feedback_score=request.user_feedback_score,
                user_feedback_stars=request.user_feedback_stars,
            )
        )
        api_timings_ms["orchestration"] = round((time.perf_counter() - orchestration_started) * 1000, 3)
        context_debug = orchestrator_result.llm_generation.get("context_debug", {})
        sanitization_started = time.perf_counter()
        sanitization = output_sanitizer.sanitize(orchestrator_result.final_response)
        public_response_text = sanitization.text
        api_timings_ms["output_sanitization"] = round((time.perf_counter() - sanitization_started) * 1000, 3)
        validation_result = dict(orchestrator_result.validation_result or {})
        validation_decision = str(validation_result.get("decision") or "").strip().lower()
        validation_risk_flags = list(validation_result.get("risk_flags") or [])
        safety_blocked_post = False
        query_harmful = _is_harmful_user_query(request.message)
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
        await _emit_stream_progress(
            progress_callback,
            stage="orchestration_complete",
            message="Response generated and validated",
            run_id=run_id,
            session_id=request.session_id,
            user_id=request.user_id,
            context_mode=context_debug.get("mode"),
            context_sources=context_debug.get("sources", {}),
            tools_used=orchestrator_result.tools_executed,
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
            pass  # session snapshot is best-effort; chat response is already complete
        # Emit this signal only when prior session memory influenced the answer.
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

        run_debug = {
            "api_timings_ms": api_timings_ms,
            "selected_tools": list(orchestrator_result.tools_executed),
            "failed_tools": [],
        }
        for transition in orchestrator_result.execution_trace:
            if transition.get("to") == "tool_execution":
                metadata = transition.get("metadata") or {}
                run_debug["executor_debug"] = dict(metadata.get("executor_debug") or {})
                run_debug["failed_tools"] = list((metadata.get("executor_debug") or {}).get("failed_tools", []))
            elif transition.get("to") == "tool_selection":
                metadata = transition.get("metadata") or {}
                run_debug["route_debug"] = dict(metadata.get("route_debug") or {})
                route_metadata = dict((metadata.get("route_debug") or {}).get("metadata") or {})
                if route_metadata.get("semantic_routing"):
                    run_debug["semantic_route"] = {
                        "intent": route_metadata.get("semantic_intent"),
                        "score": route_metadata.get("semantic_score"),
                        "scores": dict(route_metadata.get("semantic_scores") or {}),
                        "thresholds": dict(route_metadata.get("semantic_thresholds") or {}),
                    }
            elif transition.get("to") == "llm_generation":
                metadata = transition.get("metadata") or {}
                run_debug["prompt_debug"] = dict(metadata.get("prompt_debug") or {})
                run_debug["context_debug"] = dict(metadata.get("context_debug") or context_debug)
            elif transition.get("to") == "validation":
                metadata = transition.get("metadata") or {}
                run_debug["validation_trace"] = {
                    "decision": metadata.get("decision"),
                    "issues": metadata.get("issues", []),
                    "timing_ms": metadata.get("timing_ms"),
                }
            elif transition.get("to") == "complete":
                metadata = transition.get("metadata") or {}
                run_debug["completion"] = dict(metadata)
                if isinstance(metadata.get("reasoning_metrics"), dict):
                    run_debug["reasoning_metrics"] = dict(metadata.get("reasoning_metrics") or {})

        if "context_debug" not in run_debug:
            run_debug["context_debug"] = dict(context_debug)

        _CHAT_RUN_LOGGER.info(
            "chat_run_complete run_id=%s session_id=%s total_ms=%s tools=%s validation=%s context_mode=%s output_sanitized=%s",
            run_id,
            request.session_id,
            total_ms,
            orchestrator_result.tools_executed,
            validation_result.get("decision"),
            context_debug.get("mode"),
            sanitization.changed,
        )
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

        chat_response = ChatResponse(
            run_id=run_id,
            response=public_response_text,
            tools_used=orchestrator_result.tools_executed,
            tool_outputs=orchestrator_result.tool_results,
            llm_provider=orchestrator_result.llm_generation.get("provider") or "unknown",
            llm_model=orchestrator_result.llm_generation.get("model") or "unknown",
            ttft_ms=orchestrator_result.llm_generation.get("ttft_ms"),
            gen_ms=orchestrator_result.llm_generation.get("gen_ms"),
            validation_passed=bool(validation_result.get("passed", False)),
            metadata={
                "state_final": orchestrator_result.state_final,
                "execution_trace": orchestrator_result.execution_trace,
                "validation": validation_result,
                "context_debug": orchestrator_result.llm_generation.get("context_debug", {}),
                "inference_metadata": orchestrator_result.llm_generation.get("inference_metadata", {}),
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
                "safety_blocked": safety_blocked_post,
                "safety_block_stage": "post_generation" if safety_blocked_post else None,
            },
            artifacts=typed_artifacts,
            pending_tool_calls=orchestrator_result.pending_tool_calls,
        )
        return run_id, chat_response

    @app.get("/health")
    async def health(request: Request) -> Response:
        payload = {
            "status": "ok",
            "service": "liara-api",
            "memory_mode": (getattr(Settings, "MEMORY_MODE", "in_process") or "in_process"),
            "backends_configured": {
                "postgres": _postgres_backend_usable(),
                "redis": _redis_backend_usable(),
                "qdrant": _qdrant_backend_usable(),
                "chroma": bool(Settings.CHROMA_HOST),
                "neo4j": _neo4j_backend_usable(),
                "embedding": bool(Settings.EMBEDDING_SERVICE_BASE_URL),
            },
        }
        return _cacheable_json_response(payload, request, cache_control="public, max-age=5, stale-while-revalidate=10")

    @app.get("/health/backends", response_model=MemoryHealthResponse)
    async def health_backends(response: Response) -> MemoryHealthResponse:
        response.headers["Cache-Control"] = "no-store"
        store = BackedMemoryServiceStore()
        try:
            return await store.health_backends()
        finally:
            await store.close()

    @app.post("/memory/relations/cleanup-expired", response_model=RelationCleanupExpiredResponse)
    async def memory_relations_cleanup_expired(request: RelationCleanupExpiredRequest, response: Response) -> RelationCleanupExpiredResponse:
        response.headers["Cache-Control"] = "no-store"
        adapter = getattr(app.state, "memory_adapter", None)
        if isinstance(adapter, RemoteMemoryAdapter):
            try:
                payload = await adapter._post_json("/relations/cleanup-expired", request.model_dump())
                return RelationCleanupExpiredResponse(**payload)
            except Exception as exc:
                return RelationCleanupExpiredResponse(
                    removed=0,
                    status=MemoryServiceStatus(
                        status="failed",
                        backend="memory-service",
                        degraded=True,
                        error=f"relation_cleanup_proxy_error: {exc}",
                    ),
                )

        store = BackedMemoryServiceStore()
        try:
            return await store.relation_cleanup_expired(request)
        finally:
            await store.close()

    @app.get("/operations/dreaming")
    async def operations_dreaming(
        response: Response,
        decision: Literal["pending", "approved", "rejected", "all"] = Query(default="all"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        """Read-only Dreaming/Staging operations snapshot for local UIs."""
        response.headers["Cache-Control"] = "no-store"
        request = MemoryDreamingProposalListRequest(decision=decision, limit=limit)
        adapter = getattr(app.state, "memory_adapter", None)

        try:
            if isinstance(adapter, RemoteMemoryAdapter):
                status_payload = await adapter._get_json("/dreaming/status")
                proposals_payload = await adapter._post_json("/dreaming/proposals", request.model_dump())
            else:
                store = BackedMemoryServiceStore()
                try:
                    status = await store.dreaming_status()
                    proposals = await store.dreaming_proposals(request)
                    status_payload = status.model_dump(mode="json")
                    proposals_payload = proposals.model_dump(mode="json")
                finally:
                    await store.close()
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"dreaming_operations_error: {exc}",
                "scheduler_enabled": False,
                "mode": "manual_only",
                "pending_staged_items": 0,
                "pending_proposals": 0,
                "proposal_count": 0,
                "proposals": [],
                "assurance": {
                    "required": 0,
                    "blocked": 0,
                    "verdicts": {"pending": 0, "passed": 0, "attention": 0, "failed": 0},
                },
                "quality_signals": {
                    "available": 0,
                    "complexity_levels": {"low": 0, "moderate": 0, "high": 0},
                },
            }

        proposals_items = []
        for raw_item in list(proposals_payload.get("items") or []):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            item["assurance"] = _dreaming_assurance_projection(item)
            item["quality_signals"] = _dreaming_quality_projection(item)
            proposals_items.append(item)
        pending_count = sum(1 for item in proposals_items if item.get("decision") == "pending")
        verdict_counts = {"pending": 0, "passed": 0, "attention": 0, "failed": 0}
        required_count = 0
        blocked_count = 0
        quality_available = 0
        complexity_levels = {"low": 0, "moderate": 0, "high": 0}
        for item in proposals_items:
            assurance = item["assurance"]
            verdict_counts[assurance["verdict"]] += 1
            required_count += int(assurance["required"])
            blocked_count += int(assurance["blocked"])
            quality = item["quality_signals"]
            quality_available += int(quality["available"])
            quality_level = quality["complexity"]["level"]
            if quality_level in complexity_levels:
                complexity_levels[quality_level] += 1
        return {
            "status": "success",
            "scheduler_enabled": bool(status_payload.get("scheduler_enabled", False)),
            "mode": status_payload.get("mode", "manual_only"),
            "last_run_id": status_payload.get("last_run_id"),
            "last_run_at": status_payload.get("last_run_at"),
            "last_run_state": status_payload.get("last_run_state", "idle"),
            "pending_staged_items": int(status_payload.get("pending_staged_items") or 0),
            "pending_proposals": int(status_payload.get("pending_proposals") or pending_count),
            "proposal_count": len(proposals_items),
            "proposals": proposals_items,
            "assurance": {
                "required": required_count,
                "blocked": blocked_count,
                "verdicts": verdict_counts,
            },
            "quality_signals": {
                "available": quality_available,
                "complexity_levels": complexity_levels,
            },
            "memory_status": status_payload.get("status") or proposals_payload.get("status"),
            "filters": {
                "decision": decision,
                "limit": limit,
            },
        }

    @app.get("/admin/sys-audit/summary")
    async def sys_audit_summary(
        response: Response,
        limit: int = Query(default=500, ge=1, le=5000),
        blocked_only: bool = Query(default=False),
        source: str | None = Query(default=None),
        risk_level: str | None = Query(default=None),
        command_family: str | None = Query(default=None),
        log_path: str | None = Query(default=None),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        audit_path = Path(log_path) if log_path else None
        entries = load_sys_audit_entries(audit_path, limit=limit)
        filtered = filter_sys_audit_entries(
            entries,
            blocked_only=blocked_only,
            source=source,
            risk_level=risk_level,
            command_family=command_family,
        )
        summary = summarize_sys_audit_entries(filtered)
        summary["available_entries"] = count_sys_audit_entries(audit_path)
        summary["inspected_entries"] = len(entries)
        summary["filtered_entries"] = len(filtered)
        return {
            "status": "success",
            "summary": summary,
            "filters": {
                "blocked_only": blocked_only,
                "source": source or "all",
                "risk_level": risk_level or "all",
                "command_family": command_family or "all",
                "limit": limit,
                "log_path": log_path,
            },
        }

    @app.get("/admin/sys-audit/suspicious")
    async def sys_audit_suspicious(
        response: Response,
        limit: int = Query(default=500, ge=1, le=5000),
        max_items: int = Query(default=30, ge=1, le=200),
        blocked_only: bool = Query(default=False),
        source: str | None = Query(default=None),
        risk_level: str | None = Query(default=None),
        command_family: str | None = Query(default=None),
        log_path: str | None = Query(default=None),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        entries = load_sys_audit_entries(Path(log_path) if log_path else None, limit=limit)
        filtered = filter_sys_audit_entries(
            entries,
            blocked_only=blocked_only,
            source=source,
            risk_level=risk_level,
            command_family=command_family,
        )
        suspicious = find_suspicious_entries(filtered, limit=max_items)
        return {
            "status": "success",
            "count": len(suspicious),
            "items": suspicious,
            "filters": {
                "blocked_only": blocked_only,
                "source": source or "all",
                "risk_level": risk_level or "all",
                "command_family": command_family or "all",
                "limit": limit,
                "max_items": max_items,
                "log_path": log_path,
            },
        }

    @app.get("/admin/sys-audit/presets/{preset_name}")
    async def sys_audit_preset(
        preset_name: str,
        response: Response,
        log_path: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=5000),
        max_items: int | None = Query(default=None, ge=1, le=200),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        presets: dict[str, dict[str, Any]] = {
            "top-risk": {
                "blocked_only": False,
                "source": "all",
                "risk_level": "high",
                "command_family": "all",
                "limit": 500,
                "max_items": 30,
            },
            "blocked-only": {
                "blocked_only": True,
                "source": "all",
                "risk_level": "all",
                "command_family": "all",
                "limit": 500,
                "max_items": 30,
            },
            "orchestrator-network-risk": {
                "blocked_only": False,
                "source": "orchestrator",
                "risk_level": "high",
                "command_family": "network",
                "limit": 800,
                "max_items": 40,
            },
        }

        preset_key = preset_name.strip().lower()
        if preset_key not in presets:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Unknown preset: {preset_name}",
                    "available_presets": sorted(presets.keys()),
                },
            )

        selected = dict(presets[preset_key])
        selected["limit"] = int(limit or selected["limit"])
        selected["max_items"] = int(max_items or selected["max_items"])

        entries = load_sys_audit_entries(Path(log_path) if log_path else None, limit=selected["limit"])
        filtered = filter_sys_audit_entries(
            entries,
            blocked_only=selected["blocked_only"],
            source=selected["source"],
            risk_level=selected["risk_level"],
            command_family=selected["command_family"],
        )

        summary = summarize_sys_audit_entries(filtered)
        summary["inspected_entries"] = len(entries)
        summary["filtered_entries"] = len(filtered)
        suspicious = find_suspicious_entries(filtered, limit=selected["max_items"])

        return {
            "status": "success",
            "preset": preset_key,
            "config": {
                **selected,
                "log_path": log_path,
            },
            "summary": summary,
            "count": len(suspicious),
            "items": suspicious,
        }

    @app.get("/operations/workspace")
    async def operations_workspace(
        response: Response,
        artifact_type: str | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        """Return read-only workspace and validation evidence for local operations UIs."""
        allowed_types = {"validation", "governance", "memory", "chat"}
        normalized_type = artifact_type.strip().lower() if artifact_type else None
        if normalized_type and normalized_type not in allowed_types:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"Unknown artifact type: {artifact_type}",
                    "allowed_types": sorted(allowed_types),
                },
            )

        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "success",
            "workspace": get_workspace_status(),
            "artifacts": list_workspace_artifacts(
                artifact_type=normalized_type,
                limit=limit,
            ),
            "filters": {
                "artifact_type": normalized_type or "all",
                "limit": limit,
            },
        }

    @app.get("/operations/graph/subgraph", response_model=GraphSubgraphResponse)
    async def operations_graph_subgraph(
        response: Response,
        component: Literal["orchestrator", "memory"] = Query(...),
        limit: int = Query(default=20, ge=1, le=25),
    ) -> GraphSubgraphResponse:
        """Return one bounded, property-filtered Neo4j view for the architecture UI."""
        response.headers["Cache-Control"] = "no-store"
        request = GraphSubgraphRequest(component=component, limit=limit)
        adapter = getattr(app.state, "memory_adapter", None)

        if isinstance(adapter, RemoteMemoryAdapter):
            try:
                payload = await adapter._post_json(
                    "/graph/architecture/subgraph",
                    request.model_dump(),
                )
                return GraphSubgraphResponse(**payload)
            except Exception as exc:
                return GraphSubgraphResponse(
                    component=component,
                    status=MemoryServiceStatus(
                        status="failed",
                        backend="memory-service",
                        degraded=True,
                        error=f"architecture_subgraph_proxy_error: {exc}",
                    ),
                )

        store = BackedMemoryServiceStore()
        try:
            return await store.architecture_subgraph(request)
        finally:
            await store.close()

    @app.get("/operations/heartbeat", response_model=HeartbeatOperationsResponse)
    async def operations_heartbeat(
        response: Response,
        window_seconds: int = Query(default=300, ge=10, le=900),
    ) -> HeartbeatOperationsResponse:
        """Proxy the read-only state curve of the independent heartbeat instance."""
        response.headers["Cache-Control"] = "no-store"
        base_url = os.getenv("LIARA_HEARTBEAT_BASE_URL", "http://127.0.0.1:8050")
        timeout_seconds = max(0.2, float(os.getenv("LIARA_HEARTBEAT_PROXY_TIMEOUT_SECONDS", "3")))
        try:
            return await _fetch_heartbeat_operations(
                base_url=base_url,
                window_seconds=window_seconds,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            return HeartbeatOperationsResponse(
                status="failed",
                error=f"heartbeat_proxy_error: {exc}",
            )

    @app.get("/operations/self-observer", response_model=SelfObserverOperationsResponse)
    async def operations_self_observer(
        response: Response,
        history_limit: int = Query(default=60, ge=1, le=240),
    ) -> SelfObserverOperationsResponse:
        """Expose the observer's read-only state and bounded history."""
        response.headers["Cache-Control"] = "no-store"
        base_url = os.getenv("LIARA_SELF_OBSERVER_BASE_URL", "http://127.0.0.1:8060")
        timeout_seconds = max(0.2, float(os.getenv("LIARA_SELF_OBSERVER_PROXY_TIMEOUT_SECONDS", "4")))
        try:
            return await _fetch_self_observer_operations(
                base_url=base_url,
                history_limit=history_limit,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            return SelfObserverOperationsResponse(
                status="failed",
                error=f"self_observer_proxy_error: {exc}",
            )

    @app.get("/admin/llama-backends")
    async def llama_backends(response: Response) -> dict[str, Any]:
        """List available llama.cpp build variants and show which one is active."""
        response.headers["Cache-Control"] = "no-store"
        from services.config import Settings

        build_base_dir = Settings.LLAMA_CPP_BUILD_BASE_DIR
        configured_variant = Settings.LLAMA_CPP_BUILD_VARIANT

        available: list[dict[str, Any]] = []
        for variant in LlamaCppServerManager.AVAILABLE_BUILDS:
            try:
                path = LlamaCppServerManager.get_build_path(variant)
                available.append({"variant": variant, "path": str(path), "present": True})
            except FileNotFoundError:
                available.append({"variant": variant, "path": None, "present": False})

        try:
            active_variant, active_path = LlamaCppServerManager.find_available_build(
                preferred_variant=configured_variant
            )
        except FileNotFoundError:
            active_variant = None
            active_path = None

        return {
            "build_base_dir": build_base_dir,
            "configured_variant": configured_variant,
            "active_variant": active_variant,
            "active_binary": str(active_path) if active_path else None,
            "available_builds": available,
        }

    @app.post("/compute/run")
    async def compute_run(
        request: Request, response: Response
    ) -> dict[str, Any]:
        """Run a Julia computation model.

        Body JSON:
            model  (str)  — model name, must be in JULIA_ALLOWLIST
            inputs (dict) — model-specific input parameters
        """
        response.headers["Cache-Control"] = "no-store"
        from services.simulation.runner import SimulationRunner

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

    @app.get("/compute/models")
    async def compute_models(response: Response) -> dict[str, Any]:
        """List available (allowlisted) Julia computation models."""
        response.headers["Cache-Control"] = "no-store"
        from services.simulation.bridge import JuliaBridge

        bridge = JuliaBridge()
        return {"models": bridge.list_available()}

    @app.post("/compute/generate")
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
        from services.tools.builtin.compute_generate import ComputeGenerateTool
        
        body = await request.json()
        
        # Validate required fields
        for required in ["model_name", "description", "inputs", "outputs"]:
            if required not in body:
                raise HTTPException(status_code=422, detail=f"'{required}' field is required")
        
        tool = ComputeGenerateTool()
        result = await tool.execute(**body)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=422, detail=result.get("message", "generation failed"))
        
        return result

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest, response: Response) -> ChatResponse:
        response.headers["Cache-Control"] = "no-store"
        _run_id, chat_response = await _run_chat(request)
        return chat_response

    @app.post("/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
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

            run_task = asyncio.create_task(_run_chat(request, progress_callback=_progress_callback))
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
                except asyncio.CancelledError:
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
                yield (
                    "event: error\n"
                    f"data: {json.dumps({'message': str(exc), 'ts': datetime.now(UTC).isoformat()})}\n\n"
                )
                yield "event: done\ndata: {}\n\n"
                return

            text = chat_response.response
            chunk_size = 120
            
            # Always send at least one chunk, even if empty
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

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/history", response_model=MemoryHistoryResponse)
    async def history(
        response: Response,
        session_id: str = Query(...),
        run_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        include_tool_messages: bool = Query(default=True),
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

    @app.get("/session", response_model=SessionResponse)
    async def session(
        response: Response,
        session_id: str = Query(...),
        user_id: str = Query(...),
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

    @app.post("/session", response_model=SessionResponse)
    async def upsert_session(request: SessionUpdateRequest, response: Response) -> SessionResponse:
        response.headers["Cache-Control"] = "no-store"
        now = datetime.now(UTC).isoformat()
        existing_snapshot = await _get_session_snapshot_best_effort(adapter, request.session_id)

        try:
            effective_sandbox_root = _resolve_effective_sandbox_root(request.sandbox_root, existing_snapshot)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await _write_session_snapshot(
            adapter,
            request.session_id,
            request.user_id,
            now,
            sandbox_root=effective_sandbox_root,
            extra_metadata=request.metadata,
        )

        snapshot = await _get_session_snapshot_best_effort(adapter, request.session_id)
        history_response = await adapter.query_history(
            MemoryHistoryQueryRequest(
                session_id=request.session_id,
                limit=500,
                include_tool_messages=True,
            )
        )
        return _build_session_response(
            request.session_id,
            request.user_id,
            snapshot,
            history_response.status.status,
            len(history_response.items),
        )

    @app.post("/files/upload", response_model=FileUploadResponse)
    async def upload_file(
        response: Response,
        session_id: str = Form(...),
        user_id: str = Form(...),
        file: UploadFile = File(...),
        sandbox_root: str | None = Form(default=None),
    ) -> FileUploadResponse:
        response.headers["Cache-Control"] = "no-store"
        snapshot = await _get_session_snapshot_best_effort(adapter, session_id)

        try:
            effective_sandbox_root = _resolve_effective_sandbox_root(sandbox_root, snapshot)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        max_upload_bytes = max(1024, int(os.getenv("LIARA_UPLOAD_MAX_BYTES", str(5 * 1024 * 1024))))
        char_limit = max(200, int(os.getenv("LIARA_ATTACHMENT_TEXT_CHAR_LIMIT", "12000")))
        raw = await file.read()

        if len(raw) > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "message": "Uploaded file exceeds configured size limit.",
                    "size_bytes": len(raw),
                    "max_upload_bytes": max_upload_bytes,
                },
            )

        scan_result = scan_attachment_bytes(raw)
        if scan_result.status == "blocked":
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Uploaded file blocked by malware scanner.",
                    "filename": file.filename,
                    "scan": scan_result.to_metadata(),
                },
            )

        local_sandbox_root = resolve_sandbox_root(effective_sandbox_root, get_global_sandbox_root())
        storage_root = _attachment_storage_root(str(local_sandbox_root), session_id)
        storage_root.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_upload_name(file.filename or "upload.bin")
        stored_path = storage_root / f"{uuid4().hex}_{safe_name}"
        stored_path.write_bytes(raw)
        stored_path_relative = stored_path.relative_to(local_sandbox_root)
        canonical_stored_path = f"{effective_sandbox_root.rstrip('/')}/{stored_path_relative.as_posix()}"

        text_preview = extract_text_preview(raw, file.content_type, char_limit)
        attachment_payload = {
            "name": safe_name,
            "media_type": file.content_type,
            "text_content": text_preview,
            "size_bytes": len(raw),
            "source": "liara-upload",
            "metadata": {
                "stored_path": canonical_stored_path,
                "stored_path_local": str(stored_path),
                "session_id": session_id,
                "user_id": user_id,
                "sandbox_root": effective_sandbox_root,
                "sandbox_root_local": str(local_sandbox_root),
                "scan": scan_result.to_metadata(),
                "has_text_preview": bool(text_preview),
            },
        }

        return FileUploadResponse(
            attachment=attachment_payload,
            scan=scan_result.to_metadata(),
        )

    @app.get("/files/artifact")
    async def read_artifact(
        response: Response,
        session_id: str = Query(...),
        path: str = Query(...),
        sandbox_root: str | None = Query(default=None),
    ) -> Response:
        response.headers["Cache-Control"] = "private, no-store"
        snapshot = await _get_session_snapshot_best_effort(adapter, session_id)

        try:
            effective_sandbox_root = _resolve_effective_sandbox_root(sandbox_root, snapshot)
            local_sandbox_root = resolve_sandbox_root(effective_sandbox_root, get_global_sandbox_root())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session_segment = _sanitize_session_segment(session_id)
        artifact_scope_root = (local_sandbox_root / ".liara_artifacts" / session_segment).resolve()

        try:
            target = (local_sandbox_root / path).resolve()
            ensure_within_boundary(target, local_sandbox_root, "Artifact path escapes sandbox boundary.")
            ensure_within_boundary(target, artifact_scope_root, "Artifact access denied for this session.")
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found.")

        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            path=target,
            media_type=media_type,
            filename=target.name,
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/tools")
    async def list_tools(request: Request) -> Response:
        names = [name for name in tool_registry.list_tools() if _is_public_tool_name(name)]
        payload = {
            "status": "success",
            "count": len(names),
            "tools": [tool_registry.get_metadata(name) for name in names],
        }
        return _cacheable_json_response(payload, request, cache_control="public, max-age=300, stale-while-revalidate=600")

    @app.get("/tools/{tool_name}")
    async def tool_metadata(tool_name: str, request: Request) -> Response:
        if not _is_public_tool_name(tool_name):
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
        try:
            metadata = tool_registry.get_metadata(tool_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = {
            "status": "success",
            "tool": metadata,
        }
        return _cacheable_json_response(payload, request, cache_control="public, max-age=300, stale-while-revalidate=600")

    @app.post("/tools/{tool_name}/invoke", response_model=ToolExecutionResult)
    async def invoke_tool(tool_name: str, request: ToolInvokeRequest, response: Response) -> ToolExecutionResult:
        response.headers["Cache-Control"] = "no-store"
        if not _is_public_tool_name(tool_name) or tool_name not in tool_registry.list_tools():
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

        parameters = dict(request.parameters)
        parameters.pop("_governance_authorized", None)
        governance_proposal: dict[str, Any] | None = None
        if tool_name == "sys":
            governance_mode = sys_governance_mode()
            proposal_id = str(parameters.get("proposal_id") or "").strip()
            if governance_mode == "all" and not proposal_id:
                raise HTTPException(
                    status_code=422,
                    detail="SYS governance mode 'all' requires proposal_id",
                )
            if proposal_id:
                proposals = _sync_sys_governance_store()
                proposal = proposals.get(proposal_id)
                if proposal is None:
                    raise HTTPException(status_code=404, detail=f"Unknown sys proposal: {proposal_id}")
                if str(proposal.get("decision") or "") != "approved":
                    raise HTTPException(
                        status_code=409,
                        detail=f"Sys proposal is not approved: {proposal_id}",
                    )
                expected_command = str(proposal.get("command") or "").strip()
                approved_parameters = dict(proposal.get("parameters") or {})
                for key, value in approved_parameters.items():
                    if key in _SYS_GOVERNANCE_RUNTIME_PARAMETER_KEYS:
                        continue
                    if key in parameters and parameters[key] != value:
                        raise HTTPException(
                            status_code=409,
                            detail=f"Sys invoke parameter does not match approved proposal: {key}",
                        )
                    parameters.setdefault(key, value)
                incoming_command = str(parameters.get("command") or "").strip()
                expected_digest = str(proposal.get("invocation_digest") or "").strip() or _sys_governance_invocation_digest(
                    expected_command,
                    approved_parameters,
                )
                incoming_digest = _sys_governance_invocation_digest(incoming_command, parameters)
                if expected_command != incoming_command or expected_digest != incoming_digest:
                    raise HTTPException(
                        status_code=409,
                        detail="Sys invoke action does not match approved proposal",
                    )
                governance_proposal = proposal
                parameters["_governance_authorized"] = True

        generated_trace_id = f"api-tool-{uuid4().hex[:12]}"
        if not str(parameters.get("request_id") or "").strip():
            parameters["request_id"] = generated_trace_id
        if not str(parameters.get("run_id") or "").strip():
            parameters["run_id"] = str(parameters.get("request_id") or generated_trace_id)
        if not str(parameters.get("source") or "").strip():
            parameters["source"] = "api"
        if not str(parameters.get("context") or "").strip():
            parameters["context"] = f"api.tools.{tool_name}.invoke"

        if tool_name in {"read_file", "list_files"} and not parameters.get("sandbox_root"):
            session_id = parameters.get("session_id")
            if session_id:
                snapshot = await _get_session_snapshot_best_effort(adapter, session_id)
                sandbox_root = snapshot.get("metadata", {}).get("sandbox_root")
                if sandbox_root:
                    parameters["sandbox_root"] = sandbox_root

        # The canonical sys tool uses `workdir` rather than the removed
        # read_file/list_files `sandbox_root` parameter. Reuse the confined
        # root persisted in the session unless the caller selected an
        # explicit workdir or a temporary WSL workspace session.
        if (
            tool_name == "sys"
            and not parameters.get("workdir")
            and not parameters.get("workspace_session_id")
        ):
            session_id = parameters.get("session_id")
            if session_id:
                snapshot = await _get_session_snapshot_best_effort(adapter, session_id)
                metadata = snapshot.get("metadata", {})
                sandbox_root = metadata.get("sandbox_root")
                if metadata.get("sandbox_root_mode") == "wsl" and sandbox_root:
                    parameters["workdir"] = sandbox_root

        if governance_proposal is not None:
            proposal_id = str(governance_proposal["proposal_id"])
            async with app.state.sys_tool_governance_lock:
                current = app.state.sys_tool_proposals.get(proposal_id)
                invocation = dict((current or {}).get("invocation") or {})
                if invocation.get("state") == "invoking":
                    raise HTTPException(status_code=409, detail=f"Sys proposal invocation already in progress: {proposal_id}")
                attempt_count = int(invocation.get("attempt_count") or 0)
                success_count = int(invocation.get("success_count") or 0)
                max_invocations = int((current or {}).get("max_invocations") or 1)
                if attempt_count >= max_invocations:
                    raise HTTPException(status_code=409, detail=f"Sys proposal invocation limit reached: {proposal_id}")
                invocation.update(
                    {
                        "state": "invoking",
                        "attempt_count": attempt_count + 1,
                        "success_count": success_count,
                        "last_attempt_at": datetime.now(UTC).isoformat(),
                        "last_request_id": parameters.get("request_id"),
                        "last_run_id": parameters.get("run_id"),
                    }
                )
                current["invocation"] = invocation
                current["updated_at"] = invocation["last_attempt_at"]
                _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, app.state.sys_tool_proposals)
                _append_sys_governance_event(
                    app.state.sys_tool_events_path,
                    {
                        "event_type": "invocation_attempted",
                        "proposal_id": proposal_id,
                        "tool_name": "sys",
                        "invocation_digest": current.get("invocation_digest"),
                        "attempt_count": invocation["attempt_count"],
                        "traceability": {
                            "request_id": parameters.get("request_id"),
                            "run_id": parameters.get("run_id"),
                            "session_id": parameters.get("session_id"),
                            "source": parameters.get("source"),
                            "context": parameters.get("context"),
                        },
                    },
                )

        try:
            result = await tool_coordinator.execute_tool(
                ToolExecutionRequest(
                    tool_name=tool_name,
                    parameters=parameters,
                    timeout_seconds=request.timeout_seconds,
                    simulation_mode=request.simulation_mode,
                )
            )
        except Exception as exc:
            if governance_proposal is not None:
                proposal_id = str(governance_proposal["proposal_id"])
                async with app.state.sys_tool_governance_lock:
                    current = app.state.sys_tool_proposals[proposal_id]
                    invocation = dict(current.get("invocation") or {})
                    invocation.update({"state": "failed", "last_completed_at": datetime.now(UTC).isoformat(), "last_error": str(exc)})
                    current["invocation"] = invocation
                    current["updated_at"] = invocation["last_completed_at"]
                    _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, app.state.sys_tool_proposals)
                    _append_sys_governance_event(app.state.sys_tool_events_path, {"event_type": "invocation_failed", "proposal_id": proposal_id, "tool_name": "sys", "error": str(exc), "traceability": {"request_id": parameters.get("request_id"), "run_id": parameters.get("run_id")}})
            raise

        if governance_proposal is not None:
            proposal_id = str(governance_proposal["proposal_id"])
            async with app.state.sys_tool_governance_lock:
                current = app.state.sys_tool_proposals[proposal_id]
                invocation = dict(current.get("invocation") or {})
                succeeded = result.status == "success"
                success_count = int(invocation.get("success_count") or 0) + int(succeeded)
                invocation.update(
                    {
                        "state": "completed" if succeeded else "failed",
                        "success_count": success_count,
                        "last_completed_at": datetime.now(UTC).isoformat(),
                        "last_status": result.status,
                        "last_error": result.error,
                        "last_execution_ms": result.execution_ms,
                    }
                )
                current["invocation"] = invocation
                current["updated_at"] = invocation["last_completed_at"]
                _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, app.state.sys_tool_proposals)
                _append_sys_governance_event(
                    app.state.sys_tool_events_path,
                    {
                        "event_type": "invocation_completed" if succeeded else "invocation_failed",
                        "proposal_id": proposal_id,
                        "tool_name": "sys",
                        "status": result.status,
                        "error": result.error,
                        "execution_ms": result.execution_ms,
                        "success_count": success_count,
                        "traceability": {"request_id": parameters.get("request_id"), "run_id": parameters.get("run_id"), "session_id": parameters.get("session_id"), "source": parameters.get("source"), "context": parameters.get("context")},
                    },
                )
        return result

    @app.post("/tools/sys/governance/proposals")
    async def create_sys_governance_proposal(request: SysToolProposalRequest, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        _sync_sys_governance_store()
        proposal_id = f"sys-prop-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        policy = _evaluate_sys_policy(request.command)
        request_id = request.request_id or proposal_id
        run_id = request.run_id or request_id
        source = request.source or "api"
        context = request.context or "api.tools.sys.governance.proposal"

        proposal = {
            "proposal_id": proposal_id,
            "tool_name": "sys",
            "command": request.command,
            "parameters": dict(request.parameters or {}),
            "invocation_digest": _sys_governance_invocation_digest(request.command, request.parameters),
            "max_invocations": request.max_invocations,
            "invocation": {"state": "not_invoked", "attempt_count": 0, "success_count": 0},
            "capability": request.capability,
            "rationale": request.rationale,
            "requested_by": request.requested_by,
            "policy_check": policy,
            "decision": "pending",
            "decision_reason": None,
            "decided_by": None,
            "created_at": now,
            "updated_at": now,
            "traceability": {
                "request_id": request_id,
                "run_id": run_id,
                "session_id": request.session_id,
                "source": source,
                "context": context,
            },
        }
        app.state.sys_tool_proposals[proposal_id] = proposal
        _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, app.state.sys_tool_proposals)
        _append_sys_governance_event(
            app.state.sys_tool_events_path,
            {
                "event_type": "proposal_created",
                "proposal_id": proposal_id,
                "tool_name": "sys",
                "command": request.command,
                "policy_allowed": bool(policy.get("allowed")),
                "policy_risk_level": str(policy.get("risk_level") or "unknown"),
                "traceability": {
                    "request_id": request_id,
                    "run_id": run_id,
                    "session_id": request.session_id,
                    "source": source,
                    "context": context,
                },
            },
        )

        log_judge_pre_action(
            tool_name="sys_governance_proposal",
            decision="allow" if bool(policy.get("allowed")) else "block",
            issues=list(policy.get("reasons") or []),
            constraints={
                "proposal_id": proposal_id,
                "risk_level": policy.get("risk_level"),
                "command": request.command,
            },
            request_id=request_id,
            session_id=request.session_id,
            run_id=run_id,
            source=source,
            context=context,
        )

        return {
            "status": "success",
            "item": proposal,
        }

    @app.get("/tools/sys/governance/proposals")
    async def list_sys_governance_proposals(
        response: Response,
        decision: str = Query(default="all"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        all_items = list(_sync_sys_governance_store().values())
        decision_counts = {"pending": 0, "approved": 0, "rejected": 0}
        invocation_states: dict[str, int] = {}
        policy_blocked = 0
        consumed = 0
        for item in all_items:
            item_decision = str(item.get("decision") or "pending")
            if item_decision in decision_counts:
                decision_counts[item_decision] += 1
            invocation = item.get("invocation") if isinstance(item.get("invocation"), dict) else {}
            invocation_state = str(invocation.get("state") or "not_invoked")
            invocation_states[invocation_state] = invocation_states.get(invocation_state, 0) + 1
            policy_blocked += int(not bool((item.get("policy_check") or {}).get("allowed", True)))
            consumed += int(int(invocation.get("attempt_count") or 0) >= int(item.get("max_invocations") or 1))
        items = all_items
        if decision != "all":
            items = [item for item in items if str(item.get("decision") or "") == decision]
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        sliced = []
        for raw_item in items[:limit]:
            item = dict(raw_item)
            item["audit_reference"] = {
                "endpoint": f"/tools/sys/governance/events?proposal_id={item.get('proposal_id')}",
                "proposal_id": item.get("proposal_id"),
            }
            sliced.append(item)
        return {
            "status": "success",
            "count": len(sliced),
            "total": len(items),
            "items": sliced,
            "summary": {
                "decisions": decision_counts,
                "invocation_states": invocation_states,
                "policy_blocked": policy_blocked,
                "consumed": consumed,
                "enforcement_mode": sys_governance_mode(),
            },
            "filters": {"decision": decision, "limit": limit},
        }

    @app.get("/tools/sys/governance/events")
    async def list_sys_governance_events(
        response: Response,
        proposal_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        events = _load_sys_governance_events(app.state.sys_tool_events_path, proposal_id=proposal_id)
        events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return {
            "status": "success",
            "count": min(len(events), limit),
            "total": len(events),
            "items": events[:limit],
            "filters": {"proposal_id": proposal_id, "limit": limit},
        }

    @app.post("/tools/sys/governance/decisions")
    async def decide_sys_governance_proposal(request: SysToolProposalDecisionRequest, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        proposals = _sync_sys_governance_store()
        proposal = proposals.get(request.proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"Unknown sys proposal: {request.proposal_id}")
        if str(proposal.get("decision") or "") != "pending":
            raise HTTPException(status_code=409, detail=f"Proposal decision is immutable: {request.proposal_id}")
        if request.decision == "approved" and not bool((proposal.get("policy_check") or {}).get("allowed")):
            raise HTTPException(status_code=409, detail=f"Sys proposal is blocked by policy: {request.proposal_id}")

        now = datetime.now(UTC).isoformat()
        proposal["decision"] = request.decision
        proposal["decided_by"] = request.decided_by
        proposal["decision_reason"] = request.decision_reason
        proposal["decision_at"] = now
        proposal["updated_at"] = now
        _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)

        traceability = proposal.get("traceability") or {}
        request_id = request.request_id or str(traceability.get("request_id") or request.proposal_id)
        run_id = request.run_id or str(traceability.get("run_id") or request_id)
        source = request.source or str(traceability.get("source") or "api")
        context = request.context or "api.tools.sys.governance.decision"
        session_id = request.session_id or traceability.get("session_id")

        log_judge_pre_action(
            tool_name="sys_governance_decision",
            decision="allow" if request.decision == "approved" else "block",
            issues=[request.decision_reason],
            constraints={
                "proposal_id": request.proposal_id,
                "proposal_decision": request.decision,
                "command": proposal.get("command"),
            },
            request_id=request_id,
            session_id=session_id,
            run_id=run_id,
            source=source,
            context=context,
        )
        _append_sys_governance_event(
            app.state.sys_tool_events_path,
            {
                "event_type": "proposal_decided",
                "proposal_id": request.proposal_id,
                "tool_name": "sys",
                "decision": request.decision,
                "decided_by": request.decided_by,
                "decision_reason": request.decision_reason,
                "command": proposal.get("command"),
                "traceability": {
                    "request_id": request_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "source": source,
                    "context": context,
                },
            },
        )
        
        # Persist governance decision to workspace
        try:
            artifact_path = await asyncio.to_thread(
                persist_governance_decision,
                governance_id=request.proposal_id,
                command=str(proposal.get("command") or "unknown"),
                risk_tokens=list(proposal.get("risk_tokens") or []),
                decision_approved=(request.decision == "approved"),
                approver=request.decided_by,
                reason=request.decision_reason,
                session_id=session_id,
                request_id=request_id,
                run_id=run_id,
                source=source,
            )
            proposal["artifact_persistence"] = {
                "status": "verified",
                "path": str(artifact_path),
            }
        except Exception as exc:
            proposal["artifact_persistence"] = {
                "status": "failed",
                "error": str(exc),
            }

        handoff = proposal.get("handoff") if isinstance(proposal.get("handoff"), dict) else {}
        checkpoint = handoff.get("checkpoint") if isinstance(handoff.get("checkpoint"), dict) else {}
        resume_payload: dict[str, Any] | None = None
        if checkpoint:
            if request.decision == "rejected":
                handoff["state"] = "rejected"
                handoff["resume"] = {
                    "status": "rejected",
                    "decided_at": now,
                    "reason": request.decision_reason,
                }
            else:
                workspace_agent = getattr(orch, "workspace_agent", None)
                if workspace_agent is None or not hasattr(workspace_agent, "resume_from_governance_proposal"):
                    handoff["state"] = "resume_unavailable"
                    handoff["resume"] = {
                        "status": "unavailable",
                        "error": "orchestrator has no resumable workspace agent",
                    }
                else:
                    handoff["state"] = "resuming"
                    proposal["handoff"] = handoff
                    proposal["updated_at"] = datetime.now(UTC).isoformat()
                    _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
                    try:
                        invoke_parameters = dict(proposal.get("parameters") or {})
                        invoke_parameters["proposal_id"] = request.proposal_id
                        approved_execution = await invoke_tool(
                            "sys",
                            ToolInvokeRequest(parameters=invoke_parameters),
                            Response(),
                        )
                        refreshed = _sync_sys_governance_store().get(request.proposal_id)
                        if refreshed is None:
                            raise RuntimeError("approved workspace proposal disappeared during invocation")
                        workspace_result = await workspace_agent.resume_from_governance_proposal(
                            refreshed,
                            approved_execution,
                        )
                        resume_payload = workspace_result.model_dump(mode="json")
                        persistence: dict[str, Any]
                        try:
                            persistence = await workspace_agent.persist_run_artifact(
                                workspace_result,
                                session_id=str(session_id or ""),
                                run_id=run_id,
                            )
                        except Exception as persist_error:
                            persistence = {"status": "failed", "error": str(persist_error)}
                        resume_payload["persistence"] = persistence
                        proposals = _sync_sys_governance_store()
                        proposal = proposals[request.proposal_id]
                        handoff = dict(proposal.get("handoff") or {})
                        handoff["state"] = (
                            "resume_completed" if workspace_result.status == "completed" else workspace_result.status
                        )
                        handoff["resume"] = {
                            "status": workspace_result.status,
                            "completed_at": datetime.now(UTC).isoformat(),
                            "step_count": len(workspace_result.steps),
                            "validator": dict(workspace_result.validator or {}),
                            "persistence": persistence,
                        }
                    except Exception as exc:
                        proposals = _sync_sys_governance_store()
                        proposal = proposals[request.proposal_id]
                        handoff = dict(proposal.get("handoff") or {})
                        handoff["state"] = "resume_failed"
                        handoff["resume"] = {
                            "status": "failed",
                            "failed_at": datetime.now(UTC).isoformat(),
                            "error": str(exc),
                        }
                        resume_payload = dict(handoff["resume"])
                    proposal["handoff"] = handoff
                    proposal["updated_at"] = datetime.now(UTC).isoformat()
                    _append_sys_governance_event(
                        app.state.sys_tool_events_path,
                        {
                            "event_type": {
                                "resume_completed": "workspace_resume_completed",
                                "awaiting_decision": "workspace_resume_paused",
                            }.get(str(handoff.get("state") or ""), "workspace_resume_failed"),
                            "proposal_id": request.proposal_id,
                            "tool_name": "sys",
                            "resume_status": (handoff.get("resume") or {}).get("status"),
                            "traceability": {
                                "request_id": request_id,
                                "run_id": run_id,
                                "session_id": session_id,
                                "source": source,
                                "context": "api.tools.sys.governance.workspace_resume",
                            },
                        },
                    )

            proposal["handoff"] = handoff
            proposal["updated_at"] = datetime.now(UTC).isoformat()

        proposals[request.proposal_id] = proposal
        _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)

        return {
            "status": "success",
            "item": proposal,
            "workspace_resume": resume_payload,
        }

    @app.post("/tools/sys/governance/actions")
    async def act_on_sys_governance_proposal(
        request: SysToolProposalActionRequest,
        response: Response,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        proposals = _sync_sys_governance_store()
        proposal = proposals.get(request.proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"Unknown sys proposal: {request.proposal_id}")

        traceability = dict(proposal.get("traceability") or {})
        request_id = request.request_id or str(traceability.get("request_id") or request.proposal_id)
        run_id = request.run_id or str(traceability.get("run_id") or request_id)
        session_id = request.session_id or traceability.get("session_id")
        source = request.source or str(traceability.get("source") or "api")
        context = request.context or f"api.tools.sys.governance.{request.action}"
        trace = {
            "request_id": request_id,
            "run_id": run_id,
            "session_id": session_id,
            "source": source,
            "context": context,
        }

        if request.action == "apply":
            if str(proposal.get("decision") or "") != "approved":
                raise HTTPException(status_code=409, detail=f"Sys proposal is not approved: {request.proposal_id}")
            handoff = proposal.get("handoff") if isinstance(proposal.get("handoff"), dict) else {}
            if isinstance(handoff.get("checkpoint"), dict):
                raise HTTPException(
                    status_code=409,
                    detail="Workspace checkpoint proposals are applied automatically by their decision",
                )
            transaction = dict(proposal.get("transaction") or {})
            if str(transaction.get("state") or "") in {"applying", "applied", "rolling_back", "rolled_back"}:
                raise HTTPException(status_code=409, detail=f"Proposal action already started: {request.proposal_id}")
            invocation = dict(proposal.get("invocation") or {})
            if int(invocation.get("attempt_count") or 0) > 0:
                raise HTTPException(status_code=409, detail=f"Proposal invocation already consumed: {request.proposal_id}")

            async with app.state.sys_tool_governance_lock:
                proposals = _sync_sys_governance_store()
                proposal = proposals[request.proposal_id]
                transaction = dict(proposal.get("transaction") or {})
                invocation = dict(proposal.get("invocation") or {})
                if str(transaction.get("state") or "") in {
                    "preparing",
                    "applying",
                    "applied",
                    "rolling_back",
                    "rolled_back",
                } or int(invocation.get("attempt_count") or 0) > 0:
                    raise HTTPException(status_code=409, detail=f"Proposal action already started: {request.proposal_id}")
                proposal["transaction"] = {
                    "state": "preparing",
                    "apply": {
                        "acted_by": request.acted_by,
                        "reason": request.action_reason,
                        "started_at": datetime.now(UTC).isoformat(),
                    },
                }
                proposal["updated_at"] = datetime.now(UTC).isoformat()
                proposals[request.proposal_id] = proposal
                _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)

            parameters = dict(proposal.get("parameters") or {})
            parameters.setdefault("command", str(proposal.get("command") or ""))
            target_path, unsupported_reason = _reversible_sys_target(parameters)
            rollback: dict[str, Any] = {
                "supported": False,
                "state": "unavailable",
                "reason": unsupported_reason,
            }
            if target_path:
                preflight_parameters = {
                    "command": "cat",
                    "args": [target_path],
                    "workdir": str(parameters.get("workdir") or os.getenv("LIARA_AGENT_WORKSPACE_ROOT", "/home/liara/workspace")),
                    **trace,
                    "source": "governance_apply_preflight",
                    "context": "api.tools.sys.governance.rollback_capture",
                }
                try:
                    preflight = await tool_coordinator.execute_tool(
                        ToolExecutionRequest(tool_name="sys", parameters=preflight_parameters)
                    )
                except Exception as exc:
                    preflight = ToolExecutionResult(
                        tool_name="sys",
                        status="error",
                        error=str(exc),
                        execution_ms=0.0,
                    )
                if preflight.status == "success" and isinstance(preflight.output, str):
                    try:
                        reference = _persist_sys_governance_rollback_snapshot(
                            app.state.sys_tool_proposals_path,
                            request.proposal_id,
                            target_path=target_path,
                            content=preflight.output,
                        )
                        rollback = {
                            "supported": True,
                            "state": "captured",
                            "snapshot": reference,
                        }
                    except (OSError, ValueError) as exc:
                        rollback["reason"] = str(exc)
                else:
                    rollback["reason"] = "target did not exist as a readable regular file before apply"

            now = datetime.now(UTC).isoformat()
            transaction = {
                "state": "applying",
                "apply": {
                    "acted_by": request.acted_by,
                    "reason": request.action_reason,
                    "started_at": now,
                },
                "rollback": rollback,
            }
            proposal["transaction"] = transaction
            proposal["updated_at"] = now
            proposals[request.proposal_id] = proposal
            _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
            _append_sys_governance_event(
                app.state.sys_tool_events_path,
                {
                    "event_type": "governance_apply_attempted",
                    "proposal_id": request.proposal_id,
                    "tool_name": "sys",
                    "rollback_supported": bool(rollback.get("supported")),
                    "acted_by": request.acted_by,
                    "action_reason": request.action_reason,
                    "traceability": trace,
                },
            )

            execution: ToolExecutionResult | None = None
            try:
                invoke_parameters = dict(parameters)
                invoke_parameters["proposal_id"] = request.proposal_id
                execution = await invoke_tool(
                    "sys",
                    ToolInvokeRequest(parameters=invoke_parameters, timeout_seconds=120),
                    Response(),
                )
                if execution.status != "success":
                    raise RuntimeError(execution.error or "approved SYS action failed")
            except Exception as exc:
                proposals = _sync_sys_governance_store()
                proposal = proposals[request.proposal_id]
                transaction = dict(proposal.get("transaction") or {})
                transaction["state"] = "apply_failed"
                transaction["apply"] = {
                    **dict(transaction.get("apply") or {}),
                    "failed_at": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                }
                proposal["transaction"] = transaction
                proposal["updated_at"] = datetime.now(UTC).isoformat()
                proposals[request.proposal_id] = proposal
                _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
                _append_sys_governance_event(
                    app.state.sys_tool_events_path,
                    {
                        "event_type": "governance_apply_failed",
                        "proposal_id": request.proposal_id,
                        "tool_name": "sys",
                        "error": str(exc),
                        "traceability": trace,
                    },
                )
                raise HTTPException(status_code=409, detail=f"Governance apply failed: {exc}") from exc

            proposals = _sync_sys_governance_store()
            proposal = proposals[request.proposal_id]
            transaction = dict(proposal.get("transaction") or {})
            transaction["state"] = "applied"
            transaction["apply"] = {
                **dict(transaction.get("apply") or {}),
                "completed_at": datetime.now(UTC).isoformat(),
                "status": execution.status,
            }
            proposal["transaction"] = transaction
            proposal["updated_at"] = datetime.now(UTC).isoformat()
            proposals[request.proposal_id] = proposal
            _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
            _append_sys_governance_event(
                app.state.sys_tool_events_path,
                {
                    "event_type": "governance_apply_completed",
                    "proposal_id": request.proposal_id,
                    "tool_name": "sys",
                    "rollback_supported": bool((transaction.get("rollback") or {}).get("supported")),
                    "traceability": trace,
                },
            )
            return {
                "status": "success",
                "action": "apply",
                "item": proposal,
                "execution": execution.model_dump(mode="json"),
            }

        transaction = dict(proposal.get("transaction") or {})
        if str(transaction.get("state") or "") != "applied":
            raise HTTPException(status_code=409, detail=f"Proposal is not in applied state: {request.proposal_id}")
        rollback = dict(transaction.get("rollback") or {})
        if not bool(rollback.get("supported")) or str(rollback.get("state") or "") != "captured":
            reason = str(rollback.get("reason") or "rollback is unavailable")
            raise HTTPException(status_code=409, detail=reason)
        try:
            snapshot = _load_sys_governance_rollback_snapshot(
                app.state.sys_tool_proposals_path,
                request.proposal_id,
                dict(rollback.get("snapshot") or {}),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=409, detail=f"Rollback snapshot is invalid: {exc}") from exc

        async with app.state.sys_tool_governance_lock:
            proposals = _sync_sys_governance_store()
            proposal = proposals[request.proposal_id]
            transaction = dict(proposal.get("transaction") or {})
            rollback = dict(transaction.get("rollback") or {})
            if str(transaction.get("state") or "") != "applied" or str(rollback.get("state") or "") != "captured":
                raise HTTPException(status_code=409, detail=f"Proposal rollback already started: {request.proposal_id}")
            transaction["state"] = "rollback_preparing"
            rollback["state"] = "preparing"
            rollback["acted_by"] = request.acted_by
            rollback["reason"] = request.action_reason
            rollback["started_at"] = datetime.now(UTC).isoformat()
            transaction["rollback"] = rollback
            proposal["transaction"] = transaction
            proposal["updated_at"] = datetime.now(UTC).isoformat()
            proposals[request.proposal_id] = proposal
            _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)

        rollback_parameters = {
            "command": "tee",
            "args": [snapshot["target_path"]],
            "stdin_text": snapshot["content"],
            "target_path": snapshot["target_path"],
            "write_mode": "overwrite",
            "storage_scope": "wsl_workspace",
            "workdir": str((proposal.get("parameters") or {}).get("workdir") or os.getenv("LIARA_AGENT_WORKSPACE_ROOT", "/home/liara/workspace")),
            **trace,
            "source": "governance_rollback",
            "context": "api.tools.sys.governance.rollback_compensation",
        }
        rollback_proposal = await asyncio.to_thread(
            create_pending_sys_governance_proposal,
            command="tee",
            parameters=rollback_parameters,
            capability="governance_rollback",
            rationale=f"Compensate applied proposal {request.proposal_id}",
            requested_by=request.acted_by,
            traceability=trace,
            handoff={
                "state": "rollback_pending",
                "step_id": f"rollback-{request.proposal_id}",
                "rollback_of": request.proposal_id,
            },
            origin="governance_rollback",
        )
        proposals = _sync_sys_governance_store()
        rollback_proposal = proposals[str(rollback_proposal["proposal_id"])]
        now = datetime.now(UTC).isoformat()
        rollback_proposal["decision"] = "approved"
        rollback_proposal["decided_by"] = request.acted_by
        rollback_proposal["decision_reason"] = request.action_reason
        rollback_proposal["decision_at"] = now
        rollback_proposal["rollback_of"] = request.proposal_id
        rollback_proposal["updated_at"] = now
        proposal = proposals[request.proposal_id]
        transaction = dict(proposal.get("transaction") or {})
        transaction["state"] = "rolling_back"
        rollback = dict(transaction.get("rollback") or {})
        rollback.update({
            "state": "rolling_back",
            "proposal_id": rollback_proposal["proposal_id"],
            "acted_by": request.acted_by,
            "reason": request.action_reason,
            "started_at": now,
        })
        transaction["rollback"] = rollback
        proposal["transaction"] = transaction
        proposal["updated_at"] = now
        proposals[request.proposal_id] = proposal
        proposals[str(rollback_proposal["proposal_id"])] = rollback_proposal
        _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
        _append_sys_governance_event(
            app.state.sys_tool_events_path,
            {
                "event_type": "proposal_decided",
                "proposal_id": rollback_proposal["proposal_id"],
                "tool_name": "sys",
                "decision": "approved",
                "decided_by": request.acted_by,
                "decision_reason": request.action_reason,
                "command": "tee",
                "rollback_of": request.proposal_id,
                "traceability": trace,
            },
        )
        _append_sys_governance_event(
            app.state.sys_tool_events_path,
            {
                "event_type": "governance_rollback_attempted",
                "proposal_id": request.proposal_id,
                "rollback_proposal_id": rollback_proposal["proposal_id"],
                "tool_name": "sys",
                "traceability": trace,
            },
        )

        try:
            rollback_invoke_parameters = dict(rollback_parameters)
            rollback_invoke_parameters["proposal_id"] = rollback_proposal["proposal_id"]
            execution = await invoke_tool(
                "sys",
                ToolInvokeRequest(parameters=rollback_invoke_parameters, timeout_seconds=120),
                Response(),
            )
            evidence = dict(execution.metadata.get("mutation_evidence") or {})
            if execution.status != "success" or not bool(execution.metadata.get("mutation_verified")):
                raise RuntimeError(execution.error or "rollback mutation was not verified")
            if str(evidence.get("sha256") or "") != str(snapshot.get("sha256") or ""):
                raise RuntimeError("rollback content digest was not restored")
        except Exception as exc:
            proposals = _sync_sys_governance_store()
            proposal = proposals[request.proposal_id]
            transaction = dict(proposal.get("transaction") or {})
            transaction["state"] = "rollback_failed"
            rollback = dict(transaction.get("rollback") or {})
            rollback.update({"state": "failed", "failed_at": datetime.now(UTC).isoformat(), "error": str(exc)})
            transaction["rollback"] = rollback
            proposal["transaction"] = transaction
            proposal["updated_at"] = datetime.now(UTC).isoformat()
            proposals[request.proposal_id] = proposal
            _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
            _append_sys_governance_event(
                app.state.sys_tool_events_path,
                {
                    "event_type": "governance_rollback_failed",
                    "proposal_id": request.proposal_id,
                    "rollback_proposal_id": rollback_proposal["proposal_id"],
                    "tool_name": "sys",
                    "error": str(exc),
                    "traceability": trace,
                },
            )
            raise HTTPException(status_code=409, detail=f"Governance rollback failed: {exc}") from exc

        proposals = _sync_sys_governance_store()
        proposal = proposals[request.proposal_id]
        transaction = dict(proposal.get("transaction") or {})
        transaction["state"] = "rolled_back"
        rollback = dict(transaction.get("rollback") or {})
        rollback.update({
            "state": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "restored_sha256": snapshot["sha256"],
        })
        transaction["rollback"] = rollback
        proposal["transaction"] = transaction
        proposal["updated_at"] = datetime.now(UTC).isoformat()
        proposals[request.proposal_id] = proposal
        _persist_sys_governance_proposals(app.state.sys_tool_proposals_path, proposals)
        _append_sys_governance_event(
            app.state.sys_tool_events_path,
            {
                "event_type": "governance_rollback_completed",
                "proposal_id": request.proposal_id,
                "rollback_proposal_id": rollback_proposal["proposal_id"],
                "tool_name": "sys",
                "restored_sha256": snapshot["sha256"],
                "traceability": trace,
            },
        )
        return {
            "status": "success",
            "action": "rollback",
            "item": proposal,
            "rollback_proposal": proposals.get(str(rollback_proposal["proposal_id"]), rollback_proposal),
            "execution": execution.model_dump(mode="json"),
        }

    return app


app = create_api_app()

__all__ = ["SessionResponse", "app", "create_api_app", "create_default_memory_adapter", "create_default_orchestrator"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "services.api.app:app",
        host=os.getenv("LIARA_API_BIND_HOST", "0.0.0.0"),
        port=int(os.getenv("LIARA_API_PORT", "8010")),
    )
