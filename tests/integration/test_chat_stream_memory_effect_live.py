"""Optional live API test for streamed progress and session memory effect."""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest


RUN_LIVE_CHAT_STREAM_MEMORY_TESTS = os.getenv("RUN_LIVE_CHAT_STREAM_MEMORY_TESTS") == "1"
LIARA_API_BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_CHAT_STREAM_MEMORY_TESTS,
    reason="live stream memory test requires RUN_LIVE_CHAT_STREAM_MEMORY_TESTS=1",
)


def _read_sse_events(response: httpx.Response) -> list[tuple[str, str]]:
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
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "max_tokens": max_tokens,
    }
    with client.stream("POST", "/chat/stream", json=payload) as response:
        response.raise_for_status()
        return _read_sse_events(response)


def _parse_stream(events: list[tuple[str, str]]) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    progress = [json.loads(data) for event, data in events if event == "progress"]
    chunks = [json.loads(data) for event, data in events if event == "chunk"]
    final = [json.loads(data) for event, data in events if event == "final"]
    done = [data for event, data in events if event == "done"]
    return progress, chunks, final, done


def test_live_chat_stream_reports_progress_and_memory_effect():
    session_id = f"live-stream-memory-{uuid.uuid4().hex[:8]}"
    user_id = "live-test-user"

    with httpx.Client(base_url=LIARA_API_BASE_URL.rstrip("/"), timeout=180.0) as client:
        first_payload = {
            "session_id": session_id,
            "user_id": user_id,
            "message": "Mein Name ist Mira.",
            "max_tokens": 512,
        }
        with client.stream("POST", "/chat/stream", json=first_payload) as response:
            response.raise_for_status()
            first_events = _read_sse_events(response)

        second_payload = {
            "session_id": session_id,
            "user_id": user_id,
            "message": "Wie heisse ich?",
            "max_tokens": 512,
        }
        with client.stream("POST", "/chat/stream", json=second_payload) as response:
            response.raise_for_status()
            second_events = _read_sse_events(response)

    first_progress = [json.loads(data) for event, data in first_events if event == "progress"]
    second_progress = [json.loads(data) for event, data in second_events if event == "progress"]
    second_chunks = [json.loads(data) for event, data in second_events if event == "chunk"]
    second_final = [json.loads(data) for event, data in second_events if event == "final"]

    assert first_progress
    assert any(item.get("stage") == "accepted" for item in first_progress)
    assert not any(item.get("stage") == "memory_effect_detected" for item in first_progress)

    assert second_progress
    assert any(item.get("stage") == "accepted" for item in second_progress)
    assert any(item.get("stage") == "memory_effect_detected" for item in second_progress)
    assert any((item.get("metadata") or {}).get("context_mode") == "MEMORY" for item in second_progress)

    assert second_chunks
    chunk_text = "".join(item.get("text", "") for item in second_chunks)
    assert "Mira" in chunk_text

    assert second_final
    final_payload = second_final[-1]
    assert (final_payload.get("metadata") or {}).get("context_debug", {}).get("mode") == "MEMORY"


def test_live_chat_stream_complex_multi_turn_flow():
    session_id = f"live-complex-flow-{uuid.uuid4().hex[:8]}"
    user_id = "live-test-user"

    with httpx.Client(base_url=LIARA_API_BASE_URL.rstrip("/"), timeout=180.0) as client:
        turn1_events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message=(
                "Bitte merke dir: Ich heisse Mira, arbeite in Ulm und mein Budget fuer Solarpanels "
                "betragt 120000 Euro."
            ),
        )
        turn2_events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message=(
                "Plane 3 Optionen fuer Solarpanels mit 40, 70 und 100 Prozent Budgetnutzung. "
                "Ein Panel kostet 190 Euro und hat 420 Watt peak. Gib mir eine kompakte Tabelle."
            ),
        )
        turn3_events = _stream_turn(
            client,
            session_id=session_id,
            user_id=user_id,
            message=(
                "Fasse die Planung in 3 Bulletpoints zusammen und bestaetige am Ende nochmal "
                "meinen Namen und die Stadt."
            ),
        )

    turn1_progress, turn1_chunks, turn1_final, turn1_done = _parse_stream(turn1_events)
    turn2_progress, turn2_chunks, turn2_final, turn2_done = _parse_stream(turn2_events)
    turn3_progress, turn3_chunks, turn3_final, turn3_done = _parse_stream(turn3_events)

    assert turn1_progress
    assert any(item.get("stage") == "accepted" for item in turn1_progress)
    assert any(item.get("stage") == "orchestration_complete" for item in turn1_progress)
    assert turn1_chunks
    assert turn1_final
    assert turn1_done

    assert turn2_progress
    assert any(item.get("stage") == "accepted" for item in turn2_progress)
    assert any(item.get("stage") == "orchestration_complete" for item in turn2_progress)
    assert any(item.get("stage") == "memory_effect_detected" for item in turn2_progress)
    assert any((item.get("metadata") or {}).get("context_mode") == "MEMORY" for item in turn2_progress)
    assert turn2_chunks
    turn2_text = "".join(item.get("text", "") for item in turn2_chunks)
    assert any(marker in turn2_text for marker in ["40", "70", "100"])
    # LLM phrasing can vary; accept either explicit numeric carry-over
    # or equivalent domain terms from the pricing/power constraints.
    assert (
        any(marker in turn2_text for marker in ["190", "420"])
        or any(marker in turn2_text.lower() for marker in ["euro", "watt", "budget", "panel"])
    )
    assert turn2_final
    turn2_final_mode = (turn2_final[-1].get("metadata") or {}).get("context_debug", {}).get("mode")
    assert turn2_final_mode == "MEMORY"
    assert turn2_done

    assert turn3_progress
    assert any(item.get("stage") == "accepted" for item in turn3_progress)
    assert any(item.get("stage") == "orchestration_complete" for item in turn3_progress)
    assert any(item.get("stage") == "memory_effect_detected" for item in turn3_progress)
    assert turn3_chunks
    assert turn3_final
    turn3_final_mode = (turn3_final[-1].get("metadata") or {}).get("context_debug", {}).get("mode")
    assert turn3_final_mode == "MEMORY"
    assert turn3_done
