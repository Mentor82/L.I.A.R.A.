"""Unit tests for the EvidenceState contract (Issue #8)."""

from __future__ import annotations

import pytest

from services.contracts.evidence_state import (
    ConfirmationKind,
    EvidenceAssertion,
    EvidenceConfirmation,
    EvidenceState,
    merge_evidence_assertions,
)


class TestEvidenceConfirmation:
    def test_requires_non_empty_source(self):
        with pytest.raises(ValueError, match="source"):
            EvidenceConfirmation(source="", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="404 from API")

    def test_requires_non_empty_detail(self):
        with pytest.raises(ValueError, match="detail"):
            EvidenceConfirmation(source="github_api", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="   ")

    def test_detail_is_truncated_not_rejected(self):
        confirmation = EvidenceConfirmation(
            source="github_api",
            kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE,
            detail="x" * 500,
        )
        assert len(confirmation.detail) == 300

    def test_to_dict(self):
        confirmation = EvidenceConfirmation(
            source="github_api",
            kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE,
            detail="404 Not Found",
            evidence_id="evt-123",
        )
        assert confirmation.to_dict() == {
            "source": "github_api",
            "kind": "authoritative_api_response",
            "detail": "404 Not Found",
            "evidence_id": "evt-123",
        }


class TestEvidenceAssertionClassmethods:
    def test_found(self):
        a = EvidenceAssertion.found(target="octocat", source="github_api", summary="user exists")
        assert a.state == EvidenceState.FOUND
        assert a.confirmed_by is None

    def test_not_found_in_search(self):
        a = EvidenceAssertion.not_found_in_search(target="octocat", source="web_search")
        assert a.state == EvidenceState.NOT_FOUND_IN_SEARCH
        assert a.reason_code == "discovery_zero_results"

    def test_unresolved(self):
        a = EvidenceAssertion.unresolved(target="tab-1", source="browser_connector")
        assert a.state == EvidenceState.UNRESOLVED

    def test_connector_unavailable(self):
        a = EvidenceAssertion.connector_unavailable(target="octocat", source="github_api")
        assert a.state == EvidenceState.CONNECTOR_UNAVAILABLE

    def test_access_denied(self):
        a = EvidenceAssertion.access_denied(target="octocat", source="github_api")
        assert a.state == EvidenceState.ACCESS_DENIED

    def test_does_not_exist_confirmed_requires_confirmation(self):
        confirmation = EvidenceConfirmation(
            source="github_api", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="404 Not Found"
        )
        a = EvidenceAssertion.does_not_exist_confirmed(target="octocat", source="github_api", confirmed_by=confirmation)
        assert a.state == EvidenceState.DOES_NOT_EXIST_CONFIRMED
        assert a.confirmed_by is confirmation

    def test_private_confirmed_requires_confirmation(self):
        confirmation = EvidenceConfirmation(
            source="github_api", kind=ConfirmationKind.EXPLICIT_SOURCE_STATEMENT, detail="profile marked private"
        )
        a = EvidenceAssertion.private_confirmed(target="octocat", source="github_api", confirmed_by=confirmation)
        assert a.state == EvidenceState.PRIVATE_CONFIRMED


class TestEvidenceAssertionGuard:
    """The two strong-negative states must be structurally impossible to
    construct without a real EvidenceConfirmation -- not just caught later
    by a heuristic downstream."""

    def test_does_not_exist_confirmed_rejects_none(self):
        with pytest.raises(ValueError, match="EvidenceConfirmation instance"):
            EvidenceAssertion(target="x", state=EvidenceState.DOES_NOT_EXIST_CONFIRMED, source="s", confirmed_by=None)

    def test_does_not_exist_confirmed_rejects_plain_string(self):
        with pytest.raises(ValueError, match="EvidenceConfirmation instance"):
            EvidenceAssertion(
                target="x", state=EvidenceState.DOES_NOT_EXIST_CONFIRMED, source="s", confirmed_by="trust me bro"
            )

    def test_private_confirmed_rejects_plain_string(self):
        with pytest.raises(ValueError, match="EvidenceConfirmation instance"):
            EvidenceAssertion(target="x", state=EvidenceState.PRIVATE_CONFIRMED, source="s", confirmed_by="i just know")

    def test_weak_states_do_not_require_confirmation(self):
        # Sanity check: the guard is specific to the two *_CONFIRMED states.
        EvidenceAssertion(target="x", state=EvidenceState.NOT_FOUND_IN_SEARCH, source="s")
        EvidenceAssertion(target="x", state=EvidenceState.UNRESOLVED, source="s")
        EvidenceAssertion(target="x", state=EvidenceState.CONNECTOR_UNAVAILABLE, source="s")
        EvidenceAssertion(target="x", state=EvidenceState.ACCESS_DENIED, source="s")
        EvidenceAssertion(target="x", state=EvidenceState.FOUND, source="s")


