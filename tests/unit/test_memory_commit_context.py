from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.contracts import ContextSearchRequest, ContextScope, ContextSearchResponse, MemoryServiceStatus
from services.memory_adapter import InProcessMemoryAdapter
from services.orchestrator.librarian_pipeline import upsert_memory_commit_embedding


async def test_memory_commit_is_retrievable_and_scoped():
    memory = InProcessMemoryAdapter(None)
    orchestrator = SimpleNamespace(memory=memory)
    args = dict(session_id="commit-regression", user_id="test-user", run_id="run-1",
                content="Memory commit regression marker", metadata={"validation_status": "accepted"})

    # The real in-process fallback stores the document, but correctly reports
    # partial rather than claiming durable Chroma persistence.
    assert not await upsert_memory_commit_embedding(orchestrator, **args)
    assert not await upsert_memory_commit_embedding(orchestrator, **args)
    result = await memory.context_search(ContextSearchRequest(
        query=args["content"], scope=ContextScope(session_id=args["session_id"], run_id=args["run_id"]),
    ))
    items = [item for item in result.items if item.document_id == "memory_commit:commit-regression:run-1"]
    assert len(items) == 1
    assert items[0].content == args["content"]
    assert items[0].metadata["user_id"] == "test-user"
    assert items[0].metadata["source"] == "memory_commit"


@pytest.mark.parametrize("status", ["success", "partial", "failed"])
async def test_memory_commit_does_not_report_degraded_write_as_success(status):
    memory = SimpleNamespace(context_upsert=AsyncMock(return_value=ContextSearchResponse(
        status=MemoryServiceStatus(status=status, backend="memory-service", degraded=status != "success"),
    )))
    result = await upsert_memory_commit_embedding(
        SimpleNamespace(memory=memory), session_id="s", user_id="u", run_id="r", content="Test",
    )
    assert result is (status == "success")


async def test_empty_memory_commit_does_not_write():
    memory = SimpleNamespace(context_upsert=AsyncMock())
    assert not await upsert_memory_commit_embedding(
        SimpleNamespace(memory=memory), session_id="s", user_id="u", run_id="r", content="  ",
    )
    memory.context_upsert.assert_not_awaited()
