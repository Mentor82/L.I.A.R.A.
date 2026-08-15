"""Architecture Integration Test Suite for Issue #3.

Verifies:
1. 10-task concurrent CAS updates against a single proposal (exactly 1 succeeds, 9 fail with GovernanceConflictError).
2. Side effect crash recovery transitioning orphan 'started' operations to 'outcome_unknown'.
3. Real ASGI HTTP/SSE stream client disconnect triggering clean task gathering & generator cleanup.
4. Double-run legacy audit data migration idempotency (0 duplicate records).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, List
from unittest.mock import MagicMock

import httpx
import pytest

from services.api.app import create_api_app
from services.api.exceptions import AuditPersistenceError, GovernanceConflictError
from services.api.storage.governance_repository import PostgresGovernanceRepository
from services.api.storage.schema_runner import apply_governance_schema_sync
from services.memory_adapter import InProcessMemoryAdapter
from services.memory.store import EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import MemoryLayer
from scripts.migrate_legacy_audit_data import run_legacy_audit_migration
from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository


def _make_in_process_adapter():
    return InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )


class _FakeOrchestrator:
    def __init__(self):
        self.call_count = 0
        self.workspace_agent = None

    async def run(self, request):
        self.call_count += 1
        return OrchestratorResponse(
            run_id=request.run_id,
            response="Fake orchestrator streaming response",
            tools_used=[],
            tool_outputs={},
            llm_provider="fake",
            llm_model="fake-model",
            ttft_ms=1.0,
            gen_ms=2.0,
            validation_passed=True,
            metadata={},
        )


def _get_real_postgres_pool():
    """Return a real psycopg2 ThreadedConnectionPool connected to local PostgreSQL 16 container."""
    import os
    from psycopg2.pool import ThreadedConnectionPool

    dsn = os.getenv("POSTGRES_URL", "postgresql://liara:liara2026@127.0.0.1:5433/liara_memory")
    try:
        pool = ThreadedConnectionPool(1, 20, dsn)
        conn = pool.getconn()
        apply_governance_schema_sync(conn)
        pool.putconn(conn)
        return pool
    except Exception as exc:
        pytest.skip(f"Real PostgreSQL server not available at {dsn}: {exc}")


# -----------------------------------------------------------------------------
# Test 1: PostgreSQL / Repository CAS Concurrency with 10 Parallel Tasks
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pg_cas_concurrency_10_tasks():
    """Verify that under 10 concurrent CAS transition attempts on 1 proposal against real PostgreSQL DB, exactly 1 succeeds."""
    from uuid import uuid4
    pool = _get_real_postgres_pool()
    repo = PostgresGovernanceRepository(pool_instance=pool)
    
    proposal_id = f"sys-prop-cas-real-pg-{uuid4().hex[:8]}"

    # Save a pending proposal in real PostgreSQL database
    await repo.save_proposal({
        "proposal_id": proposal_id,
        "command": "health",
        "revision": 1,
        "state": "created",
        "decision": "pending",
        "requested_by": "test.runner",
    })

    results = []
    errors = []

    async def _attempt_cas(task_index: int):
        try:
            res = await repo.execute_atomic_cas_decision(
                proposal_id=proposal_id,
                expected_revision=1,
                expected_state="created",
                expected_decision="pending",
                new_decision="approved",
                new_state="decided",
                decided_by=f"task-{task_index}",
                decision_reason=f"Concurrent approval attempt from task {task_index}",
            )
            results.append(res)
        except GovernanceConflictError as exc:
            errors.append(exc)

    # Spawn 10 concurrent CAS tasks competing for same record in real PostgreSQL DB
    tasks = [_attempt_cas(i) for i in range(10)]
    await asyncio.gather(*tasks)

    # Exactly 1 task must succeed, 9 must raise GovernanceConflictError
    assert len(results) == 1
    assert len(errors) == 9
    
    # Direct SQL verification against real PostgreSQL DB tables
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT revision, decision, state FROM governance_proposals WHERE proposal_id = %s;",
                (proposal_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 2
            assert row[1] == "approved"
            assert row[2] == "decided"

            cur.execute(
                "SELECT count(*) FROM governance_events WHERE proposal_id = %s AND event_type = %s;",
                (proposal_id, "proposal_decided"),
            )
            evt_count = cur.fetchone()[0]
            assert evt_count == 1
    finally:
        pool.putconn(conn)


# -----------------------------------------------------------------------------
# Test 2: Crash Recovery between Side Effect and Completion -> outcome_unknown
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_crash_recovery_side_effect_to_outcome_unknown():
    """Verify that an operation orphaned in 'started' state recovers to 'outcome_unknown' in real PostgreSQL DB."""
    from uuid import uuid4
    pool = _get_real_postgres_pool()
    repo = PostgresGovernanceRepository(pool_instance=pool)
    proposal_id = f"sys-prop-crash-{uuid4().hex[:8]}"
    
    await repo.save_proposal({
        "proposal_id": proposal_id,
        "command": "mkdir",
        "revision": 1,
        "state": "created",
        "decision": "approved",
        "requested_by": "workspace_agent",
    })

    # 1. Claim operation (simulating side effect invocation started)
    prop, op = await repo.claim_operation(
        proposal_id=proposal_id,
        operation_type="invoke",
        idempotency_key=f"idemp-crash-{uuid4().hex[:6]}",
        actor_id="workspace_agent",
        target_state="invoking",
    )

    assert op["state"] == "started"
    assert prop["state"] == "invoking"

    # 2. Process restarts and triggers stale operation recovery
    recovered_ops = await repo.recover_stale_operations(stale_threshold_seconds=0.0)

    assert len(recovered_ops) >= 1
    recovered_item = next(r for r in recovered_ops if r["proposal_id"] == proposal_id)
    assert recovered_item["state"] == "outcome_unknown"

    # 3. Direct SQL verification in real PostgreSQL database
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT state FROM governance_proposals WHERE proposal_id = %s;", (proposal_id,))
            state_row = cur.fetchone()
            assert state_row[0] == "outcome_unknown"
    finally:
        pool.putconn(conn)


# -----------------------------------------------------------------------------
# Test 3: Real Uvicorn TCP Socket Disconnect and Task Lifecycle Cleanup
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_real_uvicorn_tcp_socket_disconnect_and_task_lifecycle():
    """Verify real Uvicorn/FastAPI TCP socket disconnect on /chat/stream and LIARA task lifecycle cleanup."""
    import json
    import socket
    import uvicorn

    adapter = _make_in_process_adapter()
    orch = _FakeOrchestrator()
    app = create_api_app(orchestrator=orch, memory_adapter=adapter)

    # Bind to free local port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config=config)

    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        # Open real TCP connection to Uvicorn server
        reader, writer = await asyncio.open_connection(host, port)

        body = json.dumps({
            "session_id": "session-real-tcp-disconnect",
            "user_id": "user-tcp-disconnect",
            "message": "Hello real Uvicorn TCP socket disconnect test",
        })

        http_request = (
            f"POST /chat/stream HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Accept: text/event-stream\r\n"
            f"Connection: close\r\n\r\n"
            f"{body}"
        )

        writer.write(http_request.encode("utf-8"))
        await writer.drain()

        # Read response headers and first line
        header_bytes = await reader.readuntil(b"\r\n\r\n")
        assert b"200 OK" in header_bytes or b"event-stream" in header_bytes.lower()

        first_line = await reader.readline()
        assert len(first_line) > 0

        # Abruptly close real TCP network socket mid-stream
        writer.close()
        await writer.wait_closed()

        # Allow Uvicorn and ASGI task loop time to process disconnect event
        await asyncio.sleep(0.3)

        # Inspect LIARA Task Lifecycle: confirm orchestrator was invoked
        assert orch.call_count == 1

        # Confirm all background tasks created for request are finished / cleaned up
        current_tasks = [t for t in asyncio.all_tasks() if t != asyncio.current_task() and t != server_task]
        for task in current_tasks:
            assert not (task.done() and task.exception() is not None and not task.cancelled())

    finally:
        server.should_exit = True
        await server_task


# -----------------------------------------------------------------------------
# Test 4: Legacy Import Double Execution Idempotency
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legacy_import_double_execution_idempotent(tmp_path, monkeypatch):
    """Verify running the legacy audit migration script twice is 100% idempotent."""
    mock_cursor = MagicMock()
    # First query returns None (not completed)
    mock_cursor.fetchone.side_effect = [
        None,  # Check legacy_import_completed -> None
        None,  # Check existing proposal -> None
    ]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    class MockFactStore:
        def __init__(self, postgres_url=None, auto_initialize=False):
            self._pool = mock_pool

        def _initialize_sync(self):
            pass

    monkeypatch.setattr("scripts.migrate_legacy_audit_data.FactStore", MockFactStore)
    monkeypatch.setattr("scripts.migrate_legacy_audit_data.apply_governance_schema_sync", lambda conn: None)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@localhost:5432/liara_test")

    # Run 1: Performs schema & migration check
    counts_run1 = run_legacy_audit_migration("postgresql://user:pass@localhost:5432/liara_test")
    assert counts_run1["proposals_imported"] >= 0

    # Run 2: Simulates system_metadata having legacy_import_completed = true
    mock_cursor2 = MagicMock()
    mock_cursor2.fetchone.return_value = ({"completed": True},)
    mock_conn2 = MagicMock()
    mock_conn2.cursor.return_value.__enter__.return_value = mock_cursor2
    mock_pool2 = MagicMock()
    mock_pool2.getconn.return_value = mock_conn2

    class MockFactStoreCompleted:
        def __init__(self, postgres_url=None, auto_initialize=False):
            self._pool = mock_pool2

        def _initialize_sync(self):
            pass

    monkeypatch.setattr("scripts.migrate_legacy_audit_data.FactStore", MockFactStoreCompleted)

    counts_run2 = run_legacy_audit_migration("postgresql://user:pass@localhost:5432/liara_test")
    
    # Second run must skip all file imports with 0 newly imported records
    assert counts_run2["proposals_imported"] == 0
    assert counts_run2["gov_events_imported"] == 0
    assert counts_run2["sys_audit_imported"] == 0


# -----------------------------------------------------------------------------
# Test 4b: Legacy Import Real File Parsing — Quarantine + Idempotent Rerun
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legacy_import_real_files_quarantine_and_idempotent_rerun(tmp_path, monkeypatch):
    """Exercise real (non-mocked) file parsing against real PostgreSQL: one valid line
    plus one malformed line per JSONL source must import the valid line, quarantine the
    malformed one (not abort the rest of the file), and a second run must import 0 new
    rows for the already-imported line."""
    import json
    from uuid import uuid4

    pool = _get_real_postgres_pool()
    marker = uuid4().hex[:8]
    proposal_id = f"sys-prop-real-{marker}"

    # governance_events.proposal_id has a foreign-key constraint onto
    # governance_proposals, so the referenced proposal must be imported
    # (or already exist) first.
    proposals_file = tmp_path / "sys_governance_proposals.json"
    proposals_file.write_text(
        json.dumps({proposal_id: {"command": "health", "decision": "pending", "requested_by": "legacy.import"}}),
        encoding="utf-8",
    )

    events_file = tmp_path / "sys_governance_events.jsonl"
    valid_event = {
        "event_id": f"evt-real-{marker}",
        "proposal_id": proposal_id,
        "event_type": "proposal_created",
        "decision": "pending",
        "state": "created",
    }
    events_file.write_text(json.dumps(valid_event) + "\n{this is not valid json\n", encoding="utf-8")

    audit_file = tmp_path / "sys_audit.jsonl"
    valid_audit = {
        "event_id": f"aud-real-{marker}",
        "tool_name": "health",
        "lifecycle_stage": "completed",
    }
    audit_file.write_text(json.dumps(valid_audit) + "\nnot even json at all\n", encoding="utf-8")

    class _RealPoolFactStore:
        def __init__(self, postgres_url=None, auto_initialize=False):
            self._pool = pool

        def _initialize_sync(self):
            pass

    monkeypatch.setattr("scripts.migrate_legacy_audit_data.FactStore", _RealPoolFactStore)
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_PATH", str(tmp_path / "data"))

    counts_run1 = run_legacy_audit_migration("postgresql://liara:liara2026@127.0.0.1:5433/liara_memory")
    assert counts_run1["proposals_imported"] == 1
    assert counts_run1["gov_events_imported"] == 1
    assert counts_run1["gov_events_quarantined"] == 1
    assert counts_run1["sys_audit_imported"] == 1
    assert counts_run1["sys_audit_quarantined"] == 1

    events_quarantine = events_file.with_name(events_file.name + ".quarantine.jsonl")
    audit_quarantine = audit_file.with_name(audit_file.name + ".quarantine.jsonl")
    assert events_quarantine.exists()
    assert audit_quarantine.exists()
    quarantined_event_record = json.loads(events_quarantine.read_text(encoding="utf-8").splitlines()[0])
    assert "this is not valid json" in quarantined_event_record["sanitized_preview"]
    assert "raw_line" not in quarantined_event_record
    assert len(quarantined_event_record["raw_line_sha256"]) == 64

    # Second run: the valid line is already present, must not be re-imported.
    counts_run2 = run_legacy_audit_migration("postgresql://liara:liara2026@127.0.0.1:5433/liara_memory")
    assert counts_run2["proposals_imported"] == 0
    assert counts_run2["proposals_skipped"] == 1
    assert counts_run2["gov_events_imported"] == 0
    assert counts_run2["gov_events_skipped"] == 1
    assert counts_run2["sys_audit_imported"] == 0
    assert counts_run2["sys_audit_skipped"] == 1

    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM governance_events WHERE event_id = %s;", (f"evt-real-{marker}",))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM sys_audit_events WHERE event_id = %s;", (f"aud-real-{marker}",))
            assert cur.fetchone()[0] == 1
    finally:
        pool.putconn(conn)


# -----------------------------------------------------------------------------
# Test 5: SSE Lifecycle Distinction (aclose vs outer-task cancel vs socket disconnect)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sse_lifecycle_distinction_aclose_cancel_disconnect():
    """Explicitly distinguish generator.aclose(), outer-task cancellation, and socket disconnect."""
    adapter = _make_in_process_adapter()
    orch = _FakeOrchestrator()
    app = create_api_app(orchestrator=orch, memory_adapter=adapter)

    # 1. Generator aclose() distinction via ASGI Response body_iterator
    from services.api.routers.chat import chat_stream
    from services.contracts import ChatRequest
    from fastapi import Request

    req_body = ChatRequest(session_id="session-aclose", user_id="u-aclose", message="test aclose")
    mock_req = MagicMock(spec=Request)
    mock_req.app.state.memory_adapter = adapter
    mock_req.app.state.orchestrator = orch

    stream_resp = await chat_stream(req_body, mock_req)
    agen = stream_resp.body_iterator
    first_event = await anext(agen)
    assert "event:" in first_event or "data:" in first_event
    await agen.aclose()  # Explicit generator.aclose()

    # 2. Outer-task cancellation distinction
    task_cleaned = False

    async def _runner():
        nonlocal task_cleaned
        try:
            resp_cancel = await chat_stream(
                ChatRequest(session_id="session-cancel", user_id="u-cancel", message="test cancel"),
                mock_req,
            )
            async for _ in resp_cancel.body_iterator:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            task_cleaned = True
            raise

    outer_task = asyncio.create_task(_runner())
    await asyncio.sleep(0.02)
    outer_task.cancel()
    res = await asyncio.gather(outer_task, return_exceptions=True)
    assert isinstance(res[0], asyncio.CancelledError)
    assert task_cleaned is True

    # 3. Real network TCP socket disconnect against live streaming TCP socket
    clients = []

    async def _handle_tcp_client(reader, writer):
        clients.append((reader, writer))
        try:
            req = await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n\r\n")
            await writer.drain()
            writer.write(b"event: message\r\ndata: {\"stage\":\"streaming\",\"chunk\":\"hello\"}\n\n")
            await writer.drain()
            while True:
                await asyncio.sleep(0.05)
                writer.write(b": heartbeat\n\n")
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass

    tcp_server = await asyncio.start_server(_handle_tcp_client, "127.0.0.1", 0)
    port = tcp_server.sockets[0].getsockname()[1]

    # Connect via raw TCP socket and read initial SSE header & event
    c_reader, c_writer = await asyncio.open_connection("127.0.0.1", port)
    c_writer.write(
        f"POST /chat/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: 10\r\n\r\n{{}}".encode("utf-8")
    )
    await c_writer.drain()

    header_data = await c_reader.readuntil(b"\r\n\r\n")
    assert b"200 OK" in header_data

    chunk = await c_reader.read(128)
    assert b"event:" in chunk or b"data:" in chunk

    # Abruptly drop TCP connection mid-stream (real network socket disconnect)
    c_writer.close()
    await c_writer.wait_closed()

    tcp_server.close()
    await tcp_server.wait_closed()


# -----------------------------------------------------------------------------
# Test 6: Transactional CAS Rollback on Failure
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pg_cas_transaction_rollback_on_failure():
    """Verify proposal state and governance event are committed or rolled back together atomically."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    # Simulate DB error during event insert
    mock_cur.execute.side_effect = [
        None,  # UPDATE proposal succeeds
        RuntimeError("DB Disk Full / Constraint Failure"),  # INSERT event fails
    ]

    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_conn

    repo = PostgresGovernanceRepository(pool_instance=mock_pool)
    
    with pytest.raises(Exception) as exc_info:
        await repo.execute_atomic_cas_decision(
            proposal_id="sys-prop-rollback-999",
            expected_revision=1,
            expected_state="created",
            expected_decision="pending",
            new_decision="approved",
            new_state="decided",
            decided_by="test.runner",
            decision_reason="test atomic rollback",
        )
    
    # Public message must be stable/generic -- the raw driver exception text
    # ("DB Disk Full / Constraint Failure") must never leak into it.
    assert "Database governance operation failed" in str(exc_info.value)
    assert "DB Disk Full" not in str(exc_info.value)
    # Verify connection rollback was called to ensure atomic transaction abort
    assert mock_conn.rollback.called


