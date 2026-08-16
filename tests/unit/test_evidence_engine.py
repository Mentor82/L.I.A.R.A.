from services.orchestrator.evidence_engine import EvidenceEngine


def test_select_sources_does_not_match_age_inside_language():
    engine = EvidenceEngine()

    selection = engine._select_sources(
        query="Wie muesste ein cross-language call zwischen Julia und Python aussehen?",
        required_level="medium",
        source_counts={"facts": 0, "qdrant": 0, "chroma": 0},
        tool_outputs={},
        has_history=False,
    )

    assert "facts" not in selection["selected_sources"]
    assert selection["selected_sources"] == ["user_input_only"]


def test_decompose_design_query_uses_medium_evidence_level():
    engine = EvidenceEngine()

    decomposition = engine._decompose_query(
        "Analysiere systemisch, warum ein hybrider Julia/Python-Ansatz mit SIMD, GIL und LLVM-JIT fuer ein Systemdesign sinnvoll ist."
    )

    assert decomposition["required_evidence_level"] == "medium"


def test_low_evidence_self_description_keeps_canonical_system_context():
    engine = EvidenceEngine()

    result = engine.analyze(
        query="Describe LIARA in one short sentence.",
        context_channels={
            "system_context": (
                "Identity: Liara (AI-Orchestrator and Agent)\n"
                "Description: Liara is a structured deterministic AI orchestrator."
            )
        },
        source_counts={"system": 1, "facts": 0, "qdrant": 0, "chroma": 0},
        tool_outputs={},
        conversation_history="",
    )

    assert "system" in result.selected_sources
    assert any(item.get("source") == "system" for item in result.evidence_items)
    assert "Liara" in result.evidence_context


# ---------------------------------------------------------------------------
# Issue #8: evidence-state classification (absence-of-evidence != evidence-of-absence)
# ---------------------------------------------------------------------------

def _analyze_tool_outputs(tool_outputs: dict, query: str = "octocat GitHub account"):
    engine = EvidenceEngine()
    return engine.analyze(
        query=query,
        context_channels={},
        source_counts={},
        tool_outputs=tool_outputs,
        conversation_history="",
    )


def test_search_miss_stays_not_found_never_does_not_exist():
    result = _analyze_tool_outputs(
        {
            "web_search": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "candidate_count": 0,
                "results": [],
                "summary_text": "No parseable search candidates were returned.",
            }
        }
    )
    assert len(result.evidence_states) == 1
    assert result.evidence_states[0]["state"] == "not_found_in_search"
    assert result.evidence_states[0]["state"] != "does_not_exist_confirmed"
    # EvidenceItem.quality must reflect the weak state, not a blanket "verified".
    assert result.evidence_items[0]["quality"] == "insufficient"
    assert result.evidence_items[0]["evidence_state"] == "not_found_in_search"


def test_malformed_tool_output_stays_unresolved():
    result = _analyze_tool_outputs(
        {"browser_connector": {"unexpected_field": "garbage", "raw": "<binary-ish>"}}
    )
    assert len(result.evidence_states) == 1
    state = result.evidence_states[0]["state"]
    assert state == "unresolved"
    assert state not in {"connector_unavailable", "not_found_in_search", "does_not_exist_confirmed"}


def test_access_denied_stays_access_denied_not_private():
    result = _analyze_tool_outputs({"github_api": {"status": "denied", "error": "403 Forbidden"}})
    assert len(result.evidence_states) == 1
    assert result.evidence_states[0]["state"] == "access_denied"
    assert result.evidence_states[0]["state"] != "private_confirmed"


def test_private_confirmed_requires_real_evidence_confirmation():
    import pytest
    from services.contracts.evidence_state import EvidenceAssertion

    with pytest.raises(ValueError):
        EvidenceAssertion.private_confirmed(target="octocat", source="github_api", confirmed_by="i just know")  # type: ignore[arg-type]


