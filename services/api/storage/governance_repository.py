"""PostgreSQL Governance Repository.

Enforces Compare-And-Swap (CAS) state transitions, coupled proposal/operation states,
operation claims with DB idempotency, and immutable event logging using PostgreSQL transactions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from services.api.exceptions import (
    GovernanceConflictError,
    GovernanceError,
    GovernanceNotFoundError,
)
from services.tools.governance import (
    load_sys_governance_proposals,
    persist_sys_governance_proposals,
    sys_governance_store_path,
)

logger = logging.getLogger("liara.governance.repository")

_SENSITIVE_KEY_PATTERN = re.compile(r"(token|secret|password|key|auth|bearer)", re.IGNORECASE)
MAX_JSONB_BYTES = 64 * 1024  # 64 KB max payload size for JSONB columns


def redact_and_bound_payload(data: Any, max_bytes: int = MAX_JSONB_BYTES) -> dict[str, Any]:
    """Recursively redact sensitive key patterns and enforce size limits on JSONB attributes."""
    if not isinstance(data, dict):
        return {}

    def _clean(item: Any) -> Any:
        if isinstance(item, dict):
            cleaned = {}
            for k, v in item.items():
                if _SENSITIVE_KEY_PATTERN.search(str(k)):
                    cleaned[k] = "[REDACTED]"
                else:
                    cleaned[k] = _clean(v)
            return cleaned
        elif isinstance(item, list):
            return [_clean(v) for v in item[:100]]  # Bound list length
        elif isinstance(item, str) and len(item) > 4096:
            return item[:4096] + "... [TRUNCATED]"
        return item

    cleaned_dict = _clean(data)
    encoded = json.dumps(cleaned_dict, ensure_ascii=True).encode("utf-8")
    if len(encoded) > max_bytes:
        return {
            "truncated_reason": f"payload exceeded limit of {max_bytes} bytes",
            "summary": str(cleaned_dict)[:500],
        }
    return cleaned_dict


class PostgresGovernanceRepository:
    """Repository handling governance persistence and transactional state transitions."""

    def __init__(self, pool_instance: Any, in_memory_store: Optional[dict[str, Any]] = None, app_state: Any = None):
        self._pool = pool_instance
        if self._pool is None and os.getenv("LIARA_ENV") == "production":
            raise GovernanceError(
                "Production environment requires a PostgreSQL connection pool. "
                "In-memory governance fallback is strictly prohibited in production."
            )
        self._in_memory_proposals: dict[str, dict[str, Any]] = in_memory_store if in_memory_store is not None else {}
        self._in_memory_events: list[dict[str, Any]] = []
        self._app_state = app_state

    def _get_proposals_dict(self) -> dict[str, dict[str, Any]]:
        if self._app_state and hasattr(self._app_state, "sys_tool_proposals"):
            return self._app_state.sys_tool_proposals
        return self._in_memory_proposals

    def _run_in_connection(self, callback: Callable[..., Any], *args) -> Any:
        if self._pool is None:
            raise GovernanceError("Database connection pool is not initialized")
        conn = None
        try:
            conn = self._pool.getconn()
            result = callback(conn, *args)
            conn.commit()
            return result
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            if isinstance(exc, GovernanceError):
                raise
            raise GovernanceError(f"Database governance query failed: {exc}") from exc
        finally:
            if conn is not None:
                self._pool.putconn(conn)

    async def save_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Insert a newly created proposal and log initial event."""
        if self._pool is None:
            pid = str(proposal["proposal_id"])
            p_copy = dict(proposal)
            p_copy.setdefault("revision", 1)
            p_copy.setdefault("decision", "pending")
            p_copy.setdefault("state", "created")
            p_copy.setdefault("created_at", datetime.now(UTC).isoformat())
            p_copy.setdefault("updated_at", p_copy["created_at"])
            self._get_proposals_dict()[pid] = p_copy
            self._in_memory_events.append({
                "event_id": f"evt-{uuid4().hex[:12]}",
                "proposal_id": pid,
                "proposal_revision": p_copy["revision"],
                "event_type": "proposal_created",
                "decision": p_copy["decision"],
                "state": p_copy["state"],
                "actor_id": p_copy.get("requested_by"),
                "created_at": p_copy["created_at"],
            })
            return p_copy

        def _sync_save(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO governance_proposals (
                        proposal_id, revision, decision, state, tool_name, command,
                        parameters, policy_check, capability, rationale, requested_by,
                        decided_by, decision_reason, decision_at, traceability, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING proposal_id, revision, decision, state, created_at, updated_at;
                    """,
                    (
                        proposal["proposal_id"],
                        proposal.get("revision", 1),
                        proposal.get("decision", "pending"),
                        proposal.get("state", "created"),
                        proposal.get("tool_name", "sys"),
                        proposal.get("command", ""),
                        json.dumps(redact_and_bound_payload(proposal.get("parameters"))),
                        json.dumps(redact_and_bound_payload(proposal.get("policy_check"))),
                        proposal.get("capability"),
                        proposal.get("rationale"),
                        proposal.get("requested_by"),
                        proposal.get("decided_by"),
                        proposal.get("decision_reason"),
                        proposal.get("decision_at"),
                        json.dumps(redact_and_bound_payload(proposal.get("traceability"))),
                        proposal.get("created_at", datetime.now(UTC).isoformat()),
                        proposal.get("updated_at", datetime.now(UTC).isoformat()),
                    ),
                )
                
                # Insert initial creation event
                event_id = f"evt-{uuid4().hex[:12]}"
                cur.execute(
                    """
                    INSERT INTO governance_events (
                        event_id, proposal_id, proposal_revision, event_type, decision, state, actor_id, traceability, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        event_id,
                        proposal["proposal_id"],
                        proposal.get("revision", 1),
                        "proposal_created",
                        proposal.get("decision", "pending"),
                        proposal.get("state", "created"),
                        proposal.get("requested_by"),
                        json.dumps(redact_and_bound_payload(proposal.get("traceability"))),
                        proposal.get("created_at", datetime.now(UTC).isoformat()),
                    ),
                )
            return proposal

        return await asyncio.to_thread(self._run_in_connection, _sync_save)

    async def get_proposal(self, proposal_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single proposal by ID."""
        if self._pool is None:
            dict_store = self._get_proposals_dict()
            item = dict_store.get(proposal_id)
            if not item:
                # Check disk file fallback if test fixture wrote directly to disk
                p_path = Path(self._app_state.sys_tool_proposals_path) if self._app_state and getattr(self._app_state, "sys_tool_proposals_path", None) else sys_governance_store_path()
                disk_items = load_sys_governance_proposals(p_path)
                if proposal_id in disk_items:
                    item = disk_items[proposal_id]
                    dict_store[proposal_id] = item
            return dict(item) if item else None

        def _sync_get(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT proposal_id, revision, decision, state, tool_name, command,
                           parameters, policy_check, capability, rationale, requested_by,
                           decided_by, decision_reason, decision_at, traceability, created_at, updated_at
                    FROM governance_proposals WHERE proposal_id = %s;
                    """,
                    (proposal_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "proposal_id": row[0],
                    "revision": row[1],
                    "decision": row[2],
                    "state": row[3],
                    "tool_name": row[4],
                    "command": row[5],
                    "parameters": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
                    "policy_check": row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
                    "capability": row[8],
                    "rationale": row[9],
                    "requested_by": row[10],
                    "decided_by": row[11],
                    "decision_reason": row[12],
                    "decision_at": row[13].isoformat() if hasattr(row[13], "isoformat") else row[13],
                    "traceability": row[14] if isinstance(row[14], dict) else json.loads(row[14] or "{}"),
                    "created_at": row[15].isoformat() if hasattr(row[15], "isoformat") else row[15],
                    "updated_at": row[16].isoformat() if hasattr(row[16], "isoformat") else row[16],
                }

        return await asyncio.to_thread(self._run_in_connection, _sync_get)

    async def list_proposals(self, decision: str = "all", limit: int = 50) -> List[dict[str, Any]]:
        """List proposals with filtering."""
        if self._pool is None:
            items = list(self._get_proposals_dict().values())
            if decision != "all":
                items = [item for item in items if item.get("decision") == decision]
            items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            return [dict(i) for i in items[:limit]]

        def _sync_list(conn):
            with conn.cursor() as cur:
                if decision != "all":
                    cur.execute(
                        """
                        SELECT proposal_id, revision, decision, state, tool_name, command,
                               parameters, policy_check, capability, rationale, requested_by,
                               decided_by, decision_reason, decision_at, traceability, created_at, updated_at
                        FROM governance_proposals WHERE decision = %s
                        ORDER BY created_at DESC LIMIT %s;
                        """,
                        (decision, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT proposal_id, revision, decision, state, tool_name, command,
                               parameters, policy_check, capability, rationale, requested_by,
                               decided_by, decision_reason, decision_at, traceability, created_at, updated_at
                        FROM governance_proposals ORDER BY created_at DESC LIMIT %s;
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "proposal_id": row[0],
                        "revision": row[1],
                        "decision": row[2],
                        "state": row[3],
                        "tool_name": row[4],
                        "command": row[5],
                        "parameters": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
                        "policy_check": row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
                        "capability": row[8],
                        "rationale": row[9],
                        "requested_by": row[10],
                        "decided_by": row[11],
                        "decision_reason": row[12],
                        "decision_at": row[13].isoformat() if hasattr(row[13], "isoformat") else row[13],
                        "traceability": row[14] if isinstance(row[14], dict) else json.loads(row[14] or "{}"),
                        "created_at": row[15].isoformat() if hasattr(row[15], "isoformat") else row[15],
                        "updated_at": row[16].isoformat() if hasattr(row[16], "isoformat") else row[16],
                    })
                return results

        return await asyncio.to_thread(self._run_in_connection, _sync_list)

    async def execute_atomic_cas_decision(
        self,
        proposal_id: str,
        expected_revision: int,
        expected_state: str,
        expected_decision: str,
        new_decision: str,
        new_state: str,
        decided_by: str,
        decision_reason: Optional[str],
        traceability: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            prop = dict_store.get(proposal_id)
            if not prop:
                # Fallback load from disk file
                p_path = Path(self._app_state.sys_tool_proposals_path) if self._app_state and getattr(self._app_state, "sys_tool_proposals_path", None) else sys_governance_store_path()
                disk_items = load_sys_governance_proposals(p_path)
                if proposal_id in disk_items:
                    prop = disk_items[proposal_id]
                    dict_store[proposal_id] = prop

            if not prop:
                raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
            actual_rev = prop.get("revision", 1)
            actual_state = prop.get("state", "created")
            actual_decision = prop.get("decision", "pending")
            if (
                actual_rev != expected_revision
                or actual_state != expected_state
                or actual_decision != expected_decision
            ):
                raise GovernanceConflictError(
                    f"CAS transition failed for proposal {proposal_id}: "
                    f"expected (rev={expected_revision}, state={expected_state}, decision={expected_decision}), "
                    f"actual (rev={actual_rev}, state={actual_state}, decision={actual_decision})"
                )
            prop["revision"] = actual_rev + 1
            prop["decision"] = new_decision
            prop["state"] = new_state
            prop["decided_by"] = decided_by
            prop["decision_reason"] = decision_reason
            prop["decision_at"] = now
            prop["updated_at"] = now
            
            # Persist back to disk if path is defined
            p_path = Path(self._app_state.sys_tool_proposals_path) if self._app_state and getattr(self._app_state, "sys_tool_proposals_path", None) else sys_governance_store_path()
            persist_sys_governance_proposals(dict_store, p_path)

            self._in_memory_events.append({
                "event_id": f"evt-{uuid4().hex[:12]}",
                "proposal_id": proposal_id,
                "proposal_revision": prop["revision"],
                "event_type": "proposal_decided",
                "decision": new_decision,
                "state": new_state,
                "actor_id": decided_by,
                "created_at": now,
            })
            return dict(prop)

        def _sync_cas(conn):
            with conn.cursor() as cur:
                # Execute CAS update checking (proposal_id, revision, state, decision)
                cur.execute(
                    """
                    UPDATE governance_proposals
                    SET revision = revision + 1,
                        decision = %s,
                        state = %s,
                        decided_by = %s,
                        decision_reason = %s,
                        decision_at = %s,
                        updated_at = %s
                    WHERE proposal_id = %s AND revision = %s AND state = %s AND decision = %s
                    RETURNING proposal_id, revision, decision, state, updated_at;
                    """,
                    (
                        new_decision,
                        new_state,
                        decided_by,
                        decision_reason,
                        now,
                        now,
                        proposal_id,
                        expected_revision,
                        expected_state,
                        expected_decision,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    # Check if proposal exists at all to provide accurate exception
                    cur.execute("SELECT revision, state, decision FROM governance_proposals WHERE proposal_id = %s;", (proposal_id,))
                    existing = cur.fetchone()
                    if not existing:
                        raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
                    raise GovernanceConflictError(
                        f"CAS transition failed for proposal {proposal_id}: "
                        f"expected (rev={expected_revision}, state={expected_state}, decision={expected_decision}), "
                        f"actual (rev={existing[0]}, state={existing[1]}, decision={existing[2]})"
                    )

                new_revision = row[1]
                # Insert immutable governance event in the SAME transaction
                event_id = f"evt-{uuid4().hex[:12]}"
                cur.execute(
                    """
                    INSERT INTO governance_events (
                        event_id, proposal_id, proposal_revision, event_type, decision, state, actor_id, traceability, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        event_id,
                        proposal_id,
                        new_revision,
                        "proposal_decided",
                        new_decision,
                        new_state,
                        decided_by,
                        json.dumps(redact_and_bound_payload(traceability or {})),
                        now,
                    ),
                )

                return {
                    "proposal_id": proposal_id,
                    "revision": new_revision,
                    "decision": new_decision,
                    "state": new_state,
                    "decided_by": decided_by,
                    "decision_reason": decision_reason,
                    "decision_at": now,
                    "updated_at": now,
                }

        return await asyncio.to_thread(self._run_in_connection, _sync_cas)

    async def claim_operation(
        self,
        proposal_id: str,
        operation_type: str,  # 'invoke', 'apply', 'rollback'
        idempotency_key: str,
        actor_id: str,
        target_state: str,    # e.g., 'invoking', 'applying', 'rolling_back'
        details: Optional[dict[str, Any]] = None,
    ) -> Tuple[dict[str, Any], dict[str, Any]]:
        """Atomically claim an external side-effect operation with DB-level idempotency constraint."""
        now = datetime.now(UTC).isoformat()
        operation_id = f"op-{uuid4().hex[:12]}"

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            prop = dict_store.get(proposal_id)
            if not prop:
                raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
            if prop.get("decision") != "approved":
                raise GovernanceConflictError(f"Proposal {proposal_id} is not approved (decision={prop.get('decision')})")

            prop["revision"] = prop.get("revision", 1) + 1
            prop["state"] = target_state
            prop["updated_at"] = now

            self._in_memory_events.append({
                "event_id": f"evt-{uuid4().hex[:12]}",
                "proposal_id": proposal_id,
                "operation_id": operation_id,
                "proposal_revision": prop["revision"],
                "event_type": "operation_started",
                "decision": prop.get("decision", "approved"),
                "state": target_state,
                "actor_id": actor_id,
                "created_at": now,
            })
            return (
                dict(prop),
                {"operation_id": operation_id, "state": "started", "idempotency_key": idempotency_key, "reused": False},
            )
            with conn.cursor() as cur:
                # 1. Fetch current proposal
                cur.execute(
                    "SELECT revision, decision, state FROM governance_proposals WHERE proposal_id = %s FOR UPDATE;",
                    (proposal_id,),
                )
                prop_row = cur.fetchone()
                if not prop_row:
                    raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")

                rev, dec, curr_state = prop_row
                if dec != "approved":
                    raise GovernanceConflictError(f"Proposal {proposal_id} is not approved (decision={dec})")

                # 2. Insert claimed operation with UNIQUE constraint on (proposal_id, operation_type, idempotency_key)
                try:
                    cur.execute(
                        """
                        INSERT INTO governance_operations (
                            operation_id, proposal_id, operation_type, state, idempotency_key, actor_id, details, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING operation_id, state, created_at;
                        """,
                        (
                            operation_id,
                            proposal_id,
                            operation_type,
                            "started",
                            idempotency_key,
                            actor_id,
                            json.dumps(redact_and_bound_payload(details or {})),
                            now,
                            now,
                        ),
                    )
                except Exception as exc:
                    # Check for unique constraint violation (duplicate idempotency key)
                    if "uq_operation_idempotency" in str(exc) or "unique" in str(exc).lower():
                        conn.rollback()
                        # Re-query existing operation safely
                        with conn.cursor() as read_cur:
                            read_cur.execute(
                                """
                                SELECT operation_id, state, created_at FROM governance_operations
                                WHERE proposal_id = %s AND operation_type = %s AND idempotency_key = %s;
                                """,
                                (proposal_id, operation_type, idempotency_key),
                            )
                            existing_op = read_cur.fetchone()
                            if existing_op:
                                return (
                                    {"proposal_id": proposal_id, "revision": rev, "decision": dec, "state": curr_state},
                                    {"operation_id": existing_op[0], "state": existing_op[1], "idempotency_key": idempotency_key, "reused": True},
                                )
                    raise

                # 3. Update proposal state (coupled invariant)
                new_rev = rev + 1
                cur.execute(
                    """
                    UPDATE governance_proposals
                    SET revision = %s, state = %s, updated_at = %s
                    WHERE proposal_id = %s;
                    """,
                    (new_rev, target_state, now, proposal_id),
                )

                # 4. Insert governance event
                event_id = f"evt-{uuid4().hex[:12]}"
                cur.execute(
                    """
                    INSERT INTO governance_events (
                        event_id, proposal_id, operation_id, proposal_revision, event_type, decision, state, actor_id, traceability, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        event_id,
                        proposal_id,
                        operation_id,
                        new_rev,
                        "operation_started",
                        dec,
                        target_state,
                        actor_id,
                        json.dumps(redact_and_bound_payload(details or {})),
                        now,
                    ),
                )

                return (
                    {"proposal_id": proposal_id, "revision": new_rev, "decision": dec, "state": target_state},
                    {"operation_id": operation_id, "state": "started", "idempotency_key": idempotency_key, "reused": False},
                )

        return await asyncio.to_thread(self._run_in_connection, _sync_claim)

    async def complete_operation(
        self,
        operation_id: str,
        proposal_id: str,
        final_op_state: str,       # 'completed', 'failed', 'outcome_unknown'
        final_proposal_state: str, # 'invoked', 'applied', 'rolled_back', 'failed'
        actor_id: str,
        details: Optional[dict[str, Any]] = None,
    ) -> Tuple[dict[str, Any], dict[str, Any]]:
        """Complete or fail a claimed operation and transition coupled proposal state."""
        now = datetime.now(UTC).isoformat()

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            prop = dict_store.get(proposal_id)
            if not prop:
                raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
            prop["revision"] = prop.get("revision", 1) + 1
            prop["state"] = final_proposal_state
            prop["updated_at"] = now

            self._in_memory_events.append({
                "event_id": f"evt-{uuid4().hex[:12]}",
                "proposal_id": proposal_id,
                "operation_id": operation_id,
                "proposal_revision": prop["revision"],
                "event_type": "operation_completed" if final_op_state == "completed" else "operation_failed",
                "decision": prop.get("decision", "approved"),
                "state": final_proposal_state,
                "actor_id": actor_id,
                "created_at": now,
            })
            return (dict(prop), {"operation_id": operation_id, "state": final_op_state})
            with conn.cursor() as cur:
                # Update operation record
                cur.execute(
                    """
                    UPDATE governance_operations
                    SET state = %s, updated_at = %s, details = %s
                    WHERE operation_id = %s;
                    """,
                    (final_op_state, now, json.dumps(redact_and_bound_payload(details or {})), operation_id),
                )

                # Update proposal record
                cur.execute(
                    """
                    UPDATE governance_proposals
                    SET revision = revision + 1, state = %s, updated_at = %s
                    WHERE proposal_id = %s
                    RETURNING revision, decision;
                    """,
                    (final_proposal_state, now, proposal_id),
                )
                row = cur.fetchone()
                if not row:
                    raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")

                new_rev = row[0]
                dec = row[1]

                # Insert terminal governance event
                event_id = f"evt-{uuid4().hex[:12]}"
                event_type = "operation_completed" if final_op_state == "completed" else "operation_failed"
                cur.execute(
                    """
                    INSERT INTO governance_events (
                        event_id, proposal_id, operation_id, proposal_revision, event_type, decision, state, actor_id, traceability, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        event_id,
                        proposal_id,
                        operation_id,
                        new_rev,
                        event_type,
                        dec,
                        final_proposal_state,
                        actor_id,
                        json.dumps(redact_and_bound_payload(details or {})),
                        now,
                    ),
                )

                return (
                    {"proposal_id": proposal_id, "revision": new_rev, "decision": dec, "state": final_proposal_state},
                    {"operation_id": operation_id, "state": final_op_state},
                )

        return await asyncio.to_thread(self._run_in_connection, _sync_complete)

    async def recover_stale_operations(self, stale_threshold_seconds: float = 300.0) -> List[dict[str, Any]]:
        """Identify claimed operations stuck in 'started' state past threshold and mark them as outcome_unknown."""
        now = datetime.now(UTC).isoformat()

        if self._pool is None:
            recovered = []
            dict_store = self._get_proposals_dict()
            for pid, prop in list(dict_store.items()):
                if prop.get("state") in {"invoking", "applying", "rolling_back", "started"}:
                    prop["state"] = "outcome_unknown"
                    prop["updated_at"] = now
                    rec_item = {"proposal_id": pid, "operation_id": f"op-stale-{pid[:8]}", "state": "outcome_unknown"}
                    recovered.append(rec_item)
                    self._in_memory_events.append({
                        "event_id": f"evt-{uuid4().hex[:12]}",
                        "proposal_id": pid,
                        "event_type": "operation_recovered",
                        "decision": prop.get("decision", "approved"),
                        "state": "outcome_unknown",
                        "created_at": now,
                    })
            return recovered

        def _sync_recover(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operation_id, proposal_id, operation_type, idempotency_key, actor_id
                    FROM governance_operations
                    WHERE state = 'started' AND updated_at < NOW() - (%s * INTERVAL '1 second')
                    FOR UPDATE;
                    """,
                    (stale_threshold_seconds,),
                )
                rows = cur.fetchall()
                recovered_ops = []
                for row in rows:
                    op_id, prop_id, op_type, idemp_key, actor = row
                    
                    cur.execute(
                        "UPDATE governance_operations SET state = 'outcome_unknown', updated_at = %s WHERE operation_id = %s;",
                        (now, op_id),
                    )
                    cur.execute(
                        "UPDATE governance_proposals SET revision = revision + 1, state = 'outcome_unknown', updated_at = %s WHERE proposal_id = %s RETURNING revision, decision;",
                        (now, prop_id),
                    )
                    prop_res = cur.fetchone()
                    rev = prop_res[0] if prop_res else 1
                    dec = prop_res[1] if prop_res else "approved"

                    event_id = f"evt-{uuid4().hex[:12]}"
                    cur.execute(
                        """
                        INSERT INTO governance_events (
                            event_id, proposal_id, operation_id, proposal_revision, event_type, decision, state, actor_id, created_at
                        ) VALUES (%s, %s, %s, %s, 'operation_recovered', %s, 'outcome_unknown', %s, %s);
                        """,
                        (event_id, prop_id, op_id, rev, dec, actor or "system.recovery", now),
                    )
                    recovered_ops.append({
                        "operation_id": op_id,
                        "proposal_id": prop_id,
                        "state": "outcome_unknown",
                        "recovered_at": now,
                    })
                return recovered_ops

        return await asyncio.to_thread(self._run_in_connection, _sync_recover)
