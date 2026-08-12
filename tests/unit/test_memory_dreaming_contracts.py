"""Contract tests for staging/dreaming schema module."""

from __future__ import annotations

from services.contracts import (
    MemoryDreamingProposalDecisionRequest,
    MemoryDreamingProposalAssuranceRequest,
    MemoryDreamingCleanupRequest,
    MemoryDreamingProposalListRequest,
    MemoryDreamingProposalRecord,
    MemoryDreamingRunRequest,
    MemoryDreamingStatusResponse,
    MemoryEvidence,
    MemoryLifecycleStatus,
    MemoryServiceStatus,
    MemoryStagingDiscardRequest,
    MemoryStagingListRequest,
    MemoryStagingStageRequest,
    MemoryStagingTouchRequest,
)


def _ok_status() -> MemoryServiceStatus:
    return MemoryServiceStatus(status="success", backend="memory-service")


def test_staging_stage_request_defaults_are_minimal_and_explicit():
    request = MemoryStagingStageRequest(
        session_id="session-1",
        content="User likes matte displays",
    )

    assert request.session_id == "session-1"
    assert request.content.startswith("User likes")
    assert request.run_id is None
    assert request.importance == 0.5
    assert request.access_count == 0
    assert request.ttl_seconds is None
    assert request.source_ids == []
    assert request.metadata == {}


def test_staging_list_request_defaults_to_staged_only():
    request = MemoryStagingListRequest(session_id="session-2")

    assert request.status == MemoryLifecycleStatus.staged
    assert request.limit == 50


def test_staging_discard_requires_reason_for_auditability():
    request = MemoryStagingDiscardRequest(
        session_id="session-3",
        staging_ids=["stg-1", "stg-2"],
        discard_reason="duplicate candidate set",
    )

    assert request.discard_reason != ""
    assert request.staging_ids == ["stg-1", "stg-2"]


def test_staging_touch_requires_reason_and_increment():
    request = MemoryStagingTouchRequest(
        session_id="session-3",
        staging_ids=["stg-1"],
        touch_reason="used as dreaming evidence",
    )

    assert request.access_increment == 1
    assert request.touch_reason != ""
    assert request.staging_ids == ["stg-1"]


def test_dreaming_run_request_is_manual_by_default():
    request = MemoryDreamingRunRequest(session_id="session-4")

    assert request.trigger == "manual"
    assert request.dry_run is False
    assert request.max_items == 25
    assert request.include_session_summary is False
    assert request.summary_max_messages == 50
    assert request.summary_max_chars == 1400
    assert request.include_relation_evidence is False
    assert request.relation_limit == 25
    assert request.include_quality_signals is False
    assert request.require_assurance_for_approval is False


def test_dreaming_proposal_record_defaults_to_candidate_pending():
    proposal = MemoryDreamingProposalRecord(
        proposal_id="prop-1",
        session_id="session-5",
        target_namespace="profile",
        target_key="favorite_color",
        proposed_value="cyan",
        promotion_reason="cross-turn recurrence",
        created_at="2026-07-13T14:00:00Z",
        evidence=[MemoryEvidence(source="session_summary", confidence=0.9, reference="turn:22")],
    )

    assert proposal.proposed_status == MemoryLifecycleStatus.candidate
    assert proposal.decision == "pending"
    assert proposal.evidence[0].source == "session_summary"


def test_dreaming_status_defaults_to_manual_only_mode():
    status = MemoryDreamingStatusResponse(status=_ok_status())

    assert status.scheduler_enabled is False
    assert status.mode == "manual_only"
    assert status.last_run_state == "idle"


def test_dreaming_proposal_list_request_supports_all_decisions_by_default():
    request = MemoryDreamingProposalListRequest(session_id="session-6")

    assert request.decision == "all"
    assert request.limit == 50


def test_dreaming_proposal_decision_request_defaults_to_human_actor():
    request = MemoryDreamingProposalDecisionRequest(
        proposal_id="prop-42",
        decision="approved",
        decision_reason="reviewed by operator",
    )

    assert request.decided_by == "human"
    assert request.policy_exception is None
    assert request.metadata == {}


def test_dreaming_proposal_assurance_requires_explicit_report_binding():
    request = MemoryDreamingProposalAssuranceRequest(
        proposal_id="prop-42",
        validator_job_id="job-42",
        assessment_reason="strict validator run completed",
    )

    assert request.proposal_id == "prop-42"
    assert request.validator_job_id == "job-42"


def test_dreaming_cleanup_defaults_to_bounded_preview():
    request = MemoryDreamingCleanupRequest()

    assert request.dry_run is True
    assert request.rejected_retention_seconds == 2_592_000
    assert request.staging_limit == 500
    assert request.proposal_limit == 500
