import json

import pytest

from services.contracts import (
    ExecutorRequest,
    InferenceResult,
    InputSituationProfile,
    RetrievalIntent,
    RouterRequest,
    ValidationContext,
)
from services.orchestrator.executor import ToolExecutor
from services.orchestrator.input_profiler import InputSituationProfiler
from services.orchestrator.orchestrator import Orchestrator
from services.orchestrator.retrieval_intent import RetrievalIntentAnalyzer
from services.orchestrator.router import QueryRouter
from services.orchestrator.validator import ResponseValidator


class _InferenceInvoker:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        return InferenceResult(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="test",
        )


class _SequenceInvoker:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_inference_decomposes_unknown_source_without_router_keywords():
    invoker = _InferenceInvoker(
        {
            "requires_external_information": True,
            "goal": "Kartennamen für BRO 118 ermitteln",
            "source_hint": "Scryfall",
            "candidate_url": "https://api.scryfall.com/cards/bro/118/de",
            "search_query": "Scryfall BRO 118 German card",
            "entities": {"set": "BRO", "collector_number": "118", "language": "de"},
            "uncertainties": [],
            "confidence": 0.91,
        }
    )
    profiler = InputSituationProfiler(inference_invoker=invoker, inference_provider="test")

    profile = await profiler.profile("Nenne mir die beiden Namen aus der dortigen Quelle.")

    assert profile.external_information_required is True
    assert profile.context_dependency == "external"
    assert profile.retrieval_intent.source_hint == "Scryfall"
    assert profile.retrieval_intent.candidate_url == "https://api.scryfall.com/cards/bro/118/de"
    assert profile.retrieval_intent.discovery_required is False
    assert invoker.requests[0].task_type == "retrieval_intent_analysis"


@pytest.mark.asyncio
async def test_uncertain_or_unsafe_candidate_becomes_search_discovery():
    invoker = _InferenceInvoker(
        {
            "requires_external_information": True,
            "goal": "Unbekannte Primärquelle finden",
            "source_hint": "Example Source",
            "candidate_url": "file:///etc/passwd",
            "search_query": "Example Source primary documentation",
            "entities": {},
            "uncertainties": ["endpoint uncertain"],
            "confidence": 0.62,
        }
    )

    intent = await RetrievalIntentAnalyzer(invoker, provider="test").analyze("Finde die Primärquelle")

    assert intent.candidate_url is None
    assert intent.discovery_required is True
    assert "candidate_url_failed_contract_validation" in intent.uncertainties


@pytest.mark.asyncio
async def test_candidate_assessment_may_derive_unverified_primary_url():
    invoker = _InferenceInvoker(
        {
            "selected_url": "https://api.primary.example/items/42",
            "refined_search_query": None,
            "confidence": 0.84,
            "uncertainties": ["endpoint must be verified by fetching it"],
        }
    )
    analyzer = RetrievalIntentAnalyzer(invoker, provider="test")
    intent = RetrievalIntent(
        kind="external_retrieval",
        requires_external_information=True,
        goal="item 42",
        source_hint="Primary Example",
        entities={"item": "42"},
        confidence=0.7,
        discovery_required=True,
        inference_status="success",
    )

    refinement = await analyzer.refine(
        intent,
        [{"title": "Primary Example", "url": "https://primary.example/", "snippet": "homepage"}],
    )

    assert refinement["selected_url"] == "https://api.primary.example/items/42"
    assert refinement["confidence"] == 0.84
    assert invoker.requests[0].task_type == "retrieval_candidate_assessment"


@pytest.mark.asyncio
async def test_retrieval_uses_small_provider_for_intent_and_main_provider_for_refinement():
    invoker = _InferenceInvoker(
        {
            "requires_external_information": False,
            "goal": "",
            "source_hint": None,
            "candidate_url": None,
            "search_query": None,
            "entities": {},
            "uncertainties": [],
            "confidence": 0.9,
        }
    )
    analyzer = RetrievalIntentAnalyzer(
        invoker,
        provider="openvino_npu_helper",
        refinement_provider="ll_ol_fallback",
    )

    await analyzer.analyze("hello")
    invoker.payload = {
        "selected_url": "https://example.test/item/42",
        "refined_search_query": None,
        "confidence": 0.9,
        "uncertainties": [],
    }
    await analyzer.refine(
        RetrievalIntent(requires_external_information=True),
        [{"title": "item", "url": "https://example.test/item/42", "snippet": "item 42"}],
    )

    assert invoker.requests[0].provider == "openvino_npu_helper"
    assert invoker.requests[1].provider == "ll_ol_fallback"


