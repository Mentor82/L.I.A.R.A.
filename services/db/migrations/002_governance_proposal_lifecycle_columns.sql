-- PostgreSQL Schema Migration (Issue #3, Phase 2 schema half)
-- Adds proposal-lifecycle columns that GovernanceService already builds into every
-- proposal dict (handoff/transaction/invocation/invocation_digest/max_invocations)
-- but that 001's save_proposal INSERT never persisted -- silently dropped on every
-- restart against a real PostgreSQL pool. Landed ahead of the apply/rollback CAS
-- rewrite (Phase 1) since manual apply/rollback need to read/write invocation and
-- transaction state, and decide's checkpoint auto-apply flow needs handoff.

ALTER TABLE governance_proposals ADD COLUMN IF NOT EXISTS handoff JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE governance_proposals ADD COLUMN IF NOT EXISTS transaction JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE governance_proposals ADD COLUMN IF NOT EXISTS invocation JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE governance_proposals ADD COLUMN IF NOT EXISTS invocation_digest TEXT;
ALTER TABLE governance_proposals ADD COLUMN IF NOT EXISTS max_invocations INT NOT NULL DEFAULT 1;
