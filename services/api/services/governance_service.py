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

    async def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Get proposal or raise GovernanceNotFoundError."""
        proposal = await self.repo.get_proposal(proposal_id)
        if not proposal:
            raise GovernanceNotFoundError(f"Unknown proposal ID: {proposal_id}")
        return proposal

    async def list_proposals(self, decision: str = "all", limit: int = 50) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """Atomically transition a proposal decision using multi-attribute CAS."""
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

    async def list_events(self, proposal_id: Optional[str] = None, limit: int = 100) -> List[dict[str, Any]]:
        """List immutable governance_events rows, optionally filtered to one proposal."""
        return await self.repo.list_events(proposal_id=proposal_id, limit=limit)
