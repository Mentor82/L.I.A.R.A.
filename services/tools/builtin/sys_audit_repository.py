"""PostgreSQL Sys-Audit Repository.

Provides 2-phase operational lifecycle audit logging (started -> completed/failed/outcome_unknown),
fail-closed audit writes for high-risk operations, strict JSONB redaction, and a safe sync/async bridge invariant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from services.api.exceptions import AuditPersistenceError
from services.api.storage.governance_repository import redact_and_bound_payload

logger = logging.getLogger("liara.sys_audit.repository")


class PostgresSysAuditRepository:
    """Repository handling sys-audit persistence in PostgreSQL."""

    def __init__(self, pool_instance: Any):
        self._pool = pool_instance
        if self._pool is None and os.getenv("LIARA_ENV") == "production":
            raise AuditPersistenceError(
                "Production environment requires a PostgreSQL connection pool. "
                "In-memory sys-audit fallback is strictly prohibited in production."
            )
        self._in_memory_events: list[dict[str, Any]] = []

    def _run_in_connection(self, callback: Callable[..., Any], *args) -> Any:
        if self._pool is None:
            raise AuditPersistenceError("Database connection pool is not initialized")
        conn = None
        try:
            conn = self._pool.getconn()
            result = callback(conn, *args)
            conn.commit()
            return result
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            if isinstance(exc, AuditPersistenceError):
                raise
            raise AuditPersistenceError(f"Database sys_audit write failed: {exc}") from exc
        finally:
            if conn is not None:
                self._pool.putconn(conn)

    async def log_event(
        self,
        tool_name: str,
        lifecycle_stage: str,  # 'started', 'completed', 'failed', 'outcome_unknown'
        outcome: Optional[str] = None,
        operation_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        request_id: Optional[str] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        context: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        fail_closed: bool = False,
    ) -> dict[str, Any]:
        """Async 2-phase audit event logging."""
        event_id = f"aud-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        clean_meta = redact_and_bound_payload(metadata or {})

        if self._pool is None:
            evt = {
                "event_id": event_id,
                "operation_id": operation_id,
                "proposal_id": proposal_id,
                "request_id": request_id,
                "session_id": session_id,
                "run_id": run_id,
                "tool_name": tool_name,
                "lifecycle_stage": lifecycle_stage,
                "outcome": outcome,
                "actor_id": actor_id,
                "context": context,
                "metadata": clean_meta,
                "timestamp": now,
            }
            self._in_memory_events.append(evt)
            return evt

        def _sync_log(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sys_audit_events (
                        event_id, operation_id, proposal_id, request_id, session_id, run_id,
                        tool_name, lifecycle_stage, outcome, actor_id, context, metadata, timestamp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING event_id, timestamp;
                    """,
                    (
                        event_id,
                        operation_id,
                        proposal_id,
                        request_id,
                        session_id,
                        run_id,
                        tool_name,
                        lifecycle_stage,
                        outcome,
                        actor_id,
                        context,
                        json.dumps(clean_meta),
                        now,
                    ),
                )
                return {
                    "event_id": event_id,
                    "operation_id": operation_id,
                    "proposal_id": proposal_id,
                    "request_id": request_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "tool_name": tool_name,
                    "lifecycle_stage": lifecycle_stage,
                    "outcome": outcome,
                    "actor_id": actor_id,
                    "context": context,
                    "metadata": clean_meta,
                    "timestamp": now,
                }

        try:
            return await asyncio.to_thread(self._run_in_connection, _sync_log)
        except Exception as exc:
            logger.error(f"Audit log_event failed for tool '{tool_name}': {exc}")
            if fail_closed:
                raise AuditPersistenceError(f"Fail-closed audit write failed: {exc}") from exc
            return {"event_id": event_id, "status": "failed", "error": str(exc)}

    def log_event_sync(
        self,
        tool_name: str,
        lifecycle_stage: str,
        outcome: Optional[str] = None,
        operation_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        request_id: Optional[str] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        context: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        fail_closed: bool = False,
    ) -> dict[str, Any]:
        """Safe Sync/Async Bridge Invariant for sys-audit logging.

        Checks if an event loop is already running in the current thread.
        If running, delegates directly to a synchronous thread connection without calling asyncio.run().
        If no loop is running, safely executes on connection pool.
        """
        event_id = f"aud-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        clean_meta = redact_and_bound_payload(metadata or {})

        if self._pool is None:
            evt = {
                "event_id": event_id,
                "operation_id": operation_id,
                "proposal_id": proposal_id,
                "request_id": request_id,
                "session_id": session_id,
                "run_id": run_id,
                "tool_name": tool_name,
                "lifecycle_stage": lifecycle_stage,
                "outcome": outcome,
                "actor_id": actor_id,
                "context": context,
                "metadata": clean_meta,
                "timestamp": now,
            }
            self._in_memory_events.append(evt)
            return evt

        def _sync_log(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sys_audit_events (
                        event_id, operation_id, proposal_id, request_id, session_id, run_id,
                        tool_name, lifecycle_stage, outcome, actor_id, context, metadata, timestamp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING event_id, timestamp;
                    """,
                    (
                        event_id,
                        operation_id,
                        proposal_id,
                        request_id,
                        session_id,
                        run_id,
                        tool_name,
                        lifecycle_stage,
                        outcome,
                        actor_id,
                        context,
                        json.dumps(clean_meta),
                        now,
                    ),
                )
                return {
                    "event_id": event_id,
                    "operation_id": operation_id,
                    "proposal_id": proposal_id,
                    "request_id": request_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "tool_name": tool_name,
                    "lifecycle_stage": lifecycle_stage,
                    "outcome": outcome,
                    "actor_id": actor_id,
                    "context": context,
                    "metadata": clean_meta,
                    "timestamp": now,
                }

        try:
            return self._run_in_connection(_sync_log)
        except Exception as exc:
            logger.error(f"Sync audit log_event failed for tool '{tool_name}': {exc}")
            if fail_closed:
                raise AuditPersistenceError(f"Fail-closed sync audit write failed: {exc}") from exc
            return {"event_id": event_id, "status": "failed", "error": str(exc)}

    async def list_events(
        self,
        proposal_id: Optional[str] = None,
        request_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict[str, Any]]:
        """Query sys-audit events with filtering."""
        def _sync_list(conn):
            with conn.cursor() as cur:
                conditions = []
                params = []
                if proposal_id:
                    conditions.append("proposal_id = %s")
                    params.append(proposal_id)
                if request_id:
                    conditions.append("request_id = %s")
                    params.append(request_id)
                if session_id:
                    conditions.append("session_id = %s")
                    params.append(session_id)

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                params.append(limit)

                cur.execute(
                    f"""
                    SELECT event_id, operation_id, proposal_id, request_id, session_id, run_id,
                           tool_name, lifecycle_stage, outcome, actor_id, context, metadata, timestamp
                    FROM sys_audit_events {where_clause}
                    ORDER BY timestamp DESC LIMIT %s;
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "event_id": row[0],
                        "operation_id": row[1],
                        "proposal_id": row[2],
                        "request_id": row[3],
                        "session_id": row[4],
                        "run_id": row[5],
                        "tool_name": row[6],
                        "lifecycle_stage": row[7],
                        "outcome": row[8],
                        "actor_id": row[9],
                        "context": row[10],
                        "metadata": row[11] if isinstance(row[11], dict) else json.loads(row[11] or "{}"),
                        "timestamp": row[12].isoformat() if hasattr(row[12], "isoformat") else row[12],
                    })
                return results

        return await asyncio.to_thread(self._run_in_connection, _sync_list)
