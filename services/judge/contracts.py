"""Unified Judge contracts for tool-agnostic policy decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JudgeStage(str, Enum):
    """Lifecycle stages where judge checks can run."""

    PRE_ACTION = "pre_action"
    RUNTIME = "runtime"
    POST_RESULT = "post_result"


class JudgeDecisionType(str, Enum):
    """System-wide decision set for judge outcomes."""

    ALLOW = "allow"
    WARN = "warn"
    REVISE = "revise"
    BLOCK = "block"


@dataclass(slots=True)
class JudgeCheckResult:
    """Single check result emitted by a judge check."""

    check: str
    status: str  # pass | fail | skip
    severity: str = "low"  # low | medium | high | critical
    reason_code: str | None = None
    message: str | None = None


@dataclass(slots=True)
class JudgeContext:
    """Input context passed into a judge evaluation stage."""

    request_id: str
    stage: JudgeStage
    actor: str
    intent: str
    action: str
    input: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JudgeDecision:
    """Unified decision produced by judge evaluation."""

    decision: JudgeDecisionType
    passed: bool
    confidence: float
    checks: list[JudgeCheckResult] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    reason_code: str | None = None
    simulated: bool = False
    next_action: str = "continue"

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

    @classmethod
    def allow(
        cls,
        *,
        confidence: float = 1.0,
        checks: list[JudgeCheckResult] | None = None,
        issues: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        reason_code: str | None = None,
        simulated: bool = False,
        next_action: str = "continue",
    ) -> "JudgeDecision":
        return cls(
            decision=JudgeDecisionType.ALLOW,
            passed=True,
            confidence=confidence,
            checks=checks or [],
            issues=issues or [],
            constraints=constraints or {},
            reason_code=reason_code,
            simulated=simulated,
            next_action=next_action,
        )

    @classmethod
    def warn(
        cls,
        *,
        confidence: float = 0.7,
        checks: list[JudgeCheckResult] | None = None,
        issues: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        reason_code: str | None = None,
        simulated: bool = False,
        next_action: str = "continue",
    ) -> "JudgeDecision":
        return cls(
            decision=JudgeDecisionType.WARN,
            passed=True,
            confidence=confidence,
            checks=checks or [],
            issues=issues or [],
            constraints=constraints or {},
            reason_code=reason_code,
            simulated=simulated,
            next_action=next_action,
        )

    @classmethod
    def revise(
        cls,
        *,
        confidence: float = 0.5,
        checks: list[JudgeCheckResult] | None = None,
        issues: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        reason_code: str | None = None,
        simulated: bool = False,
        next_action: str = "retry",
    ) -> "JudgeDecision":
        return cls(
            decision=JudgeDecisionType.REVISE,
            passed=False,
            confidence=confidence,
            checks=checks or [],
            issues=issues or [],
            constraints=constraints or {},
            reason_code=reason_code,
            simulated=simulated,
            next_action=next_action,
        )

    @classmethod
    def block(
        cls,
        *,
        confidence: float = 0.0,
        checks: list[JudgeCheckResult] | None = None,
        issues: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        reason_code: str | None = None,
        simulated: bool = False,
        next_action: str = "abort",
    ) -> "JudgeDecision":
        return cls(
            decision=JudgeDecisionType.BLOCK,
            passed=False,
            confidence=confidence,
            checks=checks or [],
            issues=issues or [],
            constraints=constraints or {},
            reason_code=reason_code,
            simulated=simulated,
            next_action=next_action,
        )
