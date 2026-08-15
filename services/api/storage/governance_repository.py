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
MAX_JSONB_BYTES = 4 * 1024  # 4 KB max payload size for JSONB columns


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


def _redact_handoff_for_storage(handoff: Any) -> dict[str, Any]:
    """Like redact_and_bound_payload, but preserves handoff_key verbatim.

    handoff_key is an opaque idempotency identifier (a hash of run_id/
    step_id/invocation_digest), not a secret -- but _SENSITIVE_KEY_PATTERN's
    broad substring match on "key" would otherwise redact it to a constant
    "[REDACTED]" string, silently defeating handoff-key-based dedup (every
    checkpoint proposal would collide on the placeholder instead of their
    real key). Mirrors why invocation_digest is stored as its own unredacted
    column rather than only inside a redacted JSONB blob.
    """
    source = handoff if isinstance(handoff, dict) else {}
    handoff_key = source.get("handoff_key") or None
    cleaned = redact_and_bound_payload(source)
    if handoff_key:
        cleaned["handoff_key"] = handoff_key
    return cleaned


def _row_to_proposal_dict(row: tuple) -> dict[str, Any]:
    """Map a governance_proposals SELECT row (22-column shape used by get_proposal/
    list_proposals) into the proposal dict shape the rest of the codebase expects."""
    def _json_field(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else json.loads(value or "{}")

    return {
        "proposal_id": row[0],
        "revision": row[1],
        "decision": row[2],
        "state": row[3],
        "tool_name": row[4],
        "command": row[5],
        "parameters": _json_field(row[6]),
        "policy_check": _json_field(row[7]),
        "capability": row[8],
        "rationale": row[9],
        "requested_by": row[10],
        "decided_by": row[11],
        "decision_reason": row[12],
        "decision_at": row[13].isoformat() if hasattr(row[13], "isoformat") else row[13],
        "traceability": _json_field(row[14]),
        "created_at": row[15].isoformat() if hasattr(row[15], "isoformat") else row[15],
        "updated_at": row[16].isoformat() if hasattr(row[16], "isoformat") else row[16],
        "handoff": _json_field(row[17]),
        "transaction": _json_field(row[18]),
        "invocation": _json_field(row[19]),
        "invocation_digest": row[20],
        "max_invocations": row[21],
    }


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
        self._in_memory_operations: dict[str, dict[str, Any]] = {}
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
            # Never surface raw DB/driver exception text (may contain SQL,
            # table/column names, connection details) in the public
            # GovernanceError message -- log the real cause server-side only.
            logger.error("Database governance query failed", exc_info=exc)
            raise GovernanceError("Database governance operation failed") from exc
        finally:
            if conn is not None:
                self._pool.putconn(conn)

    async def save_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Insert a newly created proposal and log initial event.

        Raises GovernanceConflictError if proposal["handoff"]["handoff_key"]
        collides with an already-pending proposal's handoff_key -- enforced
        by a real partial UNIQUE index in Postgres (decision='pending',
        handoff_key IS NOT NULL), an equivalent linear scan in the
        in-memory/no-pool branch. This closes the check-then-insert race a
        caller doing idempotent checkpoint creation
        (GovernanceService.create_checkpoint_proposal) would otherwise have
        under genuine concurrency: catch this and return the existing
        proposal instead of treating it as a hard failure.
        """
        handoff = proposal.get("handoff") if isinstance(proposal.get("handoff"), dict) else {}
        handoff_key = handoff.get("handoff_key") or None

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            if handoff_key:
                for existing in dict_store.values():
                    existing_handoff = existing.get("handoff") if isinstance(existing.get("handoff"), dict) else {}
                    if (
                        str(existing.get("decision") or "") == "pending"
                        and str(existing_handoff.get("handoff_key") or "") == handoff_key
                    ):
                        raise GovernanceConflictError(f"Duplicate pending handoff_key: {handoff_key}")
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
                try:
                    cur.execute(
                        """
                        INSERT INTO governance_proposals (
                            proposal_id, revision, decision, state, tool_name, command,
                            parameters, policy_check, capability, rationale, requested_by,
                            decided_by, decision_reason, decision_at, traceability, created_at, updated_at,
                            handoff, transaction, invocation, invocation_digest, max_invocations, handoff_key
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                            json.dumps(_redact_handoff_for_storage(proposal.get("handoff"))),
                            json.dumps(redact_and_bound_payload(proposal.get("transaction"))),
                            json.dumps(redact_and_bound_payload(proposal.get("invocation"))),
                            proposal.get("invocation_digest"),
                            proposal.get("max_invocations", 1),
                            handoff_key,
                        ),
                    )
                except Exception as exc:
                    if "uq_governance_proposals_pending_handoff_key" in str(exc) or "unique" in str(exc).lower():
                        conn.rollback()
                        raise GovernanceConflictError(f"Duplicate pending handoff_key: {handoff_key}") from exc
                    raise

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

    async def find_pending_proposal_by_handoff_key(self, handoff_key: str) -> Optional[dict[str, Any]]:
        """Look up a pending proposal by its handoff_key via the dedicated
        (unredacted, indexed) column -- not by scanning the JSONB handoff
        blob, whose handoff_key copy may be redacted (see
        _redact_handoff_for_storage)."""
        if self._pool is None:
            for existing in self._get_proposals_dict().values():
                existing_handoff = existing.get("handoff") if isinstance(existing.get("handoff"), dict) else {}
                if (
                    str(existing.get("decision") or "") == "pending"
                    and str(existing_handoff.get("handoff_key") or "") == handoff_key
                ):
                    return dict(existing)
            return None

        def _sync_find(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT proposal_id, revision, decision, state, tool_name, command,
                           parameters, policy_check, capability, rationale, requested_by,
                           decided_by, decision_reason, decision_at, traceability, created_at, updated_at,
                           handoff, transaction, invocation, invocation_digest, max_invocations
                    FROM governance_proposals WHERE decision = 'pending' AND handoff_key = %s LIMIT 1;
                    """,
                    (handoff_key,),
                )
                row = cur.fetchone()
                return _row_to_proposal_dict(row) if row else None

        return await asyncio.to_thread(self._run_in_connection, _sync_find)

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
                           decided_by, decision_reason, decision_at, traceability, created_at, updated_at,
                           handoff, transaction, invocation, invocation_digest, max_invocations
                    FROM governance_proposals WHERE proposal_id = %s;
                    """,
                    (proposal_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return _row_to_proposal_dict(row)

        return await asyncio.to_thread(self._run_in_connection, _sync_get)

    async def list_proposals(self, decision: str = "all", limit: Optional[int] = 50) -> List[dict[str, Any]]:
        """List proposals with filtering. limit=None means no SQL LIMIT (full scan),
        used by callers that need accurate aggregate stats across all proposals."""
        if self._pool is None:
            items = list(self._get_proposals_dict().values())
            if decision != "all":
                items = [item for item in items if item.get("decision") == decision]
            items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            return [dict(i) for i in items[:limit]]

        def _sync_list(conn):
            with conn.cursor() as cur:
                where_clause = "WHERE decision = %s" if decision != "all" else ""
                limit_clause = "LIMIT %s" if limit is not None else ""
                params: list[Any] = [decision] if decision != "all" else []
                if limit is not None:
                    params.append(limit)
                cur.execute(
                    f"""
                    SELECT proposal_id, revision, decision, state, tool_name, command,
                           parameters, policy_check, capability, rationale, requested_by,
                           decided_by, decision_reason, decision_at, traceability, created_at, updated_at,
                           handoff, transaction, invocation, invocation_digest, max_invocations
                    FROM governance_proposals {where_clause}
                    ORDER BY created_at DESC {limit_clause};
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
                return [_row_to_proposal_dict(row) for row in rows]

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
        handoff_update: Optional[dict[str, Any]] = None,
        invocation_update: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Atomically transition a proposal's decision/state.

        When handoff_update/invocation_update are given, those columns are
        set in the SAME UPDATE statement and transaction as the decision
        itself -- computing their values must happen before calling this (see
        GovernanceService.decide_proposal), never as a second CAS call
        afterward, since a second call would use a now-stale expected_revision
        once this one has already committed.
        """
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
            if handoff_update is not None:
                prop["handoff"] = handoff_update
            if invocation_update is not None:
                prop["invocation"] = invocation_update

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
                set_clauses = [
                    "revision = revision + 1",
                    "decision = %s",
                    "state = %s",
                    "decided_by = %s",
                    "decision_reason = %s",
                    "decision_at = %s",
                    "updated_at = %s",
                ]
                params: list[Any] = [new_decision, new_state, decided_by, decision_reason, now, now]
                if handoff_update is not None:
                    set_clauses.append("handoff = %s")
                    params.append(json.dumps(redact_and_bound_payload(handoff_update)))
                if invocation_update is not None:
                    set_clauses.append("invocation = %s")
                    params.append(json.dumps(redact_and_bound_payload(invocation_update)))
                params.extend([proposal_id, expected_revision, expected_state, expected_decision])

                cur.execute(
                    f"""
                    UPDATE governance_proposals
                    SET {', '.join(set_clauses)}
                    WHERE proposal_id = %s AND revision = %s AND state = %s AND decision = %s
                    RETURNING proposal_id, revision, decision, state, updated_at, handoff, invocation;
                    """,
                    tuple(params),
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
                new_handoff = row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}")
                new_invocation = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
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
                    "handoff": new_handoff,
                    "invocation": new_invocation,
                }

        return await asyncio.to_thread(self._run_in_connection, _sync_cas)

    async def update_handoff(
        self,
        proposal_id: str,
        expected_revision: int,
        handoff: dict[str, Any],
        invocation: Optional[dict[str, Any]] = None,
        event_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        event_details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """CAS-guarded update of the handoff column (and optionally invocation),
        keyed on revision alone.

        For the case where a handoff transition depends on the result of an
        external side effect (e.g. workspace_agent.resume_from_governance_proposal)
        that genuinely cannot be part of the same DB transaction as the
        decision CAS: callers must pass the *fresh* revision returned by that
        decision CAS (execute_atomic_cas_decision's return value), not the
        pre-decision revision, since the decision CAS already bumped it once.

        event_type, when given, also inserts a governance_events row in the
        SAME transaction as the CAS update -- callers that don't need an
        audit-trail entry for this particular handoff transition (e.g. the
        raw passthrough used for workspace-agent resume-result persistence,
        which follows a decision CAS that already logged its own event) can
        omit it and no event row is written, preserving prior behavior.
        """
        now = datetime.now(UTC).isoformat()

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            prop = dict_store.get(proposal_id)
            if not prop:
                p_path = Path(self._app_state.sys_tool_proposals_path) if self._app_state and getattr(self._app_state, "sys_tool_proposals_path", None) else sys_governance_store_path()
                disk_items = load_sys_governance_proposals(p_path)
                if proposal_id in disk_items:
                    prop = disk_items[proposal_id]
                    dict_store[proposal_id] = prop
            if not prop:
                raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
            if prop.get("revision") != expected_revision:
                raise GovernanceConflictError(
                    f"update_handoff CAS failed for proposal {proposal_id}: "
                    f"expected revision={expected_revision}, actual revision={prop.get('revision')}"
                )
            prop["revision"] = expected_revision + 1
            prop["handoff"] = handoff
            if invocation is not None:
                prop["invocation"] = invocation
            prop["updated_at"] = now
            p_path = Path(self._app_state.sys_tool_proposals_path) if self._app_state and getattr(self._app_state, "sys_tool_proposals_path", None) else sys_governance_store_path()
            persist_sys_governance_proposals(dict_store, p_path)
            if event_type:
                self._in_memory_events.append({
                    "event_id": f"evt-{uuid4().hex[:12]}",
                    "proposal_id": proposal_id,
                    "proposal_revision": prop["revision"],
                    "event_type": event_type,
                    "decision": prop.get("decision"),
                    "state": prop.get("state"),
                    "actor_id": actor_id,
                    "traceability": event_details or {},
                    "created_at": now,
                })
            return dict(prop)

        def _sync_update_handoff(conn):
            with conn.cursor() as cur:
                set_clauses = ["revision = revision + 1", "handoff = %s", "updated_at = %s"]
                params: list[Any] = [json.dumps(_redact_handoff_for_storage(handoff)), now]
                if invocation is not None:
                    set_clauses.append("invocation = %s")
                    params.append(json.dumps(redact_and_bound_payload(invocation)))
                params.extend([proposal_id, expected_revision])
                cur.execute(
                    f"""
                    UPDATE governance_proposals
                    SET {', '.join(set_clauses)}
                    WHERE proposal_id = %s AND revision = %s
                    RETURNING proposal_id, revision, decision, state, handoff, updated_at, invocation;
                    """,
                    tuple(params),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute("SELECT revision FROM governance_proposals WHERE proposal_id = %s;", (proposal_id,))
                    existing = cur.fetchone()
                    if not existing:
                        raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
                    raise GovernanceConflictError(
                        f"update_handoff CAS failed for proposal {proposal_id}: "
                        f"expected revision={expected_revision}, actual revision={existing[0]}"
                    )
                if event_type:
                    cur.execute(
                        """
                        INSERT INTO governance_events (
                            event_id, proposal_id, operation_id, proposal_revision, event_type, decision, state, actor_id, traceability, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                        """,
                        (
                            f"evt-{uuid4().hex[:12]}",
                            proposal_id,
                            None,
                            row[1],
                            event_type,
                            row[2],
                            row[3],
                            actor_id,
                            json.dumps(redact_and_bound_payload(event_details or {})),
                            now,
                        ),
                    )
                return {
                    "proposal_id": row[0],
                    "revision": row[1],
                    "decision": row[2],
                    "state": row[3],
                    "handoff": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                    "updated_at": row[5].isoformat() if hasattr(row[5], "isoformat") else row[5],
                    "invocation": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
                }

        return await asyncio.to_thread(self._run_in_connection, _sync_update_handoff)

    async def claim_operation(
        self,
        proposal_id: str,
        operation_type: str,  # 'invoke', 'apply', 'rollback'
        idempotency_key: str,
        actor_id: str,
        target_state: str,    # e.g., 'invoking', 'applying', 'rolling_back'
        details: Optional[dict[str, Any]] = None,
        expected_proposal_state: Optional[str] = None,
    ) -> Tuple[dict[str, Any], dict[str, Any]]:
        """Atomically claim an external side-effect operation with DB-level idempotency constraint.

        When expected_proposal_state is given, the claim additionally requires the
        proposal's current state to match it (checked under the same row lock /
        in-memory check as the decision check below), so e.g. a second concurrent
        "apply" claim on a proposal that has already moved past "decided" is
        rejected with GovernanceConflictError instead of racing a second
        governance_operations row into existence via a different idempotency key.
        """
        now = datetime.now(UTC).isoformat()
        operation_id = f"op-{uuid4().hex[:12]}"

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            prop = dict_store.get(proposal_id)
            if not prop:
                raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")

            # Idempotent retry: if this exact (proposal_id, operation_type,
            # idempotency_key) was already claimed, return it as-is *before*
            # checking decision/expected_proposal_state -- the first successful
            # claim already advanced the proposal past expected_proposal_state,
            # so a retry of the same request must not be rejected as a conflict
            # just because the state moved on.
            existing_op = next(
                (
                    op for op in self._in_memory_operations.values()
                    if op["proposal_id"] == proposal_id
                    and op["operation_type"] == operation_type
                    and op["idempotency_key"] == idempotency_key
                ),
                None,
            )
            if existing_op is not None:
                return (
                    dict(prop),
                    {"operation_id": existing_op["operation_id"], "state": existing_op["state"], "idempotency_key": idempotency_key, "reused": True},
                )

            if prop.get("decision") != "approved":
                raise GovernanceConflictError(f"Proposal {proposal_id} is not approved (decision={prop.get('decision')})")
            if expected_proposal_state is not None and prop.get("state") != expected_proposal_state:
                raise GovernanceConflictError(
                    f"Proposal {proposal_id} is not in expected state '{expected_proposal_state}' "
                    f"(actual state={prop.get('state')})"
                )

            prop["revision"] = prop.get("revision", 1) + 1
            prop["state"] = target_state
            prop["updated_at"] = now

            self._in_memory_operations[operation_id] = {
                "operation_id": operation_id,
                "proposal_id": proposal_id,
                "operation_type": operation_type,
                "state": "started",
                "idempotency_key": idempotency_key,
                "actor_id": actor_id,
                "details": details or {},
                "created_at": now,
                "updated_at": now,
            }
            self._in_memory_events.append({
                "event_id": f"evt-{uuid4().hex[:12]}",
                "proposal_id": proposal_id,
                "operation_id": operation_id,
                "proposal_revision": prop["revision"],
                "event_type": f"governance_{operation_type}_attempted",
                "decision": prop.get("decision", "approved"),
                "state": target_state,
                "actor_id": actor_id,
                "created_at": now,
            })
            return (
                dict(prop),
                {"operation_id": operation_id, "state": "started", "idempotency_key": idempotency_key, "reused": False},
            )

        def _sync_claim(conn):
            with conn.cursor() as cur:
                # 0. Idempotent retry check, ahead of the state guard below: if
                # this exact (proposal_id, operation_type, idempotency_key) was
                # already claimed, return it regardless of the proposal's
                # current state -- a retry of an already-succeeded claim must
                # not be rejected just because the first claim already moved
                # the proposal past expected_proposal_state. A residual race
                # between this check and the INSERT below is still handled by
                # the UNIQUE-constraint catch further down.
                cur.execute(
                    """
                    SELECT operation_id, state, created_at FROM governance_operations
                    WHERE proposal_id = %s AND operation_type = %s AND idempotency_key = %s;
                    """,
                    (proposal_id, operation_type, idempotency_key),
                )
                pre_existing_op = cur.fetchone()
                if pre_existing_op:
                    cur.execute(
                        "SELECT revision, decision, state FROM governance_proposals WHERE proposal_id = %s;",
                        (proposal_id,),
                    )
                    prop_row = cur.fetchone()
                    if not prop_row:
                        raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
                    rev0, dec0, state0 = prop_row
                    return (
                        {"proposal_id": proposal_id, "revision": rev0, "decision": dec0, "state": state0},
                        {"operation_id": pre_existing_op[0], "state": pre_existing_op[1], "idempotency_key": idempotency_key, "reused": True},
                    )

                # 1. Fetch current proposal (fresh claim path). FOR UPDATE
                # serializes concurrent claims on this proposal_id: a second
                # request racing in with the exact same idempotency_key blocks
                # here until the first commits.
                cur.execute(
                    "SELECT revision, decision, state FROM governance_proposals WHERE proposal_id = %s FOR UPDATE;",
                    (proposal_id,),
                )
                prop_row = cur.fetchone()
                if not prop_row:
                    raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")

                rev, dec, curr_state = prop_row

                # 1b. Re-check idempotency now that the row lock is held: the
                # step-0 check above ran *before* blocking on FOR UPDATE, so a
                # concurrent transaction using the exact same idempotency_key
                # may have inserted its operation and committed (advancing
                # curr_state past expected_proposal_state) while this
                # transaction was waiting for the lock. Without this re-check,
                # such a genuinely concurrent duplicate request would
                # incorrectly hit the expected_proposal_state guard below
                # instead of being recognized as a retry.
                cur.execute(
                    """
                    SELECT operation_id, state, created_at FROM governance_operations
                    WHERE proposal_id = %s AND operation_type = %s AND idempotency_key = %s;
                    """,
                    (proposal_id, operation_type, idempotency_key),
                )
                locked_existing_op = cur.fetchone()
                if locked_existing_op:
                    return (
                        {"proposal_id": proposal_id, "revision": rev, "decision": dec, "state": curr_state},
                        {"operation_id": locked_existing_op[0], "state": locked_existing_op[1], "idempotency_key": idempotency_key, "reused": True},
                    )

                if dec != "approved":
                    raise GovernanceConflictError(f"Proposal {proposal_id} is not approved (decision={dec})")
                if expected_proposal_state is not None and curr_state != expected_proposal_state:
                    raise GovernanceConflictError(
                        f"Proposal {proposal_id} is not in expected state '{expected_proposal_state}' "
                        f"(actual state={curr_state})"
                    )

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
                        f"governance_{operation_type}_attempted",
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
        """Complete or fail a claimed operation and transition coupled proposal state.

        The operation update is CAS-guarded: it only applies when the operation
        both belongs to the given proposal_id and is still in a non-terminal
        state, so a mismatched or already-terminal operation_id is rejected
        with GovernanceConflictError instead of silently mutating proposal
        state a second time.
        """
        now = datetime.now(UTC).isoformat()

        if self._pool is None:
            dict_store = self._get_proposals_dict()
            prop = dict_store.get(proposal_id)
            if not prop:
                raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")

            existing_op = self._in_memory_operations.get(operation_id)
            if existing_op is None:
                raise GovernanceNotFoundError(f"Unknown operation ID: {operation_id}")
            if existing_op["proposal_id"] != proposal_id or existing_op["state"] in {"completed", "failed", "outcome_unknown"}:
                raise GovernanceConflictError(
                    f"Operation {operation_id} cannot be completed: expected proposal_id={proposal_id} "
                    f"and a non-terminal state, actual proposal_id={existing_op['proposal_id']}, state={existing_op['state']}"
                )
            existing_op["state"] = final_op_state
            existing_op["details"] = details or existing_op.get("details") or {}
            existing_op["updated_at"] = now

            prop["revision"] = prop.get("revision", 1) + 1
            prop["state"] = final_proposal_state
            prop["updated_at"] = now

            op_type = existing_op.get("operation_type", "operation")
            self._in_memory_events.append({
                "event_id": f"evt-{uuid4().hex[:12]}",
                "proposal_id": proposal_id,
                "operation_id": operation_id,
                "proposal_revision": prop["revision"],
                "event_type": f"governance_{op_type}_completed" if final_op_state == "completed" else f"governance_{op_type}_failed",
                "decision": prop.get("decision", "approved"),
                "state": final_proposal_state,
                "actor_id": actor_id,
                "created_at": now,
            })
            return (dict(prop), {"operation_id": operation_id, "state": final_op_state})

        def _sync_complete(conn):
            with conn.cursor() as cur:
                # CAS-guard the operation update: must match proposal_id and
                # still be non-terminal, otherwise reject rather than silently
                # completing a foreign or already-finished operation.
                cur.execute(
                    """
                    UPDATE governance_operations
                    SET state = %s, updated_at = %s, details = %s
                    WHERE operation_id = %s AND proposal_id = %s
                      AND state NOT IN ('completed', 'failed', 'outcome_unknown')
                    RETURNING operation_id, proposal_id, operation_type;
                    """,
                    (final_op_state, now, json.dumps(redact_and_bound_payload(details or {})), operation_id, proposal_id),
                )
                op_row = cur.fetchone()
                if not op_row:
                    cur.execute(
                        "SELECT operation_id, proposal_id, state FROM governance_operations WHERE operation_id = %s;",
                        (operation_id,),
                    )
                    existing = cur.fetchone()
                    if not existing:
                        raise GovernanceNotFoundError(f"Unknown operation ID: {operation_id}")
                    raise GovernanceConflictError(
                        f"Operation {operation_id} cannot be completed: expected proposal_id={proposal_id} "
                        f"and a non-terminal state, actual proposal_id={existing[1]}, state={existing[2]}"
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
                op_type = op_row[2] or "operation"

                # Insert terminal governance event
                event_id = f"evt-{uuid4().hex[:12]}"
                event_type = f"governance_{op_type}_completed" if final_op_state == "completed" else f"governance_{op_type}_failed"
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

    async def get_latest_operation(self, proposal_id: str, operation_type: str) -> Optional[dict[str, Any]]:
        """Fetch the most recent governance_operations row for (proposal_id, operation_type).

        Used to retrieve operation-scoped bookkeeping (e.g. a captured rollback
        snapshot reference stored in an "apply" operation's details) without a
        proposal-level JSONB column for it.
        """
        if self._pool is None:
            candidates = [
                op for op in self._in_memory_operations.values()
                if op["proposal_id"] == proposal_id and op["operation_type"] == operation_type
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda op: str(op.get("created_at") or ""), reverse=True)
            return dict(candidates[0])

        def _sync_get_latest(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operation_id, proposal_id, operation_type, state, idempotency_key, actor_id, details, created_at, updated_at
                    FROM governance_operations
                    WHERE proposal_id = %s AND operation_type = %s
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (proposal_id, operation_type),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "operation_id": row[0],
                    "proposal_id": row[1],
                    "operation_type": row[2],
                    "state": row[3],
                    "idempotency_key": row[4],
                    "actor_id": row[5],
                    "details": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
                    "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
                    "updated_at": row[8].isoformat() if hasattr(row[8], "isoformat") else row[8],
                }

        return await asyncio.to_thread(self._run_in_connection, _sync_get_latest)

    async def list_events(self, proposal_id: Optional[str] = None, limit: Optional[int] = 100) -> List[dict[str, Any]]:
        """List immutable governance_events rows, optionally filtered to one proposal.
        limit=None means no SQL LIMIT (full scan)."""
        if self._pool is None:
            # Reverse append order rather than sorting by the string
            # created_at field: events from separate claim/complete calls
            # can end up with an identical (especially on Windows' coarser
            # wall-clock resolution) timestamp string, and a stable
            # sort(reverse=True) on ties doesn't reliably reproduce the
            # semantically-correct newest-first order across calls the way
            # trusting insertion order does. Mirrors the same fix already
            # applied to the legacy JSONL event reader.
            events = list(reversed(self._in_memory_events))
            if proposal_id:
                events = [e for e in events if e.get("proposal_id") == proposal_id]
            return [dict(e) for e in events[:limit]]

        def _sync_list_events(conn):
            with conn.cursor() as cur:
                where_clause = "WHERE proposal_id = %s" if proposal_id else ""
                limit_clause = "LIMIT %s" if limit is not None else ""
                params: list[Any] = [proposal_id] if proposal_id else []
                if limit is not None:
                    params.append(limit)
                cur.execute(
                    f"""
                    SELECT event_id, proposal_id, operation_id, proposal_revision, event_type,
                           decision, state, actor_id, traceability, created_at
                    FROM governance_events {where_clause}
                    ORDER BY created_at DESC {limit_clause};
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
                return [
                    {
                        "event_id": row[0],
                        "proposal_id": row[1],
                        "operation_id": row[2],
                        "proposal_revision": row[3],
                        "event_type": row[4],
                        "decision": row[5],
                        "state": row[6],
                        "actor_id": row[7],
                        "traceability": row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}"),
                        "timestamp": row[9].isoformat() if hasattr(row[9], "isoformat") else row[9],
                    }
                    for row in rows
                ]

        return await asyncio.to_thread(self._run_in_connection, _sync_list_events)

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
