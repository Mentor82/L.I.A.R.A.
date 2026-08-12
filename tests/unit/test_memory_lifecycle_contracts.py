"""Contract tests for memory lifecycle and promotion policy."""

from __future__ import annotations

from services.contracts import (
    allowed_next_statuses,
    evaluate_lifecycle_transition,
    MemoryEvidence,
    MemoryFactRecord,
    MemoryFactUpsertRequest,
    MemoryLifecycleStatus,
    MemoryPromotionActor,
    can_set_verified,
    is_terminal_status,
)


def test_memory_lifecycle_status_contains_expected_progression():
    assert MemoryLifecycleStatus.observed.value == "observed"
    assert MemoryLifecycleStatus.verified.value == "verified"
    assert MemoryLifecycleStatus.revoked.value == "revoked"


def test_verified_requires_human_or_trusted_system_exception():
    assert can_set_verified(actor=MemoryPromotionActor.human) is True
    assert can_set_verified(actor=MemoryPromotionActor.agent) is False
    assert can_set_verified(actor=MemoryPromotionActor.system) is False
    assert can_set_verified(actor=MemoryPromotionActor.system, policy_exception="trusted_import") is True
    assert can_set_verified(actor=MemoryPromotionActor.system, policy_exception="migration") is True
    assert can_set_verified(actor=MemoryPromotionActor.system, policy_exception="admin_seed") is True


def test_terminal_status_detection_is_explicit():
    assert is_terminal_status(MemoryLifecycleStatus.deprecated) is True
    assert is_terminal_status(MemoryLifecycleStatus.revoked) is True
    assert is_terminal_status(MemoryLifecycleStatus.tested) is False


def test_memory_fact_upsert_request_accepts_lifecycle_fields():
    request = MemoryFactUpsertRequest(
        namespace="profile",
        key="favorite_color",
        value="cyan",
        status=MemoryLifecycleStatus.candidate,
        promotion_reason="dreaming candidate extraction",
        promotion_actor=MemoryPromotionActor.agent,
        evidence=[MemoryEvidence(source="session_summary", confidence=0.8, reference="turn:12")],
    )

    assert request.status == MemoryLifecycleStatus.candidate
    assert request.promotion_actor == MemoryPromotionActor.agent
    assert request.evidence[0].source == "session_summary"


def test_memory_fact_record_defaults_are_backward_compatible():
    record = MemoryFactRecord(
        fact_id="fact-1",
        namespace="profile",
        key="timezone",
        value="UTC",
        created_at="2026-07-13T12:00:00Z",
    )

    assert record.status == MemoryLifecycleStatus.ephemeral
    assert record.promotion_reason is None
    assert record.evidence == []


def test_allowed_next_statuses_are_explicit_for_verified():
    next_statuses = allowed_next_statuses(MemoryLifecycleStatus.verified)
    assert next_statuses == {MemoryLifecycleStatus.deprecated, MemoryLifecycleStatus.revoked}


def test_memory_no_self_promotion():
    decision = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.candidate,
        to_status=MemoryLifecycleStatus.tested,
        actor=MemoryPromotionActor.agent,
        self_promotion=True,
        promotion_reason="auto-eval",
    )

    assert decision.allowed is False
    assert decision.reason_code == "self_promotion_blocked"


def test_promotion_requires_reason():
    decision = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.staged,
        to_status=MemoryLifecycleStatus.candidate,
        actor=MemoryPromotionActor.agent,
        promotion_reason="",
    )

    assert decision.allowed is False
    assert decision.reason_code == "promotion_reason_required"


def test_verified_immutable_against_non_terminal_backward_transition():
    decision = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.verified,
        to_status=MemoryLifecycleStatus.tested,
        actor=MemoryPromotionActor.human,
        promotion_reason="rollback",
    )

    assert decision.allowed is False
    assert decision.reason_code == "verified_immutable"


def test_verified_requires_human_gate_for_agent_actor():
    decision = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.tested,
        to_status=MemoryLifecycleStatus.verified,
        actor=MemoryPromotionActor.agent,
        promotion_reason="candidate passed checks",
    )

    assert decision.allowed is False
    assert decision.reason_code == "verified_requires_human_gate"


def test_verified_allows_trusted_system_exception():
    decision = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.tested,
        to_status=MemoryLifecycleStatus.verified,
        actor=MemoryPromotionActor.system,
        policy_exception="trusted_import",
        promotion_reason="trusted seed import",
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed"


def test_verified_not_deleted():
    # Verified facts are immutable as ground truth and may only move
    # into explicit terminal lifecycle states, never back into mutable states.
    backward = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.verified,
        to_status=MemoryLifecycleStatus.ephemeral,
        actor=MemoryPromotionActor.human,
        promotion_reason="delete",
    )
    assert backward.allowed is False
    assert backward.reason_code == "verified_immutable"

    terminal = evaluate_lifecycle_transition(
        from_status=MemoryLifecycleStatus.verified,
        to_status=MemoryLifecycleStatus.revoked,
        actor=MemoryPromotionActor.human,
        promotion_reason="policy revocation",
    )
    assert terminal.allowed is True
    assert terminal.reason_code == "allowed"
