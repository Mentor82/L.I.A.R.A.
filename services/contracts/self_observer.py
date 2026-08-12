"""Contracts for LIARA's read-only cyclic self-observer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .validator_jobs import ValidatorFinding


EvidenceDomain = Literal["hardware", "software", "assurance"]
EvidenceState = Literal["healthy", "attention", "degraded", "critical", "unknown"]
ObserverPhase = Literal["observing", "quiet_candidate", "quiet_stable"]
ObserverTrend = Literal["improving", "stable", "degrading", "unknown"]
InspectionMode = Literal["disabled", "observe", "submit"]
InspectionAction = Literal["none", "would_submit", "submitted", "completed", "failed"]
InspectionTrigger = Literal["cyclic", "operator_canary"]


class StateEvidence(BaseModel):
    """One normalized state input; source payloads never cross this boundary."""

    schema_version: Literal["1.0"] = "1.0"
    domain: EvidenceDomain
    source_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: EvidenceState
    confidence: float = Field(ge=0.0, le=1.0)
    stability: float = Field(default=1.0, ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list, max_length=128)
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamp(self) -> "StateEvidence":
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must contain timezone information")
        return self


class SystemStateEnvelope(BaseModel):
    """Verdict-free system state for control loops and human inspection."""

    schema_version: Literal["1.0"] = "1.0"
    observer_id: str
    node_id: str
    sequence: int = Field(ge=0)
    observed_at: datetime
    state: EvidenceState
    phase: ObserverPhase
    trend: ObserverTrend
    confidence: float = Field(ge=0.0, le=1.0)
    stability: float = Field(ge=0.0, le=1.0)
    quiet_cycles: int = Field(ge=0)
    background_analysis_candidate: bool = False
    signals: list[str] = Field(default_factory=list)
    evidence: list[StateEvidence] = Field(min_length=1, max_length=16)


class InspectionTransition(BaseModel):
    state: str = Field(min_length=1, max_length=32)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SelfInspectionCanaryRequest(BaseModel):
    authorization_id: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=8, max_length=512)


class SelfInspectionDecision(BaseModel):
    """Policy result for a possible validator run; never a validator verdict."""

    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: InspectionMode
    eligible: bool
    action: InspectionAction = "none"
    trigger: InspectionTrigger = "cyclic"
    authorization_id: str | None = None
    scope: str = "quick"
    observer_sequence: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)
    minimum_interval_seconds: int = Field(ge=60)
    last_submitted_at: datetime | None = None
    next_eligible_at: datetime | None = None
    job_id: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    job_state: str | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    transitions: list[InspectionTransition] = Field(default_factory=list, max_length=32)
    findings: list[ValidatorFinding] = Field(default_factory=list, max_length=100)
    artifacts: list[str] = Field(default_factory=list, max_length=100)
    error_type: str | None = None


class SelfObserverOperationsResponse(BaseModel):
    status: Literal["success", "failed"]
    service_health: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    state: SystemStateEnvelope | None = None
    history: list[SystemStateEnvelope] = Field(default_factory=list, max_length=240)
    inspection: SelfInspectionDecision | None = None
    error: str | None = None
