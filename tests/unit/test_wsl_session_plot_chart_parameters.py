"""Regression tests for Issue #16: wsl_session/plot_chart real tool-call parameters.

Covers the wsl_session_selector heuristic classifier, ToolExecutor's new
_build_wsl_session_parameters/_build_plot_chart_parameters branches, and the
session-scoped wsl_session_id tracking (populate on create, resolve on
follow-up actions, clear on matching destroy -- no stale reuse).
"""

import pytest

from services.contracts import ExecutorRequest, ToolExecutionRequest, ToolExecutionResult
from services.orchestrator import tool_discovery
from services.orchestrator.executor import ToolExecutor
from services.orchestrator.wsl_session_selector import select_wsl_session_action
from services.tools.builtin.plot_chart import PlotChartTool
from services.tools.builtin.wsl_session import WslSessionTool


# ---------------------------------------------------------------------------
# select_wsl_session_action
# ---------------------------------------------------------------------------

def test_classifies_create_german():
    assert select_wsl_session_action("Bitte erstelle eine neue WSL Sitzung").action == "create"


def test_classifies_create_english():
    assert select_wsl_session_action("please start a new session").action == "create"


def test_classifies_status_german():
    assert select_wsl_session_action("wie ist der status meiner sitzung").action == "status"


def test_classifies_status_english():
    assert select_wsl_session_action("check status of my wsl session").action == "status"


def test_classifies_destroy_english():
    assert select_wsl_session_action("please destroy the session now").action == "destroy"


def test_classifies_destroy_german():
    assert select_wsl_session_action("bitte lösche die sitzung").action == "destroy"


def test_defaults_to_plan_on_ambiguous_query():
    selection = select_wsl_session_action("hello there")
    assert selection.action == "plan"
    assert selection.matched_keyword is None


def test_defaults_to_plan_on_empty_query():
    assert select_wsl_session_action("").action == "plan"


def test_extracts_explicit_session_id_from_query():
    selection = select_wsl_session_action("collect artifacts for sess-deadbeefcafebabe0000000000")
    assert selection.explicit_session_id == "sess-deadbeefcafebabe0000000000"
    assert selection.action == "collect"


def test_negated_destroy_english_resolves_to_the_actual_intent():
    """A destroy keyword under negation must never win just by substring
    presence -- once filtered out, the single remaining (non-negated) match
    is the real intent, not a priority-order pick of "destroy"."""
    selection = select_wsl_session_action("don't destroy it, just show status")
    assert selection.action == "status"


def test_negated_destroy_german_resolves_to_the_actual_intent():
    selection = select_wsl_session_action("nicht löschen, nur Status")
    assert selection.action == "status"


def test_competing_non_negated_signals_fail_closed_to_plan():
    """Genuine ambiguity (both destroy and status keywords present, neither
    negated) must fail closed to "plan" rather than picking a winner by
    category precedence -- destroy is too consequential to guess."""
    selection = select_wsl_session_action("should I destroy it or just check status")
    assert selection.action == "plan"


# ---------------------------------------------------------------------------
# ToolExecutor.prepare_tool_requests round-trips
# ---------------------------------------------------------------------------

def test_executor_builds_valid_wsl_session_create_parameters():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(tool_names=["wsl_session"], query="create a new wsl session")

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters == {"action": "create"}
    WslSessionTool()._validate_parameters(**prepared.parameters)


def test_executor_resolves_session_id_from_routing_metadata_for_status():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(
        tool_names=["wsl_session"],
        query="what is the status of the session",
        routing_metadata={"wsl_session_id": "sess-abc123abc123abc123abc1230"},
    )

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters == {"action": "status", "session_id": "sess-abc123abc123abc123abc1230"}
    WslSessionTool()._validate_parameters(**prepared.parameters)


def test_executor_prefers_explicit_session_id_in_query_over_routing_metadata():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(
        tool_names=["wsl_session"],
        query="collect artifacts for sess-deadbeefcafebabe0000000000",
        routing_metadata={"wsl_session_id": "sess-staledeadstaledead00000000"},
    )

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters["session_id"] == "sess-deadbeefcafebabe0000000000"


def test_executor_leaves_session_id_unset_when_none_available():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(tool_names=["wsl_session"], query="check the status please")

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters == {"action": "status"}
    assert "session_id" not in prepared.parameters


def test_executor_plan_action_never_carries_session_id():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(
        tool_names=["wsl_session"],
        query="something unrelated",
        routing_metadata={"wsl_session_id": "sess-abc123abc123abc123abc1230"},
    )

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters == {"action": "plan"}
    WslSessionTool()._validate_parameters(**prepared.parameters)


