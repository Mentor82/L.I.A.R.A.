"""Regression tests for Issue #22: complete_web_discovery candidate-rank-and-fetch round trip.

rank_discovery_candidate restores the old, richer contract (candidate list +
retrieval-intent dict with entities -> winning candidate), and
complete_web_discovery restores the LLM-assessment-first / deterministic-
fallback / single-fetch-attempt flow that was dead code before this fix.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.contracts import RetrievalIntent
from services.orchestrator import tool_discovery
from services.orchestrator.tool_discovery import rank_discovery_candidate


# ---------------------------------------------------------------------------
# rank_discovery_candidate
# ---------------------------------------------------------------------------

def _result(title: str, url: str, snippet: str = "") -> dict:
    return {"title": title, "url": url, "snippet": snippet}


def test_source_hint_substring_bonus_outranks_weaker_token_overlap():
    results = [
        _result("Generic mirror with item 42", "https://mirror.example/x", "item 42 discussion"),
        _result("Primary Source docs", "https://primary.example/item/42", "official item"),
    ]
    retrieval = {"source_hint": "Primary Source", "goal": "item", "search_query": "", "entities": {}}
    selected = rank_discovery_candidate(results=results, retrieval=retrieval)
    assert selected["url"] == "https://primary.example/item/42"


def test_entities_fold_into_semantic_token_overlap():
    results = [
        _result("Unrelated page", "https://example.net/other", "nothing relevant"),
        _result("VINOX documentation", "https://example.net/vinox", "official VINOX record"),
    ]
    retrieval = {"source_hint": "", "goal": "", "search_query": "", "entities": {"item": "VINOX"}}
    selected = rank_discovery_candidate(results=results, retrieval=retrieval)
    assert selected["url"] == "https://example.net/vinox"


def test_positional_decay_breaks_ties_toward_earlier_rank():
    results = [
        _result("Alpha", "https://a.example/1", "shared token banana"),
        _result("Beta", "https://b.example/2", "shared token banana"),
    ]
    retrieval = {"source_hint": "", "goal": "banana", "search_query": "", "entities": {}}
    selected = rank_discovery_candidate(results=results, retrieval=retrieval)
    assert selected["url"] == "https://a.example/1"
    assert selected["rank"] == 1


def test_only_first_eight_results_considered_and_invalid_urls_skipped():
    results = [_result(f"Filler {i}", "not-a-url", "") for i in range(8)]
    results.append(_result("Winner", "https://winner.example/z", "banana"))
    retrieval = {"source_hint": "", "goal": "banana", "search_query": "", "entities": {}}
    selected = rank_discovery_candidate(results=results, retrieval=retrieval)
    assert selected is None


def test_returns_none_when_nothing_qualifies():
    retrieval = {"source_hint": "", "goal": "banana", "search_query": "", "entities": {}}
    assert rank_discovery_candidate(results=[], retrieval=retrieval) is None


# ---------------------------------------------------------------------------
# complete_web_discovery
# ---------------------------------------------------------------------------

def _discovery_tool_results(results=None, query="item 42") -> dict:
    return {
        "sys": {
            "source": "sys",
            "kind": "web_discovery",
            "query": query,
            "results": results if results is not None else [
                _result("Secondary discussion", "https://example.net/talk", "item 42"),
                _result("Primary Source documentation", "https://primary.example/item/42", "official item"),
            ],
            "candidate_count": 2,
            "evidence_scope": "discovery",
            "summary_text": "discovery summary",
        },
    }


def _make_orchestrator(*, refine_result=None, fetch_result=None):
    retrieval_intent = RetrievalIntent(
        kind="external_retrieval",
        requires_external_information=True,
        goal="official item 42 documentation",
        source_hint="Primary Source",
        search_query="Primary Source item 42",
        entities={"item": "42"},
        discovery_required=True,
        inference_status="success",
        confidence=0.8,
    )
    orch = SimpleNamespace()
    orch._active_input_profile = SimpleNamespace(retrieval_intent=retrieval_intent)
    orch.input_profiler = SimpleNamespace(
        refine_retrieval=AsyncMock(
            return_value=refine_result or {"selected_url": None, "confidence": 0.0, "inference_status": "not_run"}
        )
    )
    orch._last_executor_debug = {}
    orch._last_route_debug = {"metadata": {"pre_existing": "should_be_restored"}}
    orch._execute_tools = AsyncMock(
        return_value=fetch_result if fetch_result is not None else {}
    )
    return orch


@pytest.mark.asyncio
async def test_non_discovery_tool_results_pass_through_unchanged():
    orch = _make_orchestrator()
    tool_results = {"sys": {"kind": "url_fetch", "content": "already fetched"}}
    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")
    assert result is tool_results
    orch.input_profiler.refine_retrieval.assert_not_called()


@pytest.mark.asyncio
async def test_missing_sys_key_passes_through_unchanged():
    orch = _make_orchestrator()
    tool_results = {"wsl_session": {"status": "ready"}}
    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")
    assert result is tool_results


@pytest.mark.asyncio
async def test_llm_assessment_wins_when_confidence_high():
    orch = _make_orchestrator(
        refine_result={"selected_url": "https://primary.example/item/42", "confidence": 0.9},
        fetch_result={"sys": {"source": "sys", "kind": "url_fetch", "content": "fetched page body"}},
    )
    tool_results = _discovery_tool_results()

    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")

    assert result["sys::discovery"]["selected_candidate"]["selection_source"] == "inference_candidate_assessment"
    assert result["sys"]["content"] == "fetched page body"
    assert result["sys"]["retrieval_provenance"]["candidate_url"] == "https://primary.example/item/42"
    # routing metadata mutation must not leak past the nested call
    assert orch._last_route_debug == {"metadata": {"pre_existing": "should_be_restored"}}


@pytest.mark.asyncio
async def test_deterministic_fallback_wins_when_llm_assessment_low_confidence():
    orch = _make_orchestrator(
        refine_result={"selected_url": "https://weak.example/guess", "confidence": 0.2},
        fetch_result={"sys": {"source": "sys", "kind": "url_fetch", "content": "fetched primary source page"}},
    )
    tool_results = _discovery_tool_results()

    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")

    candidate = result["sys::discovery"]["selected_candidate"]
    assert candidate["url"] == "https://primary.example/item/42"
    assert "selection_source" not in candidate


@pytest.mark.asyncio
async def test_no_candidate_short_circuits_without_fetch():
    orch = _make_orchestrator()
    tool_results = _discovery_tool_results(results=[])

    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")

    assert result is tool_results
    orch._execute_tools.assert_not_called()
    assert orch._last_executor_debug["retrieval_discovery"]["status"] == "no_candidate"


@pytest.mark.asyncio
async def test_failed_fetch_leaves_sys_absent_no_retry_to_second_candidate():
    orch = _make_orchestrator(
        refine_result={"selected_url": "https://primary.example/item/42", "confidence": 0.9},
        fetch_result={},  # no "sys" key -- fetch failed/blocked
    )
    tool_results = _discovery_tool_results()

    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")

    assert "sys" not in result
    assert "sys::discovery" in result
    assert orch._last_executor_debug["retrieval_discovery"]["status"] == "candidate_failed"
    orch._execute_tools.assert_called_once()


@pytest.mark.asyncio
async def test_blocked_or_failed_refetch_never_becomes_grounding():
    """Nephy hardening #1: a non-empty "sys" payload from a blocked/failed
    re-fetch must never be treated as successfully fetched evidence -- only
    a genuine kind=="url_fetch" result may become grounding."""
    for failing_payload in (
        {"sys": {"status": "blocked", "error": "Pre-action judge blocked execution: block"}},
        {"sys": {"kind": "tool_execution_failure", "status": "failed", "evidence": False, "error": "curl failed"}},
    ):
        orch = _make_orchestrator(
            refine_result={"selected_url": "https://primary.example/item/42", "confidence": 0.9},
            fetch_result=failing_payload,
        )
        tool_results = _discovery_tool_results()

        result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")

        assert "sys" not in result
        assert "sys::discovery" in result
        assert orch._last_executor_debug["retrieval_discovery"]["status"] == "candidate_failed"


@pytest.mark.asyncio
async def test_llm_selected_url_outside_discovered_candidates_is_rejected():
    """Nephy hardening #2: refinement may only select AMONG discovered
    candidates. Discovery returns A/B/C; refinement proposes D (never
    discovered) with high confidence -> D must never be fetched, the
    deterministic fallback must pick from A/B/C instead."""
    tool_results = _discovery_tool_results(results=[
        _result("Candidate A", "https://a.example/one", "banana A"),
        _result("Candidate B", "https://b.example/two", "banana B"),
        _result("Candidate C", "https://c.example/three", "banana C"),
    ])
    orch = _make_orchestrator(
        refine_result={"selected_url": "https://d.example/hallucinated", "confidence": 0.95},
        fetch_result={"sys": {"source": "sys", "kind": "url_fetch", "content": "fetched fallback candidate"}},
    )

    result = await tool_discovery.complete_web_discovery(orch, tool_results=tool_results, run_id="r1")

    candidate = result["sys::discovery"]["selected_candidate"]
    assert candidate["url"] in {"https://a.example/one", "https://b.example/two", "https://c.example/three"}
    assert candidate.get("selection_source") != "inference_candidate_assessment"
    fetched_url = orch._execute_tools.call_args.args[1]
    assert fetched_url != "https://d.example/hallucinated"
