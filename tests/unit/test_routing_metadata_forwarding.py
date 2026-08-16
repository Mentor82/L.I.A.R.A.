"""Regression tests for Issue #21: RouterDecision.metadata forwarding.

tool_discovery.execute_tools() must forward orchestrator._last_route_debug's
metadata (intent, retrieval_intent, ...) into ExecutorRequest.routing_metadata,
merged with the wsl_session_id tracking added in Issue #16 -- neither must
clobber the other, and the merge must degrade gracefully when
_last_route_debug is absent entirely.
"""

import pytest

from services.contracts import ToolExecutionRequest, ToolExecutionResult
from services.orchestrator import tool_discovery
from services.orchestrator.executor import ToolExecutor


class _CapturingToolCoordinator:
    """Records the exact ToolExecutionRequest.parameters built for each call."""

    def __init__(self):
        self.captured_requests: list[ToolExecutionRequest] = []

    async def execute_tools_parallel(self, requests: list[ToolExecutionRequest]):
        self.captured_requests.extend(requests)
        results = {}
        for req in requests:
            if req.tool_name == "sys":
                output = {"stdout": "", "stderr": "", "exit_code": 0}
            elif req.tool_name == "wsl_session":
                if req.parameters.get("action") == "create":
                    output = {"session_id": "sess-fakecreated0000000000000001", "state": "ready"}
                else:
                    output = {"session_id": req.parameters.get("session_id", ""), "state": "ready"}
            else:
                output = {}
            results[req.tool_name] = ToolExecutionResult(tool_name=req.tool_name, status="success", output=output)
        return results


class _FakeOrchestrator:
    def __init__(self, *, last_route_debug=None, with_wsl_tracking=True):
        self.coordinator = _CapturingToolCoordinator()
        self.executor = ToolExecutor(tool_coordinator=self.coordinator)
        self._active_session_id = "chat-session-1"
        self._active_user_id = "user-1"
        if last_route_debug is not None:
            self._last_route_debug = last_route_debug
        if with_wsl_tracking:
            self._wsl_session_by_chat_session: dict[str, str] = {}


def _retrieval_intent_discovery(query: str = "primary source item 42") -> dict:
    return {
        "requires_external_information": True,
        "goal": "find primary source",
        "source_hint": "Primary Source",
        "candidate_url": None,
        "search_query": query,
        "discovery_required": True,
    }


def _retrieval_intent_url_fetch(url: str) -> dict:
    return {
        "requires_external_information": True,
        "goal": "verify claim",
        "source_hint": "",
        "candidate_url": url,
        "search_query": "",
        "discovery_required": False,
    }


@pytest.mark.asyncio
async def test_execute_tools_forwards_retrieval_intent_for_discovery_search():
    orchestrator = _FakeOrchestrator(
        last_route_debug={"metadata": {"intent": "web_discovery", "retrieval_intent": _retrieval_intent_discovery()}},
    )

    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["sys"], query="who documents VINOX", session_id="chat-session-1",
    )

    built = orchestrator.coordinator.captured_requests[0].parameters
    assert built["context"] == "agent_web_discovery"
    assert built["command"] == "curl"
    assert "bing.com/search?format=rss" in built["args"][-1]
    assert "%22Primary+Source%22" in built["args"][-1]


@pytest.mark.asyncio
async def test_execute_tools_forwards_retrieval_intent_for_direct_url_fetch():
    orchestrator = _FakeOrchestrator(
        last_route_debug={
            "metadata": {
                "intent": "url_fetch",
                "retrieval_intent": _retrieval_intent_url_fetch("https://primary.example/item/42"),
            },
        },
    )

    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["sys"], query="fetch it", session_id="chat-session-1",
    )

    built = orchestrator.coordinator.captured_requests[0].parameters
    assert built["context"] == "agent_url_fetch"
    assert built["command"] == "curl"
    assert built["url"] == "https://primary.example/item/42"
    assert built["args"][-1] == "https://primary.example/item/42"


@pytest.mark.asyncio
async def test_route_metadata_and_wsl_session_tracking_coexist_without_clobbering():
    orchestrator = _FakeOrchestrator(
        last_route_debug={"metadata": {"intent": "wsl_lifecycle"}},
    )
    session_id = "chat-session-1"
    orchestrator._wsl_session_by_chat_session[session_id] = "sess-alreadytracked0000000001"

    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["wsl_session"], query="check status of my session", session_id=session_id,
    )

    built = orchestrator.coordinator.captured_requests[0].parameters
    assert built == {"action": "status", "session_id": "sess-alreadytracked0000000001"}

    # sys retrieval_intent must still work even with wsl_session_id also present
    # in the merged routing_metadata (neither key clobbers the other).
    orchestrator.coordinator.captured_requests.clear()
    orchestrator._last_route_debug = {
        "metadata": {"intent": "url_fetch", "retrieval_intent": _retrieval_intent_url_fetch("https://example.test/x")},
    }
    await tool_discovery.execute_tools(
        orchestrator, selected_tools=["sys"], query="fetch it", session_id=session_id,
    )
    built_sys = orchestrator.coordinator.captured_requests[0].parameters
    assert built_sys["context"] == "agent_url_fetch"
    assert built_sys["url"] == "https://example.test/x"


@pytest.mark.asyncio
async def test_execute_tools_degrades_gracefully_without_last_route_debug():
    """Matches the _FakeOrchestrator stub used in Issue #16's tests, which
    never sets _last_route_debug -- must not crash, must fall through to the
    normal select_sys_command heuristic (no retrieval_intent branch)."""
    orchestrator = _FakeOrchestrator(last_route_debug=None)
    assert not hasattr(orchestrator, "_last_route_debug")

    result = await tool_discovery.execute_tools(
        orchestrator, selected_tools=["sys"], query="what time is it", session_id="chat-session-1",
    )

    assert "sys" in result
    built = orchestrator.coordinator.captured_requests[0].parameters
    assert built["context"] != "agent_web_discovery"
    assert built["context"] != "agent_url_fetch"
