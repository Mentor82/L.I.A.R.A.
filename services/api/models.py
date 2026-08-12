"""Pydantic data models for liara-api endpoints."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class SessionResponse(BaseModel):
    """Session snapshot exposed by GET /session."""

    session_id: str
    user_id: str
    message_count: int
    last_run_id: str | None = None
    summary: str | None = None
    last_accessed: str | None = None
    created_at: str | None = None
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


class SpeechGenerationRequest(BaseModel):
    """Request payload for offline speech synthesis endpoint."""

    session_id: str = "default"
    text: str
    sandbox_root: str | None = None
    speaker_profile: str = "gentle-feminine-v1"
    max_audio_tokens: int = Field(default=100, ge=25, le=400)
    seed: int | None = None
    voice: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    response_format: str = "wav"


class SpeechStreamRequest(SpeechGenerationRequest):
    """Request payload for chunked speech synthesis endpoint."""

    codec: Literal["pcm_s16le", "webm_opus", "ogg_opus"] = "webm_opus"
    persist_artifact: bool = False
    chunk_size: int = Field(default=120, ge=20, le=1000)
