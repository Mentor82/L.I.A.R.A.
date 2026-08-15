-- Migration 004: dedicated handoff_key column + partial unique index for
-- idempotent checkpoint-proposal creation.
--
-- GovernanceService.create_checkpoint_proposal() (workspace-agent checkpoints)
-- must not create two proposals for the same handoff_key under genuine
-- concurrency. A check-then-insert against the JSONB handoff column cannot
-- guarantee that; a real UNIQUE index does, mirroring the same
-- UNIQUE(proposal_id, operation_type, idempotency_key) pattern already used
-- by governance_operations for claim_operation's idempotency.
--
-- Scoped to decision = 'pending': once a proposal is decided, its
-- handoff_key is free to be reused by a later, unrelated checkpoint (matches
-- the pre-existing dedup semantics, which only ever matched against pending
-- proposals). NULL handoff_key values (every plain HTTP-submitted proposal
-- via create_proposal(), which never sets handoff) are excluded entirely.

ALTER TABLE governance_proposals ADD COLUMN IF NOT EXISTS handoff_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_proposals_pending_handoff_key
    ON governance_proposals (handoff_key)
    WHERE decision = 'pending' AND handoff_key IS NOT NULL;
