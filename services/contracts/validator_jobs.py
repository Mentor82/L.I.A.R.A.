"""Validator job handshake contracts for P2 integration.

This provides a minimal submit/status/result contract surface so LIARA can
track external AI-validator jobs with traceability metadata.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .service_boundaries import MemoryServiceStatus

ValidatorJobState = Literal["queued", "running", "completed", "failed"]


class ValidatorJobSubject(BaseModel):
    """Immutable subject binding carried through submit, status, and result."""

    model_config = ConfigDict(frozen=True)

    proposal_id: Optional[str] = None
    proposal_digest: Optional[str] = None
    context: Optional[str] = None
    scope: Literal["quick", "validate", "python", "security", "custom"] = "quick"
    strict_mode: bool = False
    checks: List[str] = Field(default_factory=list)


class ValidatorSubmitRequest(BaseModel):
    """Submit one validator job for an external worker pipeline."""

    workspace: str
    scope: Literal["quick", "validate", "python", "security", "custom"] = "quick"
    checks: List[str] = Field(default_factory=list)
    strict_mode: bool = False
    request_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None
    context: Optional[str] = None
    proposal_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ValidatorSubmitResponse(BaseModel):
    """Response envelope for validator job submission."""

    job_id: str
    state: ValidatorJobState
    status: MemoryServiceStatus
    summary: Dict[str, Any] = Field(default_factory=dict)
    subject: ValidatorJobSubject | None = None


class ValidatorStatusRequest(BaseModel):
    """Query current state for a validator job."""

    job_id: str


class ValidatorStatusResponse(BaseModel):
    """Response envelope for validator job status lookups."""

    job_id: str
    state: ValidatorJobState
    status: MemoryServiceStatus
    summary: Dict[str, Any] = Field(default_factory=dict)
    subject: ValidatorJobSubject | None = None


class ValidatorFinding(BaseModel):
    """Normalized finding returned by validator runs."""

    severity: Literal["info", "warning", "error"] = "info"
    message: str
    file_path: Optional[str] = None
    line: Optional[int] = None
    rule: Optional[str] = None
    patch_hint: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ValidatorResultRequest(BaseModel):
    """Fetch validator findings/result payload by job id."""

    job_id: str


class ValidatorResultResponse(BaseModel):
    """Response envelope for validator result lookup."""

    job_id: str
    state: ValidatorJobState
    findings: List[ValidatorFinding] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    status: MemoryServiceStatus
    summary: Dict[str, Any] = Field(default_factory=dict)
    subject: ValidatorJobSubject | None = None