@pytest.mark.asyncio
async def test_retrieval_intent_falls_back_when_small_provider_contract_fails():
    invoker = _SequenceInvoker(
        [
            InferenceResult(content="", provider="openvino", model="mini", status="failed", error="bad json"),
            InferenceResult(
                content=json.dumps(
                    {
                        "requires_external_information": True,
                        "goal": "lookup",
                        "source_hint": "Primary Source",
                        "candidate_url": None,
                        "search_query": "record 42",
                        "entities": {"record": "42"},
                        "uncertainties": [],
                        "confidence": 0.8,
                    }
                ),
                provider="test",
                model="main",
            ),
        ]
    )
    analyzer = RetrievalIntentAnalyzer(
        invoker,
        provider="openvino_npu_helper",
        refinement_provider="ll_ol_fallback",
    )

    intent = await analyzer.analyze("lookup record 42")

    assert intent.requires_external_information is True
    assert "intent_primary_provider_fallback" in intent.uncertainties
    assert [request.provider for request in invoker.requests] == [
        "openvino_npu_helper",
        "ll_ol_fallback",
    ]


@pytest.mark.asyncio
async def test_router_consumes_typed_retrieval_intent_before_keyword_fallback():
    profile = InputSituationProfile(
        external_information_required=True,
        context_dependency="external",
        retrieval_intent=RetrievalIntent(
            kind="external_retrieval",
            requires_external_information=True,
            goal="Beleg finden",
            search_query="primary source for item 42",
            confidence=0.8,
            discovery_required=True,
            inference_status="success",
        ),
    )

    decision = await QueryRouter().route(RouterRequest(query="Bitte erledigen.", input_profile=profile))

    assert decision.selected_tools == ["sys"]
    assert decision.reason == "inference_external_retrieval"
    assert decision.metadata["retrieval_intent"]["search_query"] == "primary source for item 42"


def test_retrieval_intent_resolves_false_boolean_when_model_emits_external_plan():
    intent = RetrievalIntentAnalyzer._validate_payload(
        {
            "requires_external_information": False,
            "goal": "Find the requested record",
            "source_hint": "A source inferred by the model",
            "candidate_url": None,
            "search_query": "requested record primary source",
            "entities": {"record": "42"},
            "uncertainties": [],
            "confidence": 0.8,
        },
        fallback_query="find record 42",
    )

    assert intent.requires_external_information is True
    assert intent.discovery_required is True
    assert "model_boolean_conflicted_with_external_plan" in intent.uncertainties


def test_retrieval_intent_accepts_markdown_wrapped_safe_url():
    assert (
        RetrievalIntentAnalyzer._safe_http_url("<https://example.test/items/42>")
        == "https://example.test/items/42"
    )


def test_executor_compiles_discovery_to_read_only_search_page():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(
        tool_names=["sys"],
        query="Bitte erledigen.",
        routing_metadata={
            "intent": "web_discovery",
            "retrieval_intent": {
                "requires_external_information": True,
                "goal": "Beleg finden",
                "source_hint": "Primary Source",
                "search_query": "Primary Source item 42",
                "discovery_required": True,
            },
        },
    )

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters["command"] == "curl"
    assert prepared.parameters["context"] == "agent_web_discovery"
    assert prepared.parameters["args"][:4] == ["-s", "-L", "-m", "15"]
    assert prepared.parameters["args"][-1].startswith("https://www.bing.com/search?format=rss&q=")
    assert "%22Primary+Source%22" in prepared.parameters["args"][-1]


def test_bing_rss_results_are_discovery_only_and_candidate_ranking_uses_inferred_source():
    rss = """<?xml version="1.0"?><rss><channel>
    <item><title>Secondary discussion</title><link>https://example.net/talk</link><description>item 42</description></item>
    <item><title>Primary Source documentation</title><link>https://primary.example/item/42</link><description>official item</description></item>
    </channel></rss>"""
    normalized = ToolExecutor._normalize_sys_output(
        rss,
        {"command": "curl", "context": "agent_web_discovery", "search_query": "item 42"},
    )

    selected = Orchestrator._rank_discovery_candidate(
        normalized["results"],
        {
            "source_hint": "Primary Source",
            "goal": "official item 42 documentation",
            "search_query": "Primary Source item 42",
            "entities": {"item": "42"},
        },
    )
    validation = ResponseValidator().validate(
        ValidationContext(
            original_query="Was ist der Wert?",
            response="Die Suche liefert den Wert 99.",
            tools_used=["sys"],
            tool_outputs={"sys": normalized},
            context_mode="NONE",
            context_sources={},
            context_documents="",
        )
    )

    assert normalized["evidence_scope"] == "discovery"
    assert selected["url"] == "https://primary.example/item/42"
    assert validation.checks["tool_evidence_integrity"] == "fail"