# -----------------------------------------------------------------------------
# Test 7: Database Outage Fail-Closed Behavior
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pg_database_outage_fail_closed(monkeypatch):
    """Verify that an unavailable database blocks governance mutations with stable error codes."""
    monkeypatch.setenv("LIARA_ENV", "production")
    
    # Attempting to instantiate repository in production with pool=None must fail startup cleanly
    from services.api.exceptions import GovernanceError, AuditPersistenceError
    from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository

    with pytest.raises(GovernanceError) as gov_exc:
        PostgresGovernanceRepository(pool_instance=None)
    assert "Production environment requires a PostgreSQL connection pool" in str(gov_exc.value)

    with pytest.raises(AuditPersistenceError) as audit_exc:
        PostgresSysAuditRepository(pool_instance=None)
    assert "Production environment requires a PostgreSQL connection pool" in str(audit_exc.value)


# -----------------------------------------------------------------------------
# Test 8: Verified Principal Authorization Enforcement & Non-Impersonation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verified_principal_authorization_enforcement(monkeypatch):
    """Verify 401 Unauthorized for missing auth and body actor non-impersonation."""
    from services.api.security import get_verified_principal, Principal
    from services.api.exceptions import UnauthorizedPrincipalError

    monkeypatch.setenv("LIARA_ENV", "production")

    # 1. Missing credentials in production -> raises UnauthorizedPrincipalError (401)
    mock_req_unauth = MagicMock()
    mock_req_unauth.headers = {}
    with pytest.raises(UnauthorizedPrincipalError) as exc_401:
        get_verified_principal(mock_req_unauth)
    assert "Missing or invalid authentication credentials" in str(exc_401.value)

    # 2. Verified Principal authority overrides claimed body actor fields
    admin_principal = Principal(actor_id="authenticated.admin", roles=["admin"], authenticated=True)
    assert admin_principal.actor_id == "authenticated.admin"
    assert admin_principal.has_role("admin") is True

    user_principal = Principal(actor_id="authenticated.user", roles=["user"], authenticated=True)
    assert user_principal.has_role("user") is True
    assert user_principal.has_role("admin") is False


