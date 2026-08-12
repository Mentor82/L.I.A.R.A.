"""Memory lifecycle and promotion policy contracts.

This module is intentionally standalone so lifecycle semantics can evolve
without coupling to transport- or service-specific contracts.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryLifecycleStatus(str, Enum):
    """Canonical lifecycle states for memory/fact promotion."""

    observed = "observed"
    ephemeral = "ephemeral"
    staged = "staged"
    candidate = "candidate"
    tested = "tested"
    verified = "verified"
    deprecated = "deprecated"
    revoked = "revoked"


class MemoryPromotionActor(str, Enum):
    """Actor categories for promotion decisions."""

    agent = "agent"
    human = "human"
    system = "system"


TrustedPromotionException = Literal["trusted_import", "migration", "admin_seed"]


class MemoryEvidence(BaseModel):
    """Evidence item attached to staged/candidate/tested/verified facts."""

    source: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reference: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryPromotionMetadata(BaseModel):
    """Promotion metadata captured alongside fact lifecycle transitions."""

    status: MemoryLifecycleStatus
    promotion_reason: str | None = None
    promoted_from: MemoryLifecycleStatus | None = None
    promoted_by: MemoryPromotionActor | None = None
    policy_exception: TrustedPromotionException | None = None
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryTransitionDecision(BaseModel):
    """Decision envelope for lifecycle transition validation."""

    allowed: bool
    reason_code: str
    from_status: MemoryLifecycleStatus
    to_status: MemoryLifecycleStatus


def can_set_verified(
    *,
    actor: MemoryPromotionActor,
    policy_exception: TrustedPromotionException | None = None,
) -> bool:
    """Return whether an actor can set status=verified under current policy."""

    if actor == MemoryPromotionActor.human:
        return True
    if actor == MemoryPromotionActor.system:
        return policy_exception in {"trusted_import", "migration", "admin_seed"}
    return False


def is_terminal_status(status: MemoryLifecycleStatus) -> bool:
    """Terminal statuses can only be replaced with explicit migration logic."""

    return status in {MemoryLifecycleStatus.deprecated, MemoryLifecycleStatus.revoked}


_ALLOWED_TRANSITIONS: dict[MemoryLifecycleStatus, set[MemoryLifecycleStatus]] = {
    MemoryLifecycleStatus.observed: {
        MemoryLifecycleStatus.ephemeral,
        MemoryLifecycleStatus.staged,
        MemoryLifecycleStatus.candidate,
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.ephemeral: {
        MemoryLifecycleStatus.staged,
        MemoryLifecycleStatus.candidate,
        MemoryLifecycleStatus.deprecated,
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.staged: {
        MemoryLifecycleStatus.candidate,
        MemoryLifecycleStatus.deprecated,
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.candidate: {
        MemoryLifecycleStatus.staged,
        MemoryLifecycleStatus.tested,
        MemoryLifecycleStatus.deprecated,
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.tested: {
        MemoryLifecycleStatus.candidate,
        MemoryLifecycleStatus.verified,
        MemoryLifecycleStatus.deprecated,
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.verified: {
        MemoryLifecycleStatus.deprecated,
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.deprecated: {
        MemoryLifecycleStatus.revoked,
    },
    MemoryLifecycleStatus.revoked: set(),
}


_REASON_REQUIRED_TARGETS: set[MemoryLifecycleStatus] = {
    MemoryLifecycleStatus.candidate,
    MemoryLifecycleStatus.tested,
    MemoryLifecycleStatus.verified,
    MemoryLifecycleStatus.deprecated,
    MemoryLifecycleStatus.revoked,
}


def allowed_next_statuses(from_status: MemoryLifecycleStatus) -> set[MemoryLifecycleStatus]:
    """Return allowed target statuses for a source status."""

    return set(_ALLOWED_TRANSITIONS.get(from_status, set()))


def evaluate_lifecycle_transition(
    *,
    from_status: MemoryLifecycleStatus,
    to_status: MemoryLifecycleStatus,
    actor: MemoryPromotionActor,
    policy_exception: TrustedPromotionException | None = None,
    promotion_reason: str | None = None,
    self_promotion: bool = False,
) -> MemoryTransitionDecision:
    """Evaluate whether a lifecycle transition is allowed under policy rules."""

    if from_status == to_status:
        return MemoryTransitionDecision(
            allowed=True,
            reason_code="noop",
            from_status=from_status,
            to_status=to_status,
        )

    if is_terminal_status(from_status):
        return MemoryTransitionDecision(
            allowed=False,
            reason_code="terminal_status_locked",
            from_status=from_status,
            to_status=to_status,
        )

    if from_status == MemoryLifecycleStatus.verified and to_status not in {
        MemoryLifecycleStatus.deprecated,
        MemoryLifecycleStatus.revoked,
    }:
        return MemoryTransitionDecision(
            allowed=False,
            reason_code="verified_immutable",
            from_status=from_status,
            to_status=to_status,
        )

    if to_status not in allowed_next_statuses(from_status):
        return MemoryTransitionDecision(
            allowed=False,
            reason_code="invalid_transition",
            from_status=from_status,
            to_status=to_status,
        )

    if to_status in _REASON_REQUIRED_TARGETS and not str(promotion_reason or "").strip():
        return MemoryTransitionDecision(
            allowed=False,
            reason_code="promotion_reason_required",
            from_status=from_status,
            to_status=to_status,
        )

    if self_promotion and actor == MemoryPromotionActor.agent:
        return MemoryTransitionDecision(
            allowed=False,
            reason_code="self_promotion_blocked",
            from_status=from_status,
            to_status=to_status,
        )

    if to_status == MemoryLifecycleStatus.verified and not can_set_verified(
        actor=actor,
        policy_exception=policy_exception,
    ):
        return MemoryTransitionDecision(
            allowed=False,
            reason_code="verified_requires_human_gate",
            from_status=from_status,
            to_status=to_status,
        )

    return MemoryTransitionDecision(
        allowed=True,
        reason_code="allowed",
        from_status=from_status,
        to_status=to_status,
    )