def test_executor_builds_plot_chart_bar_type_from_query():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(tool_names=["plot_chart"], query="show a bar chart of monthly revenue")

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters["chart_type"] == "bar"
    PlotChartTool()._validate_parameters(**prepared.parameters)


def test_executor_builds_plot_chart_line_default():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(tool_names=["plot_chart"], query="plot temperature over time")

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters["chart_type"] == "line"
    PlotChartTool()._validate_parameters(**prepared.parameters)


def test_executor_builds_plot_chart_title_from_query():
    executor = ToolExecutor(tool_coordinator=None)
    request = ExecutorRequest(tool_names=["plot_chart"], query="plot quarterly earnings")

    prepared = executor.prepare_tool_requests(request)[0]

    assert prepared.parameters["title"] == "quarterly earnings"


# ---------------------------------------------------------------------------
# Session-scoped session_id tracking through tool_discovery.execute_tools
# ---------------------------------------------------------------------------

class _FakeToolCoordinator:
    """Mimics ToolCoordinator.execute_tools_parallel for wsl_session only."""

    async def execute_tools_parallel(self, requests: list[ToolExecutionRequest]):
        results = {}
        for req in requests:
            if req.tool_name == "wsl_session":
                if req.parameters.get("action") == "create":
                    output = {"session_id": "sess-fakecreated0000000000000001", "state": "ready"}
                else:
                    output = {"session_id": req.parameters.get("session_id", ""), "state": "ready"}
                results[req.tool_name] = ToolExecutionResult(
                    tool_name=req.tool_name, status="success", output=output,
                )
            else:
                results[req.tool_name] = ToolExecutionResult(
                    tool_name=req.tool_name, status="success", output={},
                )
        return results


class _FakeOrchestrator:
    def __init__(self):
        self.executor = ToolExecutor(tool_coordinator=_FakeToolCoordinator())
        self._wsl_session_by_chat_session: dict[str, str] = {}
        self._active_session_id = "chat-session-1"
        self._active_user_id = "user-1"


@pytest.mark.asyncio
async def test_wsl_session_id_tracked_across_create_and_status_calls():
    orchestrator = _FakeOrchestrator()
    session_id = "chat-session-1"

    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="erstelle eine neue session", session_id=session_id,
    )
    assert orchestrator._wsl_session_by_chat_session[session_id] == "sess-fakecreated0000000000000001"

    result = await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="wie ist der status", session_id=session_id,
    )
    assert result["wsl_session"]["session_id"] == "sess-fakecreated0000000000000001"


@pytest.mark.asyncio
async def test_wsl_session_create_status_destroy_no_stale_reuse():
    orchestrator = _FakeOrchestrator()
    session_id = "chat-session-1"

    # 1) create -> populates tracking
    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="erstelle eine neue session", session_id=session_id,
    )
    assert orchestrator._wsl_session_by_chat_session[session_id] == "sess-fakecreated0000000000000001"

    # 2) status -> auto-resolves the tracked session id
    status_result = await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="check status of my session", session_id=session_id,
    )
    assert status_result["wsl_session"]["session_id"] == "sess-fakecreated0000000000000001"

    # 3) destroy -> succeeds, must clear the tracked binding
    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="destroy the session now", session_id=session_id,
    )
    assert session_id not in orchestrator._wsl_session_by_chat_session

    # 4) status again -> no stale reuse of the destroyed session id
    final_status = await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="check status of my session", session_id=session_id,
    )
    assert final_status["wsl_session"]["session_id"] == ""


@pytest.mark.asyncio
async def test_destroy_of_unrelated_session_id_does_not_clear_tracked_binding():
    """A destroy call resolving to a session id that does NOT match the
    currently tracked one must not wipe out the tracked binding."""
    orchestrator = _FakeOrchestrator()
    session_id = "chat-session-1"

    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="erstelle eine neue session", session_id=session_id,
    )
    tracked_id = orchestrator._wsl_session_by_chat_session[session_id]
    assert tracked_id == "sess-fakecreated0000000000000001"

    # Explicit destroy of a different, unrelated session id (literal id in query).
    await tool_discovery.execute_tools(
        orchestrator,
        selected_tools=["wsl_session"],
        query="destroy session sess-deadbeef0000111122223333",
        session_id=session_id,
    )

    assert orchestrator._wsl_session_by_chat_session[session_id] == tracked_id
