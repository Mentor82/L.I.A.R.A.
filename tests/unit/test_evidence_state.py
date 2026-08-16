"""Unit tests for the EvidenceState contract (Issue #8) and the canonical
target identity contract layered on top of it (Issue #12)."""

from __future__ import annotations

import pytest

from services.contracts.evidence_state import (
    ConfirmationKind,
    EvidenceAssertion,
    EvidenceConfirmation,
    EvidenceState,
    EvidenceTarget,
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

    def test_same_canonical_key_unions_explicitly_supplied_aliases_on_override(self):
        """Two observations of the same (namespace, canonical_ref) already
        explicitly agree on identity -- their independently-declared aliases
        may be safely unioned, even though the higher-strength one wins the
        state."""
        target_a = EvidenceTarget(
            namespace="github", canonical_ref="https://github.com/octocat", aliases=frozenset({"octocat"})
        )
        target_b = EvidenceTarget(
            namespace="github",
            canonical_ref="https://github.com/octocat",
            aliases=frozenset({"github account octocat"}),
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.not_found_in_search(target="octocat", source="web_search", canonical_target=target_a),
                EvidenceAssertion.found(target="github account octocat", source="direct_lookup", canonical_target=target_b),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.FOUND
        assert merged[0].canonical_target.aliases == frozenset({"octocat", "github account octocat"})

    def test_same_canonical_key_unions_aliases_even_on_conflict(self):
        target_a = EvidenceTarget(
            namespace="github", canonical_ref="https://github.com/octocat", aliases=frozenset({"octocat"})
        )
        target_b = EvidenceTarget(
            namespace="github",
            canonical_ref="https://github.com/octocat",
            aliases=frozenset({"github account octocat"}),
        )
        confirmation = EvidenceConfirmation(
            source="cached_snapshot", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="404 in snapshot"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="direct_lookup", canonical_target=target_a),
                EvidenceAssertion.does_not_exist_confirmed(
                    target="github account octocat",
                    source="cached_snapshot",
                    confirmed_by=confirmation,
                    canonical_target=target_b,
                ),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.CONFLICTING_EVIDENCE
        assert merged[0].canonical_target.aliases == frozenset({"octocat", "github account octocat"})

    def test_alias_equivalence_merges_under_one_canonical_identity(self):
        """Issue #12 scenario 1: octocat, github account octocat, and a
        canonical account URL resolve to one target once a connector
        explicitly links them via a shared canonical_ref."""
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat")
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.not_found_in_search(target="octocat", source="web_search", canonical_target=target),
                EvidenceAssertion.found(
                    target="github account octocat", source="direct_lookup", canonical_target=target
                ),
                EvidenceAssertion.found(
                    target="https://github.com/octocat", source="api_lookup", canonical_target=target
                ),
            ]
        )
        assert len(merged) == 1
        assert merged[0].state == EvidenceState.FOUND

    def test_parent_child_resources_stay_distinct_by_canonical_ref(self):
        """Issue #12 scenario 2: an account and one of its repositories must
        stay distinct even though their free text/canonical_ref overlap by
        substring."""
        account = EvidenceTarget(namespace="github_user", canonical_ref="https://github.com/octocat")
        repo = EvidenceTarget(namespace="github_repo", canonical_ref="https://github.com/octocat/Hello-World")
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="api", canonical_target=account),
                EvidenceAssertion.not_found_in_search(
                    target="octocat/Hello-World", source="web_search", canonical_target=repo
                ),
            ]
        )
        assert len(merged) == 2
        keys = {m.canonical_target.merge_key() for m in merged}
        assert keys == {account.merge_key(), repo.merge_key()}

    def test_same_display_name_different_namespace_stays_distinct(self):
        """Issue #12 scenario 5 / Nephy mandate 4a: identical display_name,
        different namespace, must never merge."""
        target_a = EvidenceTarget(namespace="github", canonical_ref="ref-a", display_name="octocat")
        target_b = EvidenceTarget(namespace="gitlab", canonical_ref="ref-a", display_name="octocat")
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="octocat", source="github_api", canonical_target=target_a),
                EvidenceAssertion.not_found_in_search(target="octocat", source="gitlab_api", canonical_target=target_b),
            ]
        )
        assert len(merged) == 2

    def test_conflict_on_one_canonical_target_does_not_leak_to_another(self):
        """Issue #12 scenario 6."""
        target_a = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat")
        target_b = EvidenceTarget(namespace="github", canonical_ref="https://github.com/torvalds")
        confirmation_1 = EvidenceConfirmation(
            source="api_1", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="private per source 1"
        )
        confirmation_2 = EvidenceConfirmation(
            source="api_2", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="does not exist per source 2"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.private_confirmed(
                    target="octocat", source="api_1", confirmed_by=confirmation_1, canonical_target=target_a
                ),
                EvidenceAssertion.does_not_exist_confirmed(
                    target="octocat", source="api_2", confirmed_by=confirmation_2, canonical_target=target_a
                ),
                EvidenceAssertion.found(target="torvalds", source="api_3", canonical_target=target_b),
            ]
        )
        assert len(merged) == 2
        by_key = {m.canonical_target.merge_key(): m for m in merged}
        assert by_key[target_a.merge_key()].state == EvidenceState.CONFLICTING_EVIDENCE
        assert by_key[target_b.merge_key()].state == EvidenceState.FOUND

    def test_missing_canonical_identity_stays_in_text_bucket_never_guessed(self):
        """Issue #12 scenario 4 / Nephy mandate 4b, aggregation half: an
        assertion without canonical_target must never be inferred to be the
        same target as one that has a canonical identity sharing display
        text."""
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat", display_name="octocat")
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.not_found_in_search(target="octocat", source="unresolved_source"),
                EvidenceAssertion.found(target="octocat", source="github_api", canonical_target=target),
            ]
        )
        assert len(merged) == 2

    def test_conflict_construction_preserves_canonical_target(self):
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat")
        confirmation_1 = EvidenceConfirmation(
            source="api_1", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="private"
        )
        confirmation_2 = EvidenceConfirmation(
            source="api_2", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="does not exist"
        )
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.private_confirmed(
                    target="octocat", source="api_1", confirmed_by=confirmation_1, canonical_target=target
                ),
                EvidenceAssertion.does_not_exist_confirmed(
                    target="octocat", source="api_2", confirmed_by=confirmation_2, canonical_target=target
                ),
            ]
        )
        assert len(merged) == 1
        assert merged[0].canonical_target is not None
        assert merged[0].canonical_target.merge_key() == target.merge_key()
        assert merged[0].metadata.get("canonicalization", {}).get("decision") == "conflict"

    def test_text_bucket_behavior_unchanged_for_assertions_without_canonical_target(self):
        """Regression anchor for the merge_evidence_assertions refactor:
        pre-existing scenarios with zero canonical_target usage must behave
        identically to before Issue #12."""
        merged_found = merge_evidence_assertions(
            [
                EvidenceAssertion.not_found_in_search(target="octocat", source="web_search"),
                EvidenceAssertion.found(target="octocat", source="direct_lookup"),
            ]
        )
        assert len(merged_found) == 1
        assert merged_found[0].state == EvidenceState.FOUND

        confirmation_1 = EvidenceConfirmation(
            source="api_a", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="private per source A"
        )
        confirmation_2 = EvidenceConfirmation(
            source="api_b", kind=ConfirmationKind.AUTHORITATIVE_API_RESPONSE, detail="does not exist per source B"
        )
        merged_sticky = merge_evidence_assertions(
            [
                EvidenceAssertion.private_confirmed(target="octocat", source="api_a", confirmed_by=confirmation_1),
                EvidenceAssertion.does_not_exist_confirmed(target="octocat", source="api_b", confirmed_by=confirmation_2),
                EvidenceAssertion.private_confirmed(target="octocat", source="api_a", confirmed_by=confirmation_1),
            ]
        )
        assert len(merged_sticky) == 1
        assert merged_sticky[0].state == EvidenceState.CONFLICTING_EVIDENCE

    def test_merge_preserves_global_first_seen_order(self):
        """Nephy plan-review correction, hardening point 2: a single ordered
        dict, not two buckets concatenated -- canonical-bearing assertions
        must not all sort before text-only ones."""
        target_b = EvidenceTarget(namespace="github", canonical_ref="https://github.com/torvalds")
        merged = merge_evidence_assertions(
            [
                EvidenceAssertion.found(target="text-first", source="s1"),
                EvidenceAssertion.found(target="torvalds", source="s2", canonical_target=target_b),
                EvidenceAssertion.found(target="text-second", source="s3"),
            ]
        )
        assert [m.target for m in merged] == ["text-first", "torvalds", "text-second"]


