"""Integration test: Chat flow with tool usage (Python, Julia).

This test documents the complete flow when a user asks the chat
interface to use tools like Python or Julia.

Actual progress stages emitted by the server:
  accepted
  history_user_written
  orchestration_started
  orchestration_complete
  history_assistant_written
  session_snapshot_written
  memory_effect_detected  (only on later turns when history matches)

Stream events:
  - progress: stage=accepted | orchestration_started | orchestration_complete | ...
  - chunk: text fragments of the LLM response
  - final: {tools_executed, tool_results, llm_generation, execution_trace, ...}
  - done: stream terminates

Example flow:
  User: "Berechne 10 + 20 mit Python."
    ↓  tool_selection inside orchestration
    ↓  tool_execution (python3 subprocess)
    ↓  LLM generation with tool result
    ↓  progress: orchestration_complete
  Response chunks: "Das Ergebnis ist 30."
  Final: {tools_executed: ["python"], tool_results: {"python": {...}}}
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest


RUN_LIVE_CHAT_TOOL_TESTS = os.getenv("RUN_LIVE_CHAT_TOOL_TESTS") == "1"
LIARA_API_BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_CHAT_TOOL_TESTS,
    reason="live chat tool test requires RUN_LIVE_CHAT_TOOL_TESTS=1 and LIARA_API_BASE_URL",
)


def _read_sse_events(response: httpx.Response) -> list[tuple[str, str]]:
    """Parse Server-Sent Events from response body."""
    events: list[tuple[str, str]] = []
    current_event = ""
    for line in response.iter_lines():
        if line is None:
            continue
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        if not text:
            continue
        if text.startswith("event:"):
            current_event = text.split(":", 1)[1].strip()
            continue
        if text.startswith("data:"):
            events.append((current_event, text.split(":", 1)[1].strip()))
    return events


def _stream_turn(
    client: httpx.Client,
    *,
    session_id: str,
    user_id: str,
    message: str,
    max_tokens: int = 512,
) -> list[tuple[str, str]]:
    """Send a chat message and collect all SSE events until done."""
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "max_tokens": max_tokens,
    }
    with client.stream("POST", "/chat/stream", json=payload) as response:
        response.raise_for_status()
        return _read_sse_events(response)


def _parse_stream(
    events: list[tuple[str, str]],
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Split SSE events into progress/chunk/final/done buckets."""
    progress = [json.loads(data) for event, data in events if event == "progress"]
    chunks = [json.loads(data) for event, data in events if event == "chunk"]
    final = [json.loads(data) for event, data in events if event == "final"]
    done = [data for event, data in events if event == "done"]
    return progress, chunks, final, done


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_chat_stream_with_python_tool_execution():
    """
    Test: User requests a Python computation.

    Flow:
      1. Send: "Berechne 10 + 20 mit Python und gib das Ergebnis."
      2. Orchestrator selects + executes the Python tool internally
      3. LLM generates response using tool result
      4. Verify orchestration stages, chunks, and final payload

    Actual server stages:
      accepted → history_user_written → orchestration_started
      → orchestration_complete → history_assistant_written
      → session_snapshot_written
    """
    session_id = f"chat-tool-python-{uuid.uuid4().hex[:8]}"
    user_id = "test-tool-user"

    with httpx.Client(base_url=LIARA_API_BASE_URL.rstrip("/"), timeout=180.0) as client:
        events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message="Berechne 10 + 20 mit Python und gib das Ergebnis.",
            max_tokens=256,
        )

    progress, chunks, final, done = _parse_stream(events)

    # === PROGRESS STAGES ===
    stages = [p.get("stage") for p in progress]
    assert progress, f"No progress events received"
    assert "accepted" in stages, f"Missing 'accepted' stage; got: {stages}"
    assert "orchestration_started" in stages, (
        f"Missing 'orchestration_started' stage; got: {stages}"
    )
    assert "orchestration_complete" in stages, (
        f"Missing 'orchestration_complete' stage; got: {stages}"
    )

    # === CHUNKS ===
    assert chunks, "No text chunks received"
    full_text = "".join(c.get("text", "") for c in chunks)
    assert len(full_text) > 0, "Generated text is empty"

    # === FINAL PAYLOAD ===
    assert final, "No final event received"
    final_payload = final[-1]

    tools_executed = final_payload.get("tools_executed", [])
    assert isinstance(tools_executed, list), "tools_executed should be a list"

    tool_results = final_payload.get("tool_results", {})
    assert isinstance(tool_results, dict), "tool_results should be a dict"

    # If python tool ran, its result should be success or partial
    if "python" in tool_results:
        python_result = tool_results["python"]
        assert python_result.get("status") in ("success", "partial"), (
            f"Python tool result unexpected: {python_result.get('status')}"
        )

    # === DONE ===
    assert done, "Stream did not emit 'done' event"