class TestMergeEvidenceAssertions:
    def test_found_overrides_earlier_not_found_in_search(self):
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.not_found_in_search(target="octocat", source="web_search"),
                EvidenceAssertion.found(target="octocat", source="direct_lookup"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.FOUND
        assert merged[0].source == "direct_lookup"

    def test_found_order_independent(self):
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="direct_lookup"),
                EvidenceAssertion.not_found_in_search(target="octocat", source="web_search"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.FOUND

    def test_unresolved_never_downgrades_found(self):
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="direct_lookup"),
                EvidenceAssertion.unresolved(target="octocat", source="flaky_connector"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.FOUND

    def test_confirmed_overrides_weaker_uncertainty(self):
        confirmation = EvidenceConfirmation(
            source="github_api", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="404 Not Found"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.not_found_in_search(target="octocat", source="web_search"),
                EvidenceAssertion.does_not_exist_confirmed(target="octocat", source="github_api", confirmed_by=confirmation),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.DOES_NOT_EXIST_CONFIRMED

    def test_conflicting_confirmed_states_become_conflicting_evidence(self):
        exists_confirmation = EvidenceConfirmation(
            source="github_api", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="200 OK, user found"
        )
        absent_confirmation = EvidenceConfirmation(
            source="cached_snapshot", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="404 in cached snapshot"
        )
        merged = merge_evidence_assertions(
            [
                # FOUND is not a *_CONFIRMED state, so use an explicit does_not_exist_confirmed
                # against a found target to force a genuine confirmed/confirmed conflict.
                EvidenceAssertion.does_not_exist_confirmed(
                    target="octocat", source="cached_snapshot", confirmed_by=absent_confirmation
                ),
                EvidenceAssertion.private_confirmed(
                    target="octocat", source="github_api", confirmed_by=exists_confirmation
                ),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.CONFLICTING_EVIDENCE

    def test_distinct_targets_stay_separate(self):
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="web_search"),
                EvidenceAssertion.not_found_in_search(target="someone_else", source="web_search"),
            ]
        )
        assert {a.target for a in merged} == {"octocat", "someone_else"}

    def test_empty_input(self):
        assert merge_evidence_assertions([]) == []

    def test_conflict_stays_sticky_against_a_later_confirmed_observation(self):
        """Nephy round 2: once a target is CONFLICTING_EVIDENCE, a third
        observation must not silently resolve it just because *_CONFIRMED
        outranks CONFLICTING_EVIDENCE on raw state-strength."""
        exists_confirmation = EvidenceConfirmation(
            source="api_a", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="private per source A"
        )
        absent_confirmation = EvidenceConfirmation(
            source="api_b", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="does not exist per source B"
        )
        third_confirmation = EvidenceConfirmation(
            source="api_c", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="private per source C"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.private_confirmed(target="octocat", source="api_a", confirmed_by=exists_confirmation),
                EvidenceAssertion.does_not_exist_confirmed(target="octocat", source="api_b", confirmed_by=absent_confirmation),
                EvidenceAssertion.private_confirmed(target="octocat", source="api_c", confirmed_by=third_confirmation),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.CONFLICTING_EVIDENCE

    def test_found_and_does_not_exist_confirmed_is_a_conflict(self):
        """FOUND vs DOES_NOT_EXIST_CONFIRMED is a real existence
        contradiction even though only one side is *_CONFIRMED -- the
        earlier special-case only caught both-confirmed disagreements."""
        confirmation = EvidenceConfirmation(
            source="cached_snapshot", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="404 in snapshot"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="direct_lookup"),
                EvidenceAssertion.does_not_exist_confirmed(
                    target="octocat", source="cached_snapshot", confirmed_by=confirmation
                ),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.CONFLICTING_EVIDENCE

    def test_found_and_private_confirmed_is_not_a_conflict(self):
        """A resource can coherently be both found and known-private --
        this must NOT collapse to CONFLICTING_EVIDENCE."""
        confirmation = EvidenceConfirmation(
            source="github_api", kind=ConfirmationKind.EXPLICIT_SOURCE_STATEMENT, detail="marked private"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="direct_lookup"),
                EvidenceAssertion.private_confirmed(target="octocat", source="github_api", confirmed_by=confirmation),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.PRIVATE_CONFIRMED