def test_search_miss_then_direct_lookup_merges_to_found_in_one_call():
    """One analyze() call whose tool_outputs contains both a discovery-scope
    zero-result search and a direct-lookup success for the same target must
    merge to FOUND -- the 'search miss, then successful direct lookup
    revises cleanly' scenario, without needing any cross-call state."""
    result = _analyze_tool_outputs(
        {
            "web_search": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "candidate_count": 0,
                "results": [],
                "summary_text": "No parseable search candidates were returned.",
            },
            "github_api": {
                "status": "success",
                "summary_text": "octocat GitHub account: 100000+ followers.",
            },
        },
        query="octocat GitHub account",
    )
    assert len(result.evidence_states) == 1
    assert result.evidence_states[0]["state"] == "found"


def test_connector_execution_failure_stays_connector_unavailable():
    result = _analyze_tool_outputs({"github_api": {"status": "failed", "error": "connection timeout"}})
    assert len(result.evidence_states) == 1
    assert result.evidence_states[0]["state"] == "connector_unavailable"


def test_403_with_generic_error_status_is_access_denied_not_connector_unavailable():
    """Nephy round 2: a real 401/403 commonly surfaces as status='error' with
    the code embedded in the error text, not status='denied'. Must still
    classify as ACCESS_DENIED so the ACCESS_DENIED != PRIVATE guard applies
    -- not CONNECTOR_UNAVAILABLE, which would silently bypass that guard."""
    result = _analyze_tool_outputs({"github_api": {"status": "error", "error": "403 Forbidden"}})
    assert len(result.evidence_states) == 1
    assert result.evidence_states[0]["state"] == "access_denied"
    assert result.evidence_states[0]["state"] != "connector_unavailable"

    result_401 = _analyze_tool_outputs({"github_api": {"status": "error", "error": "401 Unauthorized"}})
    assert result_401.evidence_states[0]["state"] == "access_denied"


def test_two_tool_calls_with_distinct_query_fields_stay_separate_targets():
    """Nephy round 3: every classified assertion used to get target=query --
    the *overall*, possibly multi-entity request text -- so two tool calls
    investigating two different things in one turn collapsed onto the same
    target and got silently merged into one. web_discovery-style outputs
    carry their own per-call "query" field; that must now be used as the
    target instead, keeping the two observations distinct."""
    result = _analyze_tool_outputs(
        {
            "web_search_a": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "query": "octocat github account",
                "candidate_count": 1,
                "results": [{"title": "octocat", "url": "https://github.com/octocat"}],
                "summary_text": "octocat: found",
            },
            "web_search_b": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "query": "definitely-nonexistent-user-xyz github account",
                "candidate_count": 0,
                "results": [],
                "summary_text": "No parseable search candidates were returned.",
            },
        },
        query="Check octocat and definitely-nonexistent-user-xyz on GitHub",
    )
    assert len(result.evidence_states) == 2
    by_target = {item["target"]: item["state"] for item in result.evidence_states}
    assert by_target["octocat github account"] == "found"
    assert by_target["definitely-nonexistent-user-xyz github account"] == "not_found_in_search"


def test_tool_output_without_its_own_query_field_falls_back_to_overall_query():
    result = _analyze_tool_outputs(
        {"github_api": {"status": "success", "summary_text": "octocat: 100000+ followers."}},
        query="octocat GitHub account",
    )
    assert len(result.evidence_states) == 1
    assert result.evidence_states[0]["target"] == "octocat GitHub account"


# ---------------------------------------------------------------------------
# Issue #12: canonical target identity attached to classified assertions
# ---------------------------------------------------------------------------

def test_canonical_ref_and_namespace_attach_evidence_target():
    result = _analyze_tool_outputs(
        {
            "github_api": {
                "status": "success",
                "summary_text": "octocat: found",
                "canonical_ref": "https://github.com/octocat",
                "canonical_namespace": "github",
            }
        }
    )
    assert len(result.evidence_states) == 1
    canonical = result.evidence_states[0]["canonical_target"]
    assert canonical is not None
    assert canonical["namespace"] == "github"
    assert canonical["canonical_ref"] == "https://github.com/octocat"