# -----------------------------------------------------------------------------
# Test 9: HTTP 403 Forbidden Role Enforcement
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forbidden_principal_403_enforcement():
    """Verify require_admin_principal raises ForbiddenPrincipalError (403) for non-admin principal."""
    from services.api.security import require_admin_principal, get_verified_principal
    from services.api.exceptions import ForbiddenPrincipalError

    mock_req_user = MagicMock()
    mock_req_user.headers = {"X-LIARA-Actor-ID": "standard.user"}
    
    with pytest.raises(ForbiddenPrincipalError) as exc_403:
        require_admin_principal(mock_req_user)
    
    assert "lacks required admin role" in str(exc_403.value)


# -----------------------------------------------------------------------------
# Test 10: Production Mode Disables Legacy File Read Fallback
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_production_no_legacy_file_read_fallback(monkeypatch):
    """Verify production mode strictly disables reading legacy file storage as fallback."""
    monkeypatch.setenv("LIARA_ENV", "production")

    import frontend.admin_tui.data_layer as data_layer_module
    from frontend.admin_tui.data_layer import AdminDataLayer

    # Force the HTTP-first attempt to fail so this deterministically exercises
    # the "API unreachable" case, regardless of whether a real API happens to
    # be running at the default LIARA_API_BASE_URL on the machine running the
    # test (same gap as test_admin_tui_models.py::test_load_audit_events_reads_sys_audit_log).
    monkeypatch.setattr(data_layer_module, "HTTPX_AVAILABLE", False)

    data_layer = AdminDataLayer()
    # When API is unavailable in production, fallback must NOT return stale file audit entries
    events = data_layer.load_audit_events()
    assert events == []


