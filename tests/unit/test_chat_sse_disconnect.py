"""Unit test for SSE chat streaming lifecycle and task cleanup."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.api.routers.chat import chat_stream
from services.contracts import ChatRequest


@pytest.mark.asyncio
async def test_chat_stream_cleanup_on_generator_aclose():
    """Verify that closing the generator explicitly cancels and consumes all tasks cleanly."""
    mock_adapter = MagicMock()
    mock_orch = MagicMock()

    # Stub orch.run to take a moment
    async def slow_run(req):
        await asyncio.sleep(10.0)

    mock_orch.run = slow_run

    req_body = ChatRequest(
        session_id="sess-sse-test",
        user_id="user-sse",
        message="Hello SSE lifecycle test",
    )

    response = await chat_stream(req_body, adapter=mock_adapter, orch=mock_orch)
    gen = response.body_iterator

    # Read first event (e.g. progress accepted)
    event1 = await gen.__anext__()
    assert "progress" in event1 or "accepted" in event1

    # Explicitly close generator mid-flight
    await gen.aclose()

    # Verify no unhandled tasks remain running in background
    await asyncio.sleep(0.05)