class TestEvidenceTarget:
    def test_requires_non_empty_namespace(self):
        with pytest.raises(ValueError, match="namespace"):
            EvidenceTarget(namespace="", canonical_ref="https://github.com/octocat")

    def test_requires_non_empty_canonical_ref(self):
        with pytest.raises(ValueError, match="canonical_ref"):
            EvidenceTarget(namespace="github", canonical_ref="")

    def test_normalizes_whitespace_only(self):
        """Nephy plan-review correction: __post_init__ strips and writes
        back, but does not lowercase, URL-normalize, or otherwise resolve
        entities."""
        target = EvidenceTarget(namespace="  github  ", canonical_ref="  https://github.com/octocat  ")
        assert target.namespace == "github"
        assert target.canonical_ref == "https://github.com/octocat"

    def test_merge_key_is_namespace_and_canonical_ref_tuple(self):
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat")
        assert target.merge_key() == ("github", "https://github.com/octocat")

    def test_whitespace_only_display_name_is_sanitized_to_empty(self):
        """Nephy round 1 finding: a whitespace-only display_name (e.g. " ")
        is truthy in Python and would otherwise pass the old "if value"
        filter in identifiers(), then match almost any response window as
        a literal substring. __post_init__ must strip it to empty at
        construction time, not rely on identifiers() to catch it later."""
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat", display_name="   ")
        assert target.display_name == ""

    def test_whitespace_only_and_non_string_aliases_are_dropped(self):
        """Nephy round 1 finding: aliases must be sanitized (stripped,
        filtered to non-empty real strings) at the contract boundary, not
        left to every producer to independently get right."""
        target = EvidenceTarget(
            namespace="github",
            canonical_ref="https://github.com/octocat",
            aliases=frozenset({"  octocat  ", "   ", ""}),
        )
        assert target.aliases == frozenset({"octocat"})

    def test_identifiers_includes_canonical_ref_and_aliases_but_not_display_name(self):
        """User/Nephy decision, Issue #12 round 1: display_name is
        presentation-only and must never carry claim-binding authority on
        its own -- only canonical_ref and explicitly declared aliases do.
        A connector that wants its display label to be bindable must add it
        to aliases explicitly."""
        target = EvidenceTarget(
            namespace="github",
            canonical_ref="https://github.com/octocat",
            display_name="octocat display label",
            aliases=frozenset({"github account octocat"}),
        )
        assert target.identifiers() == frozenset(
            {"https://github.com/octocat", "github account octocat"}
        )
        assert "octocat display label" not in target.identifiers()

    def test_identifiers_with_no_aliases_is_just_canonical_ref(self):
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat")
        assert target.identifiers() == frozenset({"https://github.com/octocat"})

    def test_to_dict_round_trips_all_fields(self):
        target = EvidenceTarget(
            namespace="github",
            canonical_ref="https://github.com/octocat",
            kind="account",
            display_name="octocat",
            aliases=frozenset({"github account octocat"}),
        )
        assert target.to_dict() == {
            "namespace": "github",
            "canonical_ref": "https://github.com/octocat",
            "kind": "account",
            "display_name": "octocat",
            "aliases": ["github account octocat"],
        }

    def test_evidence_assertion_canonical_target_defaults_to_none(self):
        assertion = EvidenceAssertion.found(target="octocat", source="direct_lookup")
        assert assertion.canonical_target is None

    def test_evidence_assertion_to_dict_includes_null_canonical_target_when_unset(self):
        assertion = EvidenceAssertion.found(target="octocat", source="direct_lookup")
        assert assertion.to_dict()["canonical_target"] is None

    def test_evidence_assertion_to_dict_serializes_canonical_target_when_set(self):
        target = EvidenceTarget(namespace="github", canonical_ref="https://github.com/octocat")
        assertion = EvidenceAssertion.found(target="octocat", source="direct_lookup", canonical_target=target)
        assert assertion.to_dict()["canonical_target"]["canonical_ref"] == "https://github.com/octocat"