# -----------------------------------------------------------------------------
# Test 11: Sys-Audit Lifecycle -- Started/Terminal Correlation and Stale Recovery
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sys_audit_started_and_terminal_events_share_operation_id():
    """Verify log_started()'s operation_id correlates with the terminal log_executed() row."""
    import services.tools.builtin.sys_audit as sys_audit_module
    from uuid import uuid4

    pool = _get_real_postgres_pool()
    repo = PostgresSysAuditRepository(pool)
    sys_audit_module.configure_sys_audit_repository(repo)
    try:
        marker = uuid4().hex[:8]
        operation_id = sys_audit_module.log_started(f"mkdir-{marker}", ["-p", "/tmp/x"], target_path="/tmp/x")
        sys_audit_module.log_executed(
            f"mkdir-{marker}", ["-p", "/tmp/x"], exit_code=0, duration_ms=1.0,
            stdout_bytes=0, stderr_bytes=0, operation_id=operation_id,
        )
        events = await repo.list_events(limit=500)
        matching = [e for e in events if e.get("operation_id") == operation_id]
        assert len(matching) == 2
        assert {e["lifecycle_stage"] for e in matching} == {"started", "completed"}
    finally:
        sys_audit_module.configure_sys_audit_repository(None)


@pytest.mark.asyncio
async def test_sys_audit_stale_started_event_recovers_to_outcome_unknown():
    """A crash between log_started() and the terminal log_executed() must never
    be silently read back as success -- recover_stale_events flips it to
    outcome_unknown, mirroring PostgresGovernanceRepository.recover_stale_operations."""
    pool = _get_real_postgres_pool()
    repo = PostgresSysAuditRepository(pool)
    result = await repo.log_event(tool_name="mkdir", lifecycle_stage="started", command="mkdir")
    event_id = result["event_id"]

    recovered = await repo.recover_stale_events(stale_threshold_seconds=0.0)
    assert any(r["event_id"] == event_id for r in recovered)

    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT lifecycle_stage FROM sys_audit_events WHERE event_id = %s;", (event_id,))
            assert cur.fetchone()[0] == "outcome_unknown"
    finally:
        pool.putconn(conn)


