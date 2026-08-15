"""Governance Application Service.

Pure domain business logic for sys proposal creation, state-machine decision transitions,
operation claims, and audit trail generation. Contains no FastAPI or HTTP code.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from services.api.exceptions import (
    GovernanceConflictError,
    GovernanceError,
    GovernanceNotFoundError,
    PolicyViolationError,
)
from services.api.security import Principal
from services.api.storage.governance_repository import PostgresGovernanceRepository
from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository
from services.tools.governance import (
    classify_sys_governance,
    sys_governance_invocation_digest,
    sys_governance_mode,
)

logger = logging.getLogger("liara.governance.service")


def evaluate_sys_policy(command: str) -> dict[str, Any]:
    """Evaluate command against security policy rules."""
    normalized = (command or "").strip().lower()
    blocked_tokens = ("rm", "del", "remove-item", "shutdown", "reboot", "format")
    network_tokens = ("curl", "invoke-webrequest", "wget")
    mutation_tokens = ("tee", "mkdir", "touch", "cp", "mv", "venv-pip")

    reasons: list[str] = []
    allowed = True
    risk_level = "low"

    if any(token in normalized for token in blocked_tokens):
        allowed = False
        risk_level = "high"
        reasons.append("blocked_command_family")
    elif any(token in normalized for token in mutation_tokens):
        risk_level = "high"
        reasons.append("mutation_requires_review")
    elif any(token in normalized for token in network_tokens):
        risk_level = "medium"
        reasons.append("network_command_requires_review")

    return {
        "allowed": allowed,
        "reasons": reasons,
        "command_name": normalized,
        "risk_level": risk_level,
    }


class GovernanceService:
    """Application Service encapsulating all governance workflows."""

    def __init__(self, repository: PostgresGovernanceRepository, audit_repository: PostgresSysAuditRepository):
        self.repo = repository
        self.audit_repo = audit_repository

    async def create_proposal(
        self,
        command: str,
        parameters: Optional[dict[str, Any]],
        principal: Principal,
        capability: Optional[str] = None,
        rationale: Optional[str] = None,
        max_invocations: int = 1,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        source: str = "api",
        context: str = "api.tools.sys.governance.proposal",
    ) -> dict[str, Any]:
        """Create a new governance proposal after policy evaluation."""
        proposal_id = f"sys-prop-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        policy = evaluate_sys_policy(command)

        req_id = request_id or proposal_id
        r_id = run_id or req_id

        proposal = {
            "proposal_id": proposal_id,
            "revision": 1,
            "tool_name": "sys",
            "command": command,
            "parameters": parameters or {},
            "invocation_digest": sys_governance_invocation_digest(command, parameters or {}),
            "max_invocations": max_invocations,
            "invocation": {"state": "not_invoked", "attempt_count": 0, "success_count": 0},
            "capability": capability,
            "rationale": rationale,
            "requested_by": principal.actor_id,
            "policy_check": policy,
            "decision": "pending",
            "state": "created",
            "decision_reason": None,
            "decided_by": None,
            "created_at": now,
            "updated_at": now,
            "traceability": {
                "request_id": req_id,
                "run_id": r_id,
                "session_id": session_id,
                "source": source,
                "context": context,
            },
        }

        # 1. Save proposal to PostgreSQL
        saved_proposal = await self.repo.save_proposal(proposal)

        # 2. Log pre-execution audit event
        await self.audit_repo.log_event(
            tool_name="sys_governance_proposal",
            lifecycle_stage="started",
            outcome="allow" if policy["allowed"] else "block",
            proposal_id=proposal_id,
            request_id=req_id,
            session_id=session_id,
            run_id=r_id,
            actor_id=principal.actor_id,
            context=context,
            metadata={"command": command, "policy_check": policy},
        )

        return saved_proposal

    async def create_checkpoint_proposal(
        self,
        *,
        command: str,
        parameters: dict[str, Any],
        principal: Principal,
        capability: str,
        rationale: str,
        traceability: dict[str, Any],
        handoff: Optional[dict[str, Any]] = None,
        max_invocations: int = 1,
    ) -> dict[str, Any]:
        """Create a proposal that starts directly in an "awaiting_decision"
        handoff state, e.g. a workspace-agent step checkpoint -- as opposed
        to create_proposal's plain HTTP-submitted flow, which never sets
        handoff at creation time.

        Idempotent on handoff.handoff_key: a second call with the same key
        while a matching proposal is still pending returns that existing
        proposal instead of creating a duplicate. A partial UNIQUE index on
        governance_proposals(handoff_key) WHERE decision='pending' (migration
        004) enforces this atomically even under genuine concurrency --
        mirrors claim_operation's UNIQUE(proposal_id, operation_type,
        idempotency_key) idempotency pattern. The pre-check below is purely
        an optimization to skip proposal construction on the common
        non-conflicting path; save_proposal() raising GovernanceConflictError
        on the actual insert is the real correctness guarantee.
        """
        classification = classify_sys_governance(parameters)
        invocation_digest = sys_governance_invocation_digest(command, parameters)
        normalized_handoff = dict(handoff or {})
        handoff_key = str(normalized_handoff.get("handoff_key") or "").strip()
        if not handoff_key:
            handoff_key = ":".join((
                str(traceability.get("run_id") or traceability.get("request_id") or "unknown"),
                str(normalized_handoff.get("step_id") or "unknown"),
                invocation_digest,
            ))
        normalized_handoff["handoff_key"] = handoff_key

        existing = await self._find_pending_proposal_by_handoff_key(handoff_key)
        if existing is not None:
            return existing

        proposal_id = f"sys-prop-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        policy_check = {
            "allowed": True,
            "risk_level": classification["risk_level"],
            "reasons": list(classification["reasons"]),
        }
        proposal = {
            "proposal_id": proposal_id,
            "revision": 1,
            "decision": "pending",
            "state": "created",
            "tool_name": "sys",
            "command": str(command),
            "parameters": {k: v for k, v in dict(parameters).items() if k != "_governance_authorized"},
            "invocation_digest": invocation_digest,
            "max_invocations": max(1, min(int(max_invocations), 10)),
            "invocation": {"state": "not_invoked", "attempt_count": 0, "success_count": 0},
            "capability": capability,
            "rationale": rationale,
            "requested_by": principal.actor_id,
            "policy_check": policy_check,
            "decision_reason": None,
            "decided_by": None,
            "created_at": now,
            "updated_at": now,
            "traceability": dict(traceability),
            "handoff": normalized_handoff,
        }

        try:
            saved_proposal = await self.repo.save_proposal(proposal)
        except GovernanceConflictError:
            # Lost the race: a concurrent call already won the partial
            # UNIQUE index for this handoff_key between our pre-check above
            # and this insert. Return the winner instead of propagating a
            # conflict for what the caller intends as an idempotent create.
            existing = await self._find_pending_proposal_by_handoff_key(handoff_key)
            if existing is not None:
                return existing
            raise

        await self.audit_repo.log_event(
            tool_name="sys_governance_proposal",
            lifecycle_stage="started",
            outcome="allow",
            proposal_id=proposal_id,
            request_id=traceability.get("request_id"),
            session_id=traceability.get("session_id"),
            run_id=traceability.get("run_id"),
            actor_id=principal.actor_id,
            context=str(traceability.get("context") or "workspace_agent.governance_handoff"),
            metadata={"command": command, "policy_check": policy_check},
        )

        return saved_proposal

    async def _find_pending_proposal_by_handoff_key(self, handoff_key: str) -> Optional[dict[str, Any]]:
        return await self.repo.find_pending_proposal_by_handoff_key(handoff_key)

    async def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Get proposal or raise GovernanceNotFoundError."""
        proposal = await self.repo.get_proposal(proposal_id)
        if not proposal:
            raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
        return proposal

    async def list_proposals(self, decision: str = "all", limit: Optional[int] = 50) -> dict[str, Any]:
        """List proposals with summary metrics."""
        items = await self.repo.list_proposals(decision=decision, limit=limit)
        decision_counts = {"pending": 0, "approved": 0, "rejected": 0}
        for item in items:
            d = str(item.get("decision") or "pending")
            if d in decision_counts:
                decision_counts[d] += 1

        return {
            "status": "success",
            "count": len(items),
            "items": items,
            "summary": {
                "decisions": decision_counts,
                "enforcement_mode": sys_governance_mode(),
            },
        }

    async def decide_proposal(
        self,
        proposal_id: str,
        decision: str,  # 'approved' or 'rejected'
        principal: Principal,
        decision_reason: Optional[str] = None,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        source: str = "api",
        context: str = "api.tools.sys.governance.decision",
        handoff_update: Optional[dict[str, Any]] = None,
        invocation_update: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Atomically transition a proposal decision using multi-attribute CAS.

        handoff_update/invocation_update, when given, are committed in the
        SAME CAS statement as the decision -- the caller (router) computes
        them before calling this, since they may depend on orchestrator/
        workspace-agent state this FastAPI-free service intentionally has no
        access to.
        """
        if decision not in {"approved", "rejected"}:
            raise PolicyViolationError(f"Invalid decision value: {decision}. Must be 'approved' or 'rejected'.")

        proposal = await self.get_proposal(proposal_id)

        current_decision = proposal.get("decision", "pending")
        if current_decision != "pending":
            raise GovernanceConflictError(f"Proposal decision is immutable: {proposal_id} is already '{current_decision}'")

        if decision == "approved" and not bool((proposal.get("policy_check") or {}).get("allowed", True)):
            raise PolicyViolationError(f"Proposal is blocked by policy and cannot be approved: {proposal_id}")

        req_rev = expected_revision if expected_revision is not None else proposal.get("revision", 1)
        current_state = proposal.get("state", "created")
        new_state = "decided"

        # Execute CAS transition in PostgreSQL
        updated = await self.repo.execute_atomic_cas_decision(
            proposal_id=proposal_id,
            expected_revision=req_rev,
            expected_state=current_state,
            expected_decision=current_decision,
            new_decision=decision,
            new_state=new_state,
            decided_by=principal.actor_id,
            decision_reason=decision_reason,
            traceability={
                "request_id": request_id or proposal_id,
                "run_id": run_id or request_id or proposal_id,
                "session_id": session_id,
                "source": source,
                "context": context,
            },
            handoff_update=handoff_update,
            invocation_update=invocation_update,
        )

        # Log audit event for decision
        await self.audit_repo.log_event(
            tool_name="sys_governance_decision",
            lifecycle_stage="completed",
            outcome=decision,
            proposal_id=proposal_id,
            request_id=request_id or proposal_id,
            session_id=session_id,
            run_id=run_id or request_id or proposal_id,
            actor_id=principal.actor_id,
            context=context,
            metadata={"decision": decision, "reason": decision_reason},
            fail_closed=True,
        )

        # Merge fields
        full_proposal = dict(proposal)
        full_proposal.update(updated)
        return full_proposal

    async def recover_stale_operations(self, stale_threshold_seconds: float = 300.0) -> List[dict[str, Any]]:
        """Trigger recovery for operations stuck in 'started' state past threshold."""
        return await self.repo.recover_stale_operations(stale_threshold_seconds)

    async def claim_apply(
        self,
        proposal_id: str,
        principal: Principal,
        idempotency_key: str,
        action_reason: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Exclusively claim an "apply" operation for an approved, not-yet-applied proposal.

        The proposal must be in state "decided" (i.e. approved and not already
        applying/applied/rolling_back/etc). The DB row lock inside
        claim_operation, combined with the expected_proposal_state check,
        serializes concurrent claims across worker processes -- replacing the
        single-process app_state.sys_tool_governance_lock the router used to
        rely on.
        """
        proposal = await self.get_proposal(proposal_id)
        if proposal.get("decision") != "approved":
            raise GovernanceConflictError(f"Sys proposal is not approved: {proposal_id}")
        handoff = proposal.get("handoff") if isinstance(proposal.get("handoff"), dict) else {}
        if isinstance(handoff.get("checkpoint"), dict):
            raise GovernanceConflictError("Workspace checkpoint proposals are applied automatically by their decision")

        claim_details = dict(details or {})
        claim_details["acted_by"] = principal.actor_id
        claim_details["reason"] = action_reason
        return await self.repo.claim_operation(
            proposal_id=proposal_id,
            operation_type="apply",
            idempotency_key=idempotency_key,
            actor_id=principal.actor_id,
            target_state="applying",
            details=claim_details,
            expected_proposal_state="decided",
        )

    async def complete_apply(
        self,
        operation_id: str,
        proposal_id: str,
        principal: Principal,
        success: bool,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Finalize a claimed "apply" operation as applied or apply_failed."""
        proposal, _operation = await self.repo.complete_operation(
            operation_id=operation_id,
            proposal_id=proposal_id,
            final_op_state="completed" if success else "failed",
            final_proposal_state="applied" if success else "apply_failed",
            actor_id=principal.actor_id,
            details=details,
        )
        return proposal

    async def claim_rollback(
        self,
        proposal_id: str,
        principal: Principal,
        idempotency_key: str,
        action_reason: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Exclusively claim a "rollback" operation for an already-applied proposal."""
        claim_details = dict(details or {})
        claim_details["acted_by"] = principal.actor_id
        claim_details["reason"] = action_reason
        return await self.repo.claim_operation(
            proposal_id=proposal_id,
            operation_type="rollback",
            idempotency_key=idempotency_key,
            actor_id=principal.actor_id,
            target_state="rolling_back",
            details=claim_details,
            expected_proposal_state="applied",
        )

    async def complete_rollback(
        self,
        operation_id: str,
        proposal_id: str,
        principal: Principal,
        success: bool,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Finalize a claimed "rollback" operation as rolled_back or rollback_failed."""
        proposal, _operation = await self.repo.complete_operation(
            operation_id=operation_id,
            proposal_id=proposal_id,
            final_op_state="completed" if success else "failed",
            final_proposal_state="rolled_back" if success else "rollback_failed",
            actor_id=principal.actor_id,
            details=details,
        )
        return proposal

    async def get_latest_operation(self, proposal_id: str, operation_type: str) -> Optional[dict[str, Any]]:
        """Fetch the most recent claimed operation of a given type for a proposal."""
        return await self.repo.get_latest_operation(proposal_id, operation_type)

    async def list_events(self, proposal_id: Optional[str] = None, limit: Optional[int] = 100) -> List[dict[str, Any]]:
        """List immutable governance_events rows, optionally filtered to one proposal."""
        return await self.repo.list_events(proposal_id=proposal_id, limit=limit)

    async def update_handoff(
        self,
        proposal_id: str,
        expected_revision: int,
        handoff: dict[str, Any],
        invocation: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """CAS-guarded update of a proposal's handoff (and optionally invocation)
        state, keyed on revision alone.

        Used after an external workspace-agent resume call whose result must
        be persisted using the revision execute_atomic_cas_decision's own
        handoff_update already returned -- see decide_proposal's docstring.
        """
        return await self.repo.update_handoff(proposal_id, expected_revision, handoff, invocation=invocation)

    async def claim_invocation(
        self,
        proposal_id: str,
        principal: Principal,
        request_id: Optional[str],
        run_id: Optional[str],
    ) -> dict[str, Any]:
        """Claim one invocation attempt against an approved sys proposal.

        Unlike apply/rollback (single-use, claim_operation-based), a proposal
        may legally be invoked multiple times up to max_invocations, so this
        is not an exclusivity claim -- it's an optimistic-concurrency CAS
        (reusing update_handoff, revision-keyed) that enforces the
        max_invocations cap and the "not already mid-invocation" guard. A
        concurrent second claim racing on the same revision fails with
        GovernanceConflictError rather than corrupting the counter.

        Returns the updated proposal; its "invocation" field carries the
        freshly-claimed state, and its "revision" is the one
        complete_invocation must be called with.
        """
        proposal = await self.get_proposal(proposal_id)
        if proposal.get("decision") != "approved":
            raise GovernanceConflictError(f"Sys proposal is not approved: {proposal_id}")

        invocation = dict(proposal.get("invocation") or {})
        if invocation.get("state") == "invoking":
            raise GovernanceConflictError(f"Sys proposal invocation already in progress: {proposal_id}")
        attempt_count = int(invocation.get("attempt_count") or 0)
        max_invocations = int(proposal.get("max_invocations") or 1)
        if attempt_count >= max_invocations:
            raise GovernanceConflictError(f"Sys proposal invocation limit reached: {proposal_id}")

        now = datetime.now(UTC).isoformat()
        new_invocation = {
            **invocation,
            "state": "invoking",
            "attempt_count": attempt_count + 1,
            "success_count": int(invocation.get("success_count") or 0),
            "last_attempt_at": now,
            "last_request_id": request_id,
            "last_run_id": run_id,
            "last_actor_id": principal.actor_id,
        }
        return await self.repo.update_handoff(
            proposal_id,
            proposal["revision"],
            dict(proposal.get("handoff") or {}),
            invocation=new_invocation,
            event_type="invocation_attempted",
            actor_id=principal.actor_id,
            event_details={"request_id": request_id, "run_id": run_id},
        )

    async def complete_invocation(
        self,
        proposal_id: str,
        expected_revision: int,
        success: bool,
        status: Optional[str] = None,
        error: Optional[str] = None,
        execution_ms: Optional[float] = None,
        actor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Finalize a claimed invocation attempt as completed or failed.

        expected_revision must be the fresh revision claim_invocation's
        result returned, not the pre-claim one.
        """
        proposal = await self.get_proposal(proposal_id)
        invocation = dict(proposal.get("invocation") or {})
        success_count = int(invocation.get("success_count") or 0) + int(success)
        now = datetime.now(UTC).isoformat()
        new_invocation = {
            **invocation,
            "state": "completed" if success else "failed",
            "success_count": success_count,
            "last_completed_at": now,
            "last_status": status,
            "last_error": error,
            "last_execution_ms": execution_ms,
        }
        return await self.repo.update_handoff(
            proposal_id,
            expected_revision,
            dict(proposal.get("handoff") or {}),
            invocation=new_invocation,
            event_type="invocation_completed" if success else "invocation_failed",
            actor_id=actor_id,
            event_details={"status": status, "error": error},
        )
