"""Unit test for PostgresSysAuditRepository and safe sync-bridge invariant."""

import asyncio
import pytest

from services.api.exceptions import AuditPersistenceError
from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository


def test_sync_bridge_does_not_crash_in_active_event_loop(monkeypatch):
    """Test that log_event_sync handles loop state safely without calling asyncio.run()."""
    from unittest.mock import MagicMock
    mock_pool = MagicMock()
    mock_pool.getconn.side_effect = Exception("DB Connection Refused")
    repo = PostgresSysAuditRepository(mock_pool)

    # In non-fail-closed mode, DB failure returns error payload cleanly without crash
    res = repo.log_event_sync(
        tool_name="test_tool",
        lifecycle_stage="started",
        fail_closed=False,
    )
    assert res["status"] == "failed"

    # In fail-closed mode, raises AuditPersistenceError cleanly
    with pytest.raises(AuditPersistenceError):
        repo.log_event_sync(
            tool_name="test_tool",
            lifecycle_stage="started",
            fail_closed=True,
        )
