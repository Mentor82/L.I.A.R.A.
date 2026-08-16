"""Evidence-state contract shared by EvidenceEngine, ResponseValidator, and Judge.

Models the distinction between "we have no evidence" and "we have evidence
of absence" so a weak/negative evidence state cannot be silently promoted
into a stronger factual claim downstream (Issue #8: NO_RESULT must never
silently become NOT_EXISTS).

Mirrors services/judge/contracts.py's structural style: str-Enum states,
paired with a slots dataclass carrying state + provenance, with classmethod
constructors for ergonomic construction.

Scope (Nephy round 2): as of Issue #8's Phase 1-7 implementation, the only
producer of EvidenceAssertion is EvidenceEngine's classification of
tool_outputs (services/orchestrator/evidence_engine.py). Memory/history,
file, and database retrieval channels do not yet emit EvidenceAssertion --
they still flow through EvidenceEngine as plain, unstated-quality evidence
items (see services/orchestrator/defs/evidence_adapter.py's docstring for
the current tool_output-only wiring). The "NO_RESULT must never silently
become NOT_EXISTS" invariant this module enforces is therefore currently
guaranteed for tool-sourced claims only; a memory/file/database result
being silently over-claimed would not yet be caught by this contract. Those
channels need to be classified into EvidenceState the same way before the
invariant can be considered fully general across the pipeline.

Canonical target identity (Issue #12): EvidenceAssertion.target is free
text -- two different spellings of the same real entity are never
recognized as one, which is safe (never over-merges) but not precise. This
module additionally offers a stricter, optional EvidenceTarget identity: a
connector-native, stable reference (a real URL, resource ID, primary key,
etc.), never derived by parsing, hashing, or fuzzy-matching free text.
Equivalence between two identifiers is established ONLY when a single
observation explicitly asserts its own aliases alongside its canonical_ref
-- two SEPARATE observations are never compared against each other by
their free text to decide they're "the same," no matter how similar they
look. An assertion without a canonical_target (the common case today, since
no real connector populates one yet -- see EvidenceEngine's
_resolve_canonical_target docstring) is the explicit "unresolved identity"
state, not a weaker guess at a resolved one; it therefore never merges with,
and never authorizes a claim on behalf of, any canonical-identified target,
and vice versa. This is a deliberate, permanent split, not a temporary gap:
an assertion with a canonical_target and one without, for what a human
might call "the same" real-world entity, are intentionally never merged
across that boundary -- there is no basis to assume they refer to the same
thing without an explicit connector-asserted link. Retrieval channels
beyond tool_output (memory, files, databases, RAG, graph) remain out of
scope for producing EvidenceTarget-bearing assertions in this issue, same
as they remain out of scope for EvidenceState above.
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
class EvidenceTarget:
    """A connector-verified, stable identity for the resource/entity an
    EvidenceAssertion is about (Issue #12).

    Constructed only from a real, connector-native identifier (a canonical
    URL, resource ID, primary key, etc.) -- never derived by parsing,
    hashing, or fuzzy-matching free text. Absence of an EvidenceTarget
    (EvidenceAssertion.canonical_target is None) is the explicit "unresolved
    identity" state: it must never be treated as equivalent to, or silently
    upgraded into, a resolved one.
    """

    namespace: str
    canonical_ref: str
    kind: str = "entity"
    display_name: str = ""
    aliases: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Whitespace-only normalization, nothing more: no lowercasing, no
        # URL/slash normalization, no entity resolution. " github " and
        # "github" must not silently become two identities, but that is the
        # full extent of what this class does on its own.
        self.namespace = (self.namespace or "").strip()
        self.canonical_ref = (self.canonical_ref or "").strip()
        if not self.namespace:
            raise ValueError("EvidenceTarget requires a non-empty namespace")
        if not self.canonical_ref:
            raise ValueError("EvidenceTarget requires a non-empty canonical_ref")

        # Nephy round 1: identifiers() feeds the validator's literal
        # substring claim-binding match -- a whitespace-only display_name
        # or alias (e.g. " ") would otherwise pass the old "if value"
        # truthiness filter (non-empty strings are truthy) and then match
        # almost any response window, letting an unrelated claim bind to
        # this target. Sanitize here, at the contract boundary, rather than
        # relying on every producer (the engine's _coerce_aliases included)
        # to independently get this right -- defense in depth, not the only
        # line of defense.
        self.display_name = (self.display_name or "").strip()
        self.aliases = frozenset(
            stripped
            for alias in self.aliases
            if isinstance(alias, str) and (stripped := alias.strip())
        )

    def merge_key(self) -> tuple[str, str]:
        """Exact-equality aggregation key. Never prefix/substring matching --
        this is what keeps parent/child resources (an account vs. one of its
        repositories) and same-display-name-different-namespace targets
        structurally distinct, by construction rather than special-casing.
        """
        return (self.namespace, self.canonical_ref)

    def identifiers(self) -> frozenset[str]:
        """All strings that carry claim-binding authority for this target:
        its canonical_ref and its explicitly declared aliases. Used for
        whole-string (never fragment/token) claim-binding matches -- see
        ResponseValidator._check_evidence_state_integrity.

        display_name is deliberately excluded (user/Nephy decision,
        Issue #12 round 1): the contract defines display_name as
        presentation-only, and letting it silently carry identity authority
        would blur that line -- a generic display_name (e.g. "Account")
        with only one canonical target in scope could otherwise bind an
        unrelated claim with no ambiguity to catch it (the ambiguity-fail-
        closed check in the validator only helps when two or more targets
        collide; a single generic display_name never triggers it). If a
        connector's presentation label should also be claim-bindable, it
        must be explicitly added to `aliases` too -- an explicit assertion
        of identity, not an implicit one borrowed from a UI label.
        """
        candidates = {self.canonical_ref} | self.aliases
        return frozenset(value for value in candidates if value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "canonical_ref": self.canonical_ref,
            "kind": self.kind,
            "display_name": self.display_name,
            "aliases": sorted(self.aliases),
        }


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
    canonical_target: EvidenceTarget | None = None

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
            "canonical_target": self.canonical_target.to_dict() if self.canonical_target is not None else None,
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

# State pairs that are semantically contradictory even when not both
# *_CONFIRMED -- a target cannot simultaneously be FOUND and have an
# authoritative confirmation that it does not exist. (PRIVATE_CONFIRMED is
# deliberately excluded from this: a resource can coherently be both found
# and known-private, so FOUND/PRIVATE_CONFIRMED is not a contradiction.)
_EXISTENCE_CONTRADICTING_PAIRS = frozenset({frozenset({EvidenceState.FOUND, EvidenceState.DOES_NOT_EXIST_CONFIRMED})})


def _merge_canonical_targets(a: EvidenceTarget, b: EvidenceTarget) -> EvidenceTarget:
    """Union two EvidenceTargets that already share the same merge_key().

    Safe, not fuzzy: both sides already explicitly agree on the same
    (namespace, canonical_ref) identity (that's the precondition for this
    function being called at all -- see _group_key), so their
    independently-declared aliases can be combined without inferring
    anything new. `kind`/`display_name` prefer `a`'s value, falling back to
    `b`'s when `a`'s is empty. Callers must pass the actual state-winner as
    `a` when one exists (see _merge_pair) -- calling this with a fixed
    (existing, new) order regardless of which one wins the state would make
    the preferred display_name/kind track the wrong side whenever `new`
    wins (Nephy round 1 finding).
    """
    return EvidenceTarget(
        namespace=a.namespace,
        canonical_ref=a.canonical_ref,
        kind=a.kind or b.kind,
        display_name=a.display_name or b.display_name,
        aliases=a.aliases | b.aliases,
    )


def _group_key(assertion: EvidenceAssertion) -> tuple[str, ...]:
    """The dict key merge_evidence_assertions groups on: a canonical
    (namespace, canonical_ref) tuple when available, else the raw target
    string -- discriminated by a leading tag so the two key spaces can never
    collide even if a text target happened to look like a canonical tuple.
    """
    if assertion.canonical_target is not None:
        namespace, canonical_ref = assertion.canonical_target.merge_key()
        return ("canonical", namespace, canonical_ref)
    return ("text", assertion.target)


def _merge_pair(existing: EvidenceAssertion, new: EvidenceAssertion) -> EvidenceAssertion:
    """Decide the merged result for two assertions sharing one group key.

    Same strength/sticky-conflict/existence-contradiction rules regardless
    of whether the shared key is a canonical (namespace, canonical_ref) pair
    or a raw target string -- one implementation for both, so there is
    exactly one place this logic can drift. When both sides carry a
    canonical_target (always true for a canonical-keyed pair, never true for
    a text-keyed one), their aliases are unioned onto whichever assertion
    ends up as the result -- regardless of override, conflict, or sticky
    conflict -- since two assertions sharing a merge_key() have already
    explicitly agreed on the same identity.
    """
    both_canonical = existing.canonical_target is not None and new.canonical_target is not None

    if existing.state == EvidenceState.CONFLICTING_EVIDENCE:
        # No state-winner to speak of here -- existing stays the result as-is,
        # just with its identity's aliases enriched (existing-first order,
        # consistent with it being the side that's kept).
        if both_canonical:
            existing.canonical_target = _merge_canonical_targets(existing.canonical_target, new.canonical_target)
        return existing

    existing_confirmed = existing.state in _CONFIRMATION_REQUIRED_STATES
    new_confirmed = new.state in _CONFIRMATION_REQUIRED_STATES
    is_confirmed_disagreement = existing_confirmed and new_confirmed and existing.state != new.state
    is_existence_contradiction = frozenset({existing.state, new.state}) in _EXISTENCE_CONTRADICTING_PAIRS

    if is_confirmed_disagreement or is_existence_contradiction:
        # Neither side "wins" in a conflict -- existing-first order here is
        # arbitrary but consistent with the summary text below, which also
        # states existing before new.
        merged_identity = (
            _merge_canonical_targets(existing.canonical_target, new.canonical_target) if both_canonical else None
        )
        conflicting = EvidenceAssertion.conflicting_evidence(
            target=new.target,
            source=f"{existing.source}+{new.source}",
            summary=(
                f"Conflicting observations: {existing.state.value} (via {existing.source}) "
                f"vs {new.state.value} (via {new.source})"
            ),
            canonical_target=merged_identity,
        )
        if merged_identity is not None:
            conflicting.metadata["canonicalization"] = {
                "merge_key": f"{merged_identity.namespace}:{merged_identity.canonical_ref}",
                "decision": "conflict",
            }
        return conflicting

    if _STATE_STRENGTH[new.state] > _STATE_STRENGTH[existing.state]:
        winner, loser, decision = new, existing, "override"
    else:
        # Equal-or-weaker new observation does not override the state, but
        # its aliases (if any) still get folded in below.
        winner, loser, decision = existing, new, "alias_union"

    if both_canonical:
        # winner first (Nephy round 1 finding): _merge_canonical_targets'
        # display_name/kind preference must track whichever side actually
        # won the state, not always "existing" regardless of outcome.
        winner.canonical_target = _merge_canonical_targets(winner.canonical_target, loser.canonical_target)
        winner.metadata["canonicalization"] = {
            "merge_key": f"{winner.canonical_target.namespace}:{winner.canonical_target.canonical_ref}",
            "decision": decision,
            "losing_source": loser.source,
        }
    return winner


def merge_evidence_assertions(assertions: list[EvidenceAssertion]) -> list[EvidenceAssertion]:
    """Aggregate multiple observations of the same target into one per target.

    Grouping key (Issue #12): assertions carrying a canonical_target are
    grouped by its exact (namespace, canonical_ref) tuple; assertions
    without one (the common case today) are grouped by the raw target
    string, exactly as before Issue #12 -- these two key spaces are never
    merged into each other. A single ordered dict (not two buckets
    concatenated) holds both, so the result preserves the assertions'
    original first-seen order regardless of whether they carry a
    canonical_target or not.

    Rules (see _STATE_STRENGTH), identical for both grouping kinds:
    - The observation with strictly higher state-strength wins for a given
      target (e.g. FOUND overrides an earlier NOT_FOUND_IN_SEARCH/
      UNRESOLVED/CONNECTOR_UNAVAILABLE; a *_CONFIRMED state overrides any
      weaker uncertainty state).
    - Two *_CONFIRMED assertions for the same target that disagree (different
      state), or a FOUND/DOES_NOT_EXIST_CONFIRMED pair, collapse into a
      single CONFLICTING_EVIDENCE assertion, rather than an arbitrary pick
      of one.
    - CONFLICTING_EVIDENCE is sticky: once a target is flagged conflicting,
      no later single observation may silently resolve it by outranking it
      on raw state-strength -- there is no reconciliation-with-provenance
      mechanism yet, so a conflict must stay visible until one exists.
    - An equal-or-lower-strength observation never overrides an
      already-stronger one for the same target.
    - When two canonical-keyed assertions merge (override, conflict, or
      sticky-conflict), their explicitly declared aliases are always
      unioned onto the result -- see _merge_pair.
    """
    grouped: dict[tuple[str, ...], EvidenceAssertion] = {}
    for assertion in assertions:
        key = _group_key(assertion)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = assertion
            continue
        grouped[key] = _merge_pair(existing, assertion)

    return list(grouped.values())
