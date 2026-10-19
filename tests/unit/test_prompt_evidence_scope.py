"""Grounding must not turn self-contained reasoning into a retrieval task."""

import pytest

from services.contracts import PlannerRequest
from services.orchestrator.planner import QueryPlanner


@pytest.mark.parametrize("route", ["DIRECT", "FACT_LOOKUP", "SESSION_RECALL", "SEMANTIC_MEMORY"])
@pytest.mark.parametrize("context", ["", "Unrelated historical context"])
def test_calculation_scope_is_present_for_every_context_route(route, context):
    planner = QueryPlanner()
    plan = planner.build_plan(PlannerRequest(
        query="Was ist 2 + 2?", primary_context_kind=route, memory_context=context,
        tools_used=[], tool_outputs={},
    ))
    assert "For self-contained arithmetic, mathematical or logical derivations" in plan.prompt
    assert "missing retrieved context is not a reason to refuse or ask for sources" in plan.prompt
    assert "Do not add a fabricated source or [KNOWLEDGE_REFERENCE] to your own calculation" in plan.prompt
    # Existing configured system policy is retained; the renderer scopes its
    # retrieval rules rather than silently replacing stored configuration.
    assert planner.system_context in plan.prompt


@pytest.mark.parametrize("query", [
    "Welche Dienste laufen gerade?",
    "Was ist mein gespeichertes Passwort?",
    "Was kosten 12 Artikel zum aktuellen Marktpreis?",
])
def test_empirical_grounding_and_missing_input_rules_remain(query):
    plan = QueryPlanner().build_plan(PlannerRequest(
        query=query, primary_context_kind="FACT_LOOKUP", fact_context="No verified matching fact available",
        tools_used=[], tool_outputs={},
    ))
    assert "Every factual claim from [TOOL]" in plan.prompt
    assert "Never invent facts or citations" in plan.prompt
    assert "request or retrieve those inputs instead of inventing them" in plan.prompt
    assert "do not relax evidence requirements for empirical claims or permissions for tools" in plan.prompt
    assert "Before emitting any factual claim" not in plan.prompt
