from __future__ import annotations

from typing import Any, Dict, List

from services.orchestrator.orchestrator import Orchestrator


def _build_metrics_for_scenario(
    *,
    query: str,
    response: str,
    tools_used: List[str],
    context_debug: Dict[str, Any],
    validation_decision: str,
    retry_count: int,
    failed_tools: List[str],
) -> Dict[str, Any]:
    inputs = Orchestrator._derive_reasoning_metric_inputs(
        query=query,
        response=response,
        tools_used=tools_used,
        context_debug=context_debug,
        validation_decision=validation_decision,
        retry_count=retry_count,
        failed_tools=failed_tools,
    )
    return Orchestrator._compute_reasoning_metrics_snapshot_python(inputs)


def test_live_chat_scenarios_flat_deep_tool_heavy_uncertain() -> None:
    flat = _build_metrics_for_scenario(
        query="Kurze Frage: 2+2?",
        response="4",
        tools_used=[],
        context_debug={"sources": {"redis": 8}},
        validation_decision="accept",
        retry_count=0,
        failed_tools=[],
    )

    deep = _build_metrics_for_scenario(
        query="Bitte leite in mehreren Schritten die Gleichung her.",
        response="Mehrstufige Herleitung mit mehreren Zwischenresultaten.",
        tools_used=[],
        context_debug={"sources": {"redis": 8}},
        validation_decision="accept",
        retry_count=5,
        failed_tools=[],
    )

    tool_heavy = _build_metrics_for_scenario(
        query="Suche, lese und kombiniere mehrere Quellen zu einem Ergebnis.",
        response="Zusammenfassung aus mehreren Tool-Aufrufen.",
        tools_used=["web_search", "read_file", "list_files", "sys"],
        context_debug={"sources": {"redis": 8}},
        validation_decision="accept",
        retry_count=0,
        failed_tools=[],
    )

    uncertain = _build_metrics_for_scenario(
        query="Die Quellen widersprechen sich, was ist die verlässlichste Aussage?",
        response="Es gibt Konflikte und offene Punkte; konservative Bewertung folgt.",
        tools_used=["web_search"],
        context_debug={
            "sources": {"redis": 3, "qdrant": 3, "chroma": 3},
            "conflict_ratio": 0.9,
            "unresolved_ratio": 0.8,
        },
        validation_decision="block",
        retry_count=1,
        failed_tools=["web_search"],
    )

    # flach vs tief: more retries increase depth and complexity/cost.
    assert deep["depth"] > flat["depth"]
    assert deep["rds_v2"] > flat["rds_v2"]
    assert deep["total_cost"] > flat["total_cost"]

    # tool-lastig: same depth but significantly higher tool cost and total cost.
    assert tool_heavy["depth"] == flat["depth"]
    assert tool_heavy["tool_cost"] > flat["tool_cost"]
    assert tool_heavy["total_cost"] > flat["total_cost"]

    # unsicher: elevated entropy + policy risk should raise total risk and context entropy.
    assert uncertain["context_entropy"] > flat["context_entropy"]
    assert uncertain["total_risk"] > flat["total_risk"]
    assert uncertain["total_risk"] > tool_heavy["total_risk"]