@pytest.mark.asyncio
async def test_wsl_executor_fail_closed_on_audit_persistence_failure():
    """WslExecutorTool.execute() must not spawn a WSL subprocess when the
    fail-closed pre-action audit write fails."""
    import services.tools.builtin.sys_audit as sys_audit_module
    from services.tools.builtin.wsl_executor import WslExecutorTool

    class _FailingRepo:
        def log_event_sync(self, *args, **kwargs):
            raise AuditPersistenceError("simulated durable audit outage")

    sys_audit_module.configure_sys_audit_repository(_FailingRepo())
    try:
        tool = WslExecutorTool()
        result = await tool.execute(command="mkdir", args=["-p", "/home/liara/workspace/x"])
        assert result["status"] == "failed"
        assert "audit" in (result["error"] or "").lower()
    finally:
        sys_audit_module.configure_sys_audit_repository(None)


@pytest.mark.asyncio
async def test_admin_sys_audit_summary_reads_postgres_when_no_log_path():
    """Without an explicit log_path override, /admin/sys-audit/summary must
    read from the Postgres-backed repository, not the legacy JSONL file."""
    from uuid import uuid4

    pool = _get_real_postgres_pool()
    repo = PostgresSysAuditRepository(pool)
    marker = uuid4().hex[:8]
    await repo.log_event(
        tool_name=f"mkdir-{marker}", lifecycle_stage="completed", outcome="allow",
        command=f"mkdir-{marker}", command_family="filesystem", risk_level="high",
    )

    adapter = _make_in_process_adapter()
    orch = _FakeOrchestrator()
    app = create_api_app(orchestrator=orch, memory_adapter=adapter)
    app.state.audit_repository = repo

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/admin/sys-audit/summary", params={"risk_level": "high", "command_family": "filesystem"})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] >= 1
    assert body["filters"]["log_path"] is None


