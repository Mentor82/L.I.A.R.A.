import pytest

from services.contracts import InputEnvironmentProfile, InputSituationProfile, RouterRequest
from services.orchestrator.input_profiler import InputSituationProfiler
from services.orchestrator.planner import QueryPlanner
from services.orchestrator.router import QueryRouter
from services.contracts import PlannerRequest


@pytest.mark.asyncio
async def test_profiles_direct_playful_turn():
    profile = await InputSituationProfiler().profile("Das erklärt sich ja fast selbst :D")

    assert profile.processing_level == "answer"
    assert profile.recommended_path == "direct_answer"
    assert profile.mood.label == "playful"
    assert profile.processing_chain == ["analyze", "answer"]


@pytest.mark.asyncio
async def test_profiles_liara_architecture_analysis():
    profile = await InputSituationProfiler().profile(
        "Analysiere, warum der LIARA Orchestrator den Validator an dieser Stelle braucht."
    )

    assert profile.processing_level == "think"
    assert profile.domain == "ai_architecture"
    assert profile.resource_budget.max_reasoning_depth == 2


@pytest.mark.asyncio
async def test_profiles_workspace_action_and_bounded_budget():
    profile = await InputSituationProfiler().profile(
        "Implementiere den Worker im WSL Workspace, schreibe Tests und führe pytest aus.",
        workspace_available=True,
        max_tokens=32768,
    )

    assert profile.processing_level == "act"
    assert profile.recommended_path == "plan_act"
    assert profile.context_dependency == "workspace"
    assert profile.resource_budget.max_reasoning_depth == 5
    assert profile.resource_budget.tool_budget == 5
    assert profile.environment.max_tokens == 32768


@pytest.mark.asyncio
async def test_profiles_conversation_dependency_from_message_and_history():
    profile = await InputSituationProfiler().profile(
        "Und was hatten wir gerade dazu beschlossen?",
        conversation_history="USER: Wir verwenden das Situationsprofil.\nASSISTANT: Einverstanden.",
    )

    assert profile.context_dependency == "conversation"
    assert profile.external_information_required is False
    assert profile.environment.session_history_available is True


@pytest.mark.asyncio
async def test_profiles_external_current_information():
    profile = await InputSituationProfiler().profile("Wie ist das aktuelle Wetter heute in Berlin?")

    assert profile.context_dependency == "external"
    assert profile.external_information_required is True
    assert profile.resource_budget.tool_budget >= 1


@pytest.mark.asyncio
async def test_profiles_local_hardware_as_system_not_web_context():
    profile = await InputSituationProfiler().profile("Prüfe bitte lokal Akku- und CPU-Temperatur.")

    assert profile.context_dependency == "system"
    assert profile.external_information_required is False
    assert profile.resource_budget.tool_budget >= 1


@pytest.mark.asyncio
async def test_router_uses_profile_property_for_local_recall():
    profile = InputSituationProfile(
        context_dependency="conversation",
        confidence=0.9,
        environment=InputEnvironmentProfile(session_history_available=True),
    )
    decision = await QueryRouter().route(
        RouterRequest(query="Wie war das noch?", input_profile=profile)
    )

    assert decision.selected_tools == []
    assert decision.reason == "input_profile_local_context"


def test_planner_exposes_profile_without_granting_permissions():
    profile = InputSituationProfile(
        processing_level="think",
        processing_chain=["analyze", "think", "answer"],
        recommended_path="think_answer",
        domain="ai_architecture",
    )
    plan = QueryPlanner().build_plan(
        PlannerRequest(query="Analysiere das.", tools_used=[], tool_outputs={}, input_profile=profile)
    )

    assert "[INPUT_SITUATION_PROFILE]" in plan.prompt
    assert '"processing_level": "think"' in plan.prompt
    assert "never grants permissions" in plan.prompt
    assert plan.metadata["input_profile_schema"] == "liara.input-situation.v1"