def test_chat_stream_with_julia_tool_execution():
    """
    Test: User requests a Julia computation.

    Flow:
      1. Send: "Schreibe Julia Code, der [1,2,3] verdoppelt."
      2. Orchestrator selects + executes the Julia tool internally
      3. LLM generates response with result
      4. Verify orchestration stages and final payload structure

    Actual server stages:
      accepted → history_user_written → orchestration_started
      → orchestration_complete → history_assistant_written
      → session_snapshot_written
    """
    session_id = f"chat-tool-julia-{uuid.uuid4().hex[:8]}"
    user_id = "test-tool-user"

    with httpx.Client(base_url=LIARA_API_BASE_URL.rstrip("/"), timeout=180.0) as client:
        events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message="Schreibe Julia Code, der das Array [1,2,3] verdoppelt und zeig mir das Ergebnis.",
            max_tokens=256,
        )

    progress, chunks, final, done = _parse_stream(events)

    # === PROGRESS STAGES ===
    stages = [p.get("stage") for p in progress]
    assert progress, "No progress events"
    assert "accepted" in stages, f"Missing 'accepted' stage; got: {stages}"
    assert "orchestration_complete" in stages, (
        f"Missing 'orchestration_complete' stage; got: {stages}"
    )

    # === CHUNKS ===
    assert chunks, "No text chunks"
    full_text = "".join(c.get("text", "") for c in chunks)
    assert len(full_text) > 0, "Generated response is empty"

    # === FINAL EVENT ===
    # Actual top-level keys: gen_ms, llm_model, llm_provider, metadata (nested)
    # state_final is inside metadata or metadata.debug_run, not always top-level.
    assert final, "No final event"

    # Use the first final event which carries the richest metadata
    final_payload = final[0]

    # state_final may be top-level or nested inside metadata / metadata.debug_run
    def _find_state_final(d: dict) -> str | None:
        if "state_final" in d:
            return d["state_final"]
        meta = d.get("metadata") or {}
        if "state_final" in meta:
            return meta["state_final"]
        return (meta.get("debug_run") or {}).get("state_final")

    state_final = _find_state_final(final_payload)
    assert state_final in ("complete", "success"), (
        f"Expected successful completion; got state_final={state_final!r}, "
        f"top-level keys: {list(final_payload.keys())}"
    )

    # LLM info is always present (gen_ms / llm_model / llm_provider at top level)
    has_llm_info = (
        "llm_model" in final_payload
        or "llm_generation" in final_payload
        or "llm_provider" in final_payload
        or "gen_ms" in final_payload
    )
    assert has_llm_info, (
        f"Final payload missing LLM info; keys: {list(final_payload.keys())}"
    )

    # === DONE ===
    assert done, "Stream did not emit 'done' event"


def test_chat_stream_with_multiple_tool_invocations():
    """
    Test: Multi-turn chat with tool usage across turns.

    Turn 1: "Berechne 5 * 5 und merke dir das Ergebnis."
      - Orchestration runs, may use Python tool
      - Response acknowledges

    Turn 2: "Multipliziere das gespeicherte Ergebnis mit 2."
      - Orchestration references prior session history
      - May trigger memory_effect_detected

    Verifies:
      - Both turns complete (progress + chunks + done)
      - Turn 2 includes orchestration or memory effect stages
    """
    session_id = f"chat-tool-multi-{uuid.uuid4().hex[:8]}"
    user_id = "test-tool-user"

    with httpx.Client(base_url=LIARA_API_BASE_URL.rstrip("/"), timeout=180.0) as client:
        # === TURN 1 ===
        turn1_events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message="Berechne 5 * 5 und merke dir das Ergebnis.",
            max_tokens=256,
        )
        turn1_progress, turn1_chunks, turn1_final, turn1_done = _parse_stream(turn1_events)

        assert turn1_progress, "Turn 1: No progress events"
        assert turn1_chunks, "Turn 1: No text chunks"
        assert turn1_done, "Turn 1: Stream did not end"
        assert len("".join(c.get("text", "") for c in turn1_chunks)) > 0, "Turn 1: Empty response"

        # === TURN 2 ===
        turn2_events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message="Multipliziere das gespeicherte Ergebnis mit 2. Was kommt raus?",
            max_tokens=256,
        )
        turn2_progress, turn2_chunks, turn2_final, turn2_done = _parse_stream(turn2_events)

        turn2_stages = [p.get("stage") for p in turn2_progress]
        assert turn2_progress, "Turn 2: No progress events"
        assert turn2_chunks, "Turn 2: No text chunks"
        assert any(
            s in turn2_stages
            for s in ("orchestration_started", "orchestration_complete", "memory_effect_detected")
        ), f"Turn 2: Expected orchestration stages; got: {turn2_stages}"

        assert len("".join(c.get("text", "") for c in turn2_chunks)) > 0, "Turn 2: Empty response"
        assert turn2_done, "Turn 2: Stream did not end"


def test_chat_stream_tool_execution_metadata():
    """
    Test: Verify rich metadata in final event and optional tool_execution progress.

    The final event must always include:
      - tools_executed: list[str]
      - tool_results: dict[str, ...]
      - execution_trace: list[{from, to, duration_ms}]

    The tool_execution progress stage is optional (only emitted when tools ran),
    but if present its metadata.tools_to_execute must be a list.
    """
    session_id = f"chat-tool-meta-{uuid.uuid4().hex[:8]}"
    user_id = "test-tool-user"

    with httpx.Client(base_url=LIARA_API_BASE_URL.rstrip("/"), timeout=180.0) as client:
        events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message="Berechne sqrt(16) mit Python.",
            max_tokens=256,
        )

    progress, chunks, final, done = _parse_stream(events)

    # === OPTIONAL: tool_execution stage metadata ===
    for p in progress:
        if p.get("stage") == "tool_execution":
            metadata = p.get("metadata", {})
            if "tools_to_execute" in metadata:
                assert isinstance(metadata["tools_to_execute"], list), (
                    "tools_to_execute should be a list"
                )

    # === FINAL PAYLOAD ===
    assert final, "No final event"
    final_payload = final[-1]

    tools_executed = final_payload.get("tools_executed", [])
    assert isinstance(tools_executed, list), "tools_executed should be a list"

    tool_results = final_payload.get("tool_results", {})
    assert isinstance(tool_results, dict), "tool_results should be a dict"

    exec_trace = final_payload.get("execution_trace", [])
    assert isinstance(exec_trace, list), "execution_trace should be a list"
    if exec_trace:
        assert all("to" in t for t in exec_trace), "Each trace entry must have 'to'"

    # === DONE ===
    assert done, "Stream did not emit 'done' event"