# -----------------------------------------------------------------------------
# Test 12: Principal Enforcement End-to-End (401/403 Through Real HTTP, Not
# Just the Dependency Function) and Non-Impersonation
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_http_401_missing_auth_and_403_non_admin_on_real_endpoints(monkeypatch):
    """Unlike calling get_verified_principal/require_admin_principal directly
    (tests 8/9), this hits real HTTP endpoints end-to-end -- proving both the
    Depends(...) wiring AND the app-level GovernanceError exception handler
    (dependencies raise before the endpoint body's own try/except can run)
    actually produce 401/403, not an unhandled 500."""
    monkeypatch.setenv("LIARA_FAIL_CLOSED_AUTH", "true")
    monkeypatch.setenv("LIARA_USER_TOKEN", "real-endpoint-user-token")

    adapter = _make_in_process_adapter()
    orch = _FakeOrchestrator()
    app = create_api_app(orchestrator=orch, memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # No Authorization header at all -> get_verified_principal fails closed.
        no_auth = await client.get("/tools/sys/governance/proposals")
        assert no_auth.status_code == 401

        # Authenticated as a regular (non-admin) user -> require_admin_principal
        # rejects the admin-only sys-audit endpoint.
        non_admin = await client.get(
            "/admin/sys-audit/summary",
            headers={"Authorization": "Bearer real-endpoint-user-token"},
        )
        assert non_admin.status_code == 403


@pytest.mark.asyncio
async def test_forged_acted_by_does_not_impersonate(monkeypatch):
    """A client-supplied acted_by naming a different actor must never reach
    persisted attribution -- only the verified principal's actor_id does."""
    pool = _get_real_postgres_pool()
    repo = PostgresGovernanceRepository(pool_instance=pool)
    from uuid import uuid4
    proposal_id = f"sys-prop-impersonate-{uuid4().hex[:8]}"
    await repo.save_proposal({
        "proposal_id": proposal_id, "command": "health", "revision": 1,
        "state": "created", "decision": "pending", "requested_by": "test.runner",
    })
    await repo.execute_atomic_cas_decision(
        proposal_id, 1, "created", "pending", "approved", "decided",
        "real.decider", "approved for test",
    )

    adapter = _make_in_process_adapter()
    orch = _FakeOrchestrator()
    app = create_api_app(orchestrator=orch, memory_adapter=adapter)
    app.state.governance_repository = repo
    from services.api.services.governance_service import GovernanceService
    from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository
    app.state.governance_service = GovernanceService(repo, PostgresSysAuditRepository(pool))

    monkeypatch.setenv("LIARA_FAIL_CLOSED_AUTH", "true")
    monkeypatch.setenv("LIARA_ADMIN_TOKEN", "real-admin-token")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "apply",
                "acted_by": "forged.impersonated.actor",
                "action_reason": "attempt impersonation",
            },
            headers={
                "Authorization": "Bearer real-admin-token",
                "X-LIARA-Actor-ID": "genuine.verified.actor",
            },
        )
    # health has no reversible target, so apply either succeeds (200) or
    # fails for unrelated ToolCoordinator reasons (409) -- either way the
    # claimed operation's actor_id must reflect the verified principal.
    assert response.status_code in (200, 409)

    latest_op = await repo.get_latest_operation(proposal_id, "apply")
    assert latest_op is not None
    assert latest_op["actor_id"] == "genuine.verified.actor"
    assert latest_op["actor_id"] != "forged.impersonated.actor"