def test_canonical_ref_without_namespace_stays_unresolved():
    """Nephy mandate 2: never guess one field from the other."""
    result = _analyze_tool_outputs(
        {"github_api": {"status": "success", "summary_text": "octocat: found", "canonical_ref": "https://github.com/octocat"}}
    )
    assert result.evidence_states[0]["canonical_target"] is None


def test_canonical_namespace_without_ref_stays_unresolved():
    result = _analyze_tool_outputs(
        {"github_api": {"status": "success", "summary_text": "octocat: found", "canonical_namespace": "github"}}
    )
    assert result.evidence_states[0]["canonical_target"] is None


def test_canonical_aliases_are_captured_from_the_declaring_observation():
    result = _analyze_tool_outputs(
        {
            "github_api": {
                "status": "success",
                "summary_text": "octocat: found",
                "canonical_ref": "https://github.com/octocat",
                "canonical_namespace": "github",
                "canonical_aliases": ["octocat", "github account octocat"],
            }
        }
    )
    assert set(result.evidence_states[0]["canonical_target"]["aliases"]) == {"octocat", "github account octocat"}


def test_single_string_canonical_alias_is_a_singleton_not_exploded_into_characters():
    """Nephy plan-review correction, hardening point 1: a naive
    'iterable -> frozenset' would explode a plain string character-by-
    character, since str is itself iterable."""
    result = _analyze_tool_outputs(
        {
            "github_api": {
                "status": "success",
                "summary_text": "octocat: found",
                "canonical_ref": "https://github.com/octocat",
                "canonical_namespace": "github",
                "canonical_aliases": "octocat",
            }
        }
    )
    assert result.evidence_states[0]["canonical_target"]["aliases"] == ["octocat"]


def test_existing_classification_branches_unchanged_without_canonical_fields():
    """Regression anchor for the _classify_tool_output_state rename/wrap:
    pre-existing scenarios without any canonical_ref field must classify
    identically to before Issue #12."""
    search_miss = _analyze_tool_outputs(
        {
            "web_search": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "candidate_count": 0,
                "results": [],
            }
        }
    )
    assert search_miss.evidence_states[0]["state"] == "not_found_in_search"
    assert search_miss.evidence_states[0]["canonical_target"] is None

    access_denied = _analyze_tool_outputs({"github_api": {"status": "denied", "error": "403 Forbidden"}})
    assert access_denied.evidence_states[0]["state"] == "access_denied"
    assert access_denied.evidence_states[0]["canonical_target"] is None

    malformed = _analyze_tool_outputs({"browser_connector": {"unexpected_field": "garbage"}})
    assert malformed.evidence_states[0]["state"] == "unresolved"
    assert malformed.evidence_states[0]["canonical_target"] is None


def test_two_tool_calls_with_distinct_canonical_ref_stay_separate_targets():
    """Mirrors test_two_tool_calls_with_distinct_query_fields_stay_separate_targets,
    but proving separation via canonical_ref instead of the free-text query
    field -- the parent/child case (account vs. repo) survives classify+merge."""
    result = _analyze_tool_outputs(
        {
            "web_search_account": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "query": "octocat",
                "candidate_count": 1,
                "results": [{"title": "octocat", "url": "https://github.com/octocat"}],
                "canonical_ref": "https://github.com/octocat",
                "canonical_namespace": "github_user",
            },
            "web_search_repo": {
                "kind": "web_discovery",
                "evidence_scope": "discovery",
                "query": "octocat/Hello-World",
                "candidate_count": 0,
                "results": [],
                "canonical_ref": "https://github.com/octocat/Hello-World",
                "canonical_namespace": "github_repo",
            },
        },
        query="Does octocat and definitely-nonexistent-octocat-repo exist on GitHub",
    )
    assert len(result.evidence_states) == 2
    by_ref = {item["canonical_target"]["canonical_ref"]: item["state"] for item in result.evidence_states}
    assert by_ref["https://github.com/octocat"] == "found"
    assert by_ref["https://github.com/octocat/Hello-World"] == "not_found_in_search"
