"""Evidence-state contract shared by EvidenceEngine, ResponseValidator, and Judge.

Models the distinction between "we have no evidence" and "we have evidence
of absence" so a weak/negative evidence state cannot be silently promoted
into a stronger factual claim downstream (Issue #8: NO_RESULT must never
silently become NOT_EXISTS).

Mirrors services/judge/contracts.py's structural style: str-Enum states,
paired with a slots dataclass carrying state + provenance, with classmethod
constructors for ergonomic construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidenceState(str, Enum):
    """Distinguishes absence-of-evidence from evidence-of-absence."""

    FOUND = "found"
    NOT_FOUND_IN_SEARCH = "not_found_in_search"
    UNRESOLVED = "unresolved"
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    ACCESS_DENIED = "access_denied"
    PRIVATE_CONFIRMED = "private_confirmed"
    DOES_NOT_EXIST_CONFIRMED = "does_not_exist_confirmed"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class ConfirmationKind(str, Enum):
    """Closed set of what may back a *_CONFIRMED evidence state.

    Deliberately small and closed -- a caller must pick one of these, not
    invent an arbitrary label, so a confirmed negative state always traces
    back to a real kind of confirming edge.
    """

    AUTHORITATIVE_API_RESPONSE = "authoritative_api_response"
    EXPLICIT_SOURCE_STATEMENT = "explicit_source_statement"
    OPERATOR_OVERRIDE = "operator_override"


_CONFIRMATION_DETAIL_MAX_CHARS = 300


@dataclass(slots=True)
class EvidenceConfirmation:
    """A bounded, typed confirming edge for a *_CONFIRMED evidence state.

    A bare non-empty string is not a sufficient guard against a caller
    writing something like confirmed_by="trust me bro" -- this type forces
    a specific source, a specific kind (from the closed ConfirmationKind
    set), and a bounded/sanitized justification instead of arbitrary
    unbounded text (e.g. raw tool/connector output pulled in verbatim).
    """

    source: str
    kind: ConfirmationKind
    detail: str
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        self.source = (self.source or "").strip()
        self.detail = (self.detail or "").strip()
        if not self.source:
            raise ValueError("EvidenceConfirmation requires a non-empty source")
        if not self.detail:
            raise ValueError("EvidenceConfirmation requires a non-empty detail")
        if len(self.detail) > _CONFIRMATION_DETAIL_MAX_CHARS:
            self.detail = self.detail[:_CONFIRMATION_DETAIL_MAX_CHARS]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind.value,
            "detail": self.detail,
            "evidence_id": self.evidence_id,
        }


# States that structurally require an EvidenceConfirmation to construct.
_CONFIRMATION_REQUIRED_STATES = (EvidenceState.DOES_NOT_EXIST_CONFIRMED, EvidenceState.PRIVATE_CONFIRMED)


@dataclass(slots=True)
class EvidenceAssertion:
    """A single provenance-carrying evidence-state fact about one target."""

    target: str
    state: EvidenceState
    source: str
    confidence: float = 0.5
    summary: str = ""
    reason_code: str | None = None
    confirmed_by: EvidenceConfirmation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Defense in depth: the two "strong negative" states are
        # structurally impossible to construct without a real
        # EvidenceConfirmation instance -- not just caught later by a
        # heuristic check downstream.
        if self.state in _CONFIRMATION_REQUIRED_STATES and not isinstance(self.confirmed_by, EvidenceConfirmation):
            raise ValueError(
                f"{self.state.value} requires confirmed_by to be an EvidenceConfirmation instance, "
                f"got {type(self.confirmed_by).__name__ if self.confirmed_by is not None else 'None'}"
            )

    @classmethod
    def found(
        cls,
        *,
        target: str,
        source: str,
        summary: str = "",
        confidence: float = 0.9,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(target=target, state=EvidenceState.FOUND, source=source, summary=summary, confidence=confidence, **kwargs)

    @classmethod
    def not_found_in_search(
        cls,
        *,
        target: str,
        source: str,
        summary: str = "",
        confidence: float = 0.3,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.NOT_FOUND_IN_SEARCH,
            source=source,
            summary=summary or "Search returned zero candidates; absence of evidence, not evidence of absence.",
            confidence=confidence,
            reason_code="discovery_zero_results",
            **kwargs,
        )

    @classmethod
    def unresolved(
        cls,
        *,
        target: str,
        source: str,
        summary: str = "",
        confidence: float = 0.2,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.UNRESOLVED,
            source=source,
            summary=summary or "Tool/connector output could not be interpreted.",
            confidence=confidence,
            reason_code="ambiguous_tool_output",
            **kwargs,
        )

    @classmethod
    def connector_unavailable(
        cls,
        *,
        target: str,
        source: str,
        summary: str = "",
        confidence: float = 0.0,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.CONNECTOR_UNAVAILABLE,
            source=source,
            summary=summary or "Connector/tool execution failed.",
            confidence=confidence,
            reason_code="tool_execution_failed",
            **kwargs,
        )

    @classmethod
    def access_denied(
        cls,
        *,
        target: str,
        source: str,
        summary: str = "",
        confidence: float = 0.1,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.ACCESS_DENIED,
            source=source,
            summary=summary or "Access was denied (401/403).",
            confidence=confidence,
            reason_code="access_denied_response",
            **kwargs,
        )

    @classmethod
    def does_not_exist_confirmed(
        cls,
        *,
        target: str,
        source: str,
        confirmed_by: EvidenceConfirmation,
        summary: str = "",
        confidence: float = 0.85,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.DOES_NOT_EXIST_CONFIRMED,
            source=source,
            confirmed_by=confirmed_by,
            # getattr, not confirmed_by.detail: an invalid confirmed_by (not
            # a real EvidenceConfirmation) must surface as __post_init__'s
            # ValueError, not an unrelated AttributeError raised here first.
            summary=summary or f"Confirmed absent by source: {getattr(confirmed_by, 'detail', confirmed_by)}",
            confidence=confidence,
            reason_code="explicit_negative_confirmation",
            **kwargs,
        )

    @classmethod
    def private_confirmed(
        cls,
        *,
        target: str,
        source: str,
        confirmed_by: EvidenceConfirmation,
        summary: str = "",
        confidence: float = 0.85,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.PRIVATE_CONFIRMED,
            source=source,
            confirmed_by=confirmed_by,
            summary=summary or f"Confirmed private by source: {getattr(confirmed_by, 'detail', confirmed_by)}",
            confidence=confidence,
            reason_code="explicit_privacy_confirmation",
            **kwargs,
        )

    @classmethod
    def conflicting_evidence(
        cls,
        *,
        target: str,
        source: str,
        summary: str = "",
        confidence: float = 0.4,
        **kwargs: Any,
    ) -> "EvidenceAssertion":
        return cls(
            target=target,
            state=EvidenceState.CONFLICTING_EVIDENCE,
            source=source,
            summary=summary or "Conflicting confirmed observations for this target.",
            confidence=confidence,
            reason_code="conflicting_confirmed_observations",
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "state": self.state.value,
            "source": self.source,
            "confidence": self.confidence,
            "summary": self.summary,
            "reason_code": self.reason_code,
            "confirmed_by": self.confirmed_by.to_dict() if self.confirmed_by is not None else None,
            "metadata": dict(self.metadata or {}),
        }


# Precedence ranking used by merge_evidence_assertions -- weakest to
# strongest. A stronger state always overrides a weaker one for the same
# target; equal-or-weaker observations never downgrade an already-stronger
# one (e.g. a later UNRESOLVED never overrides an earlier FOUND).
_STATE_STRENGTH: dict[EvidenceState, int] = {
    EvidenceState.UNRESOLVED: 0,
    EvidenceState.CONNECTOR_UNAVAILABLE: 0,
    EvidenceState.ACCESS_DENIED: 1,
    EvidenceState.NOT_FOUND_IN_SEARCH: 1,
    EvidenceState.CONFLICTING_EVIDENCE: 2,
    EvidenceState.FOUND: 3,
    EvidenceState.DOES_NOT_EXIST_CONFIRMED: 4,
    EvidenceState.PRIVATE_CONFIRMED: 4,
}

# ACCESS_DENIED never implies PRIVATE_CONFIRMED: this is structurally true
# already (PRIVATE_CONFIRMED requires an EvidenceConfirmation, which an
# ACCESS_DENIED classification never carries), stated here explicitly so
# the invariant isn't only an emergent property nobody wrote down.


def merge_evidence_assertions(assertions: list[EvidenceAssertion]) -> list[EvidenceAssertion]:
    """Aggregate multiple observations of the same target into one per target.

    Rules (see _STATE_STRENGTH):
    - The observation with strictly higher state-strength wins for a given
      target (e.g. FOUND overrides an earlier NOT_FOUND_IN_SEARCH/
      UNRESOLVED/CONNECTOR_UNAVAILABLE; a *_CONFIRMED state overrides any
      weaker uncertainty state).
    - Two *_CONFIRMED assertions for the same target that disagree (different
      state) collapse into a single CONFLICTING_EVIDENCE assertion, rather
      than an arbitrary pick of one.
    - An equal-or-lower-strength observation never overrides an
      already-stronger one for the same target.
    """
    by_target: dict[str, EvidenceAssertion] = {}
    for assertion in assertions:
        existing = by_target.get(assertion.target)
        if existing is None:
            by_target[assertion.target] = assertion
            continue

        existing_confirmed = existing.state in _CONFIRMATION_REQUIRED_STATES
        new_confirmed = assertion.state in _CONFIRMATION_REQUIRED_STATES
        if existing_confirmed and new_confirmed and existing.state != assertion.state:
            by_target[assertion.target] = EvidenceAssertion.conflicting_evidence(
                target=assertion.target,
                source=f"{existing.source}+{assertion.source}",
                summary=(
                    f"Conflicting confirmed observations: {existing.state.value} (via {existing.source}) "
                    f"vs {assertion.state.value} (via {assertion.source})"
                ),
            )
            continue

        if _STATE_STRENGTH[assertion.state] > _STATE_STRENGTH[existing.state]:
            by_target[assertion.target] = assertion
        # else: equal-or-weaker new observation does not override.

    return list(by_target.values())