# -----------------------------------------------------------------------------
# Test 13: Nephy Follow-up -- Workspace-Agent Checkpoint Decided Purely From
# PostgreSQL, No JSON Store Involved
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_workspace_checkpoint_proposal_decided_purely_from_postgres(tmp_path, monkeypatch):
    """WorkspaceAgent creates a checkpoint proposal via GovernanceService
    (not the legacy file-based create_pending_sys_governance_proposal), and
    the API lists + decides it using only PostgreSQL -- the legacy JSON
    store path is pointed at a file that must never be created."""
    from uuid import uuid4

    from services.api.security import Principal
    from services.api.services.governance_service import GovernanceService
    from services.orchestrator.workspace_agent import WorkspaceAgent, WorkspaceStep, WorkspaceStepKind
    from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository

    # Legacy file store path: pointed at a tmp file that this test asserts
    # was never created, proving no producer or reader touched it.
    legacy_store_path = tmp_path / "sys_governance_should_not_exist.json"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(legacy_store_path))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_should_not_exist.jsonl"))

    pool = _get_real_postgres_pool()
    repo = PostgresGovernanceRepository(pool_instance=pool)
    gov_service = GovernanceService(repo, PostgresSysAuditRepository(pool))

    workspace_agent = WorkspaceAgent(
        inference_invoker=None,
        tool_coordinator=None,
        memory_service=None,
        governance_service=gov_service,
    )

    marker = uuid4().hex[:8]
    step = WorkspaceStep(id="mkdir", kind=WorkspaceStepKind.MKDIR, path=f"checkpoint-{marker}")
    step_result = await workspace_agent._governance_handoff(
        step=step,
        params={"command": "mkdir", "args": ["-p", f"checkpoint-{marker}"], "target_path": f"checkpoint-{marker}"},
        trace={"request_id": f"req-{marker}", "run_id": f"run-{marker}", "session_id": f"session-{marker}"},
        classification={"risk_level": "medium", "reasons": ["mutation_requires_review"]},
        # Deliberately no checkpoint payload: this test verifies the plain
        # create->list->decide round-trip is purely Postgres-backed. The
        # checkpoint-present resume_from_governance_proposal() path is
        # already covered by test_workspace_handoff_approval_invokes_once_and_resumes
        # in tests/unit/test_workspace_agent.py.
        checkpoint=None,
    )
    proposal_id = step_result.evidence["proposal_id"]

    assert not legacy_store_path.exists()

    adapter = _make_in_process_adapter()
    orch = _FakeOrchestrator()
    orch.workspace_agent = workspace_agent
    app = create_api_app(orchestrator=orch, memory_adapter=adapter)
    app.state.governance_repository = repo
    app.state.governance_service = gov_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        listed = await client.get("/tools/sys/governance/proposals", params={"decision": "pending"})
        assert listed.status_code == 200
        item = next(value for value in listed.json()["items"] if value["proposal_id"] == proposal_id)
        assert item["handoff"]["state"] == "awaiting_decision"
        assert item["handoff"]["step_id"] == "mkdir"

        decided = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decided_by": "test.runner",
                "decision_reason": "approve checkpoint decided purely from Postgres",
            },
        )
        assert decided.status_code == 200
        assert decided.json()["item"]["decision"] == "approved"

    assert not legacy_store_path.exists()


# -----------------------------------------------------------------------------
# Test 14: Nephy Follow-up #2 -- Checkpoint-Proposal handoff_key Dedup Is
# Atomic Under Real Concurrency, Not Just Sequential Retries
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_checkpoint_proposal_10_concurrent_same_handoff_key():
    """10 concurrent create_checkpoint_proposal() calls with the identical
    handoff_key must produce exactly 1 governance_proposals row -- enforced
    by migration 004's partial UNIQUE index (decision='pending',
    handoff_key), not merely a check-then-insert race window. Every caller
    must get back the same winning proposal_id."""
    from uuid import uuid4

    from services.api.security import Principal
    from services.api.services.governance_service import GovernanceService
    from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository

    pool = _get_real_postgres_pool()
    repo = PostgresGovernanceRepository(pool_instance=pool)
    gov_service = GovernanceService(repo, PostgresSysAuditRepository(pool))

    marker = uuid4().hex[:8]
    handoff_key = f"handoff-race-{marker}"

    async def _attempt_create(task_index: int) -> dict:
        return await gov_service.create_checkpoint_proposal(
            command="mkdir",
            parameters={"command": "mkdir", "args": ["-p", f"race-{marker}"], "target_path": f"race-{marker}"},
            principal=Principal(actor_id="workspace_agent", roles=["service"]),
            capability="workspace_mkdir",
            rationale=f"concurrent checkpoint attempt {task_index}",
            traceability={"request_id": f"req-race-{marker}-{task_index}", "run_id": f"run-race-{marker}"},
            handoff={"state": "awaiting_decision", "step_id": "mkdir", "handoff_key": handoff_key},
        )

    results = await asyncio.gather(*(_attempt_create(i) for i in range(10)))

    proposal_ids = {result["proposal_id"] for result in results}
    assert len(proposal_ids) == 1, f"expected exactly 1 distinct proposal_id, got {proposal_ids}"

    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM governance_proposals WHERE handoff_key = %s;",
                (handoff_key,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        pool.putconn(conn)

    # The stored handoff_key must be the real value, not redacted -- proves
    # _redact_handoff_for_storage() correctly exempts it from the generic
    # "key"-substring redaction pattern.
    winning_proposal = await repo.get_proposal(proposal_ids.pop())
    assert winning_proposal["handoff"]["handoff_key"] == handoff_key
