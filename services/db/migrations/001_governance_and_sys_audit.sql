-- PostgreSQL Schema Migration for Governance & Sys-Audit (Issue #3)
-- Defines canonical tables for proposal state machine, operation claims, idempotency, and operational audit events.

-- 1. Governance Proposals Table
CREATE TABLE IF NOT EXISTS governance_proposals (
    proposal_id TEXT PRIMARY KEY,
    revision INT NOT NULL DEFAULT 1,
    decision TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
    state TEXT NOT NULL DEFAULT 'created',    -- 'created', 'decided', 'invoking', 'invoked', 'applying', 'applied', 'rolling_back', 'rolled_back', 'failed', 'outcome_unknown'
    tool_name TEXT NOT NULL DEFAULT 'sys',
    command TEXT NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_check JSONB NOT NULL DEFAULT '{}'::jsonb,
    capability TEXT,
    rationale TEXT,
    requested_by TEXT,
    decided_by TEXT,
    decision_reason TEXT,
    decision_at TIMESTAMPTZ,
    traceability JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_governance_proposals_decision_state 
    ON governance_proposals(decision, state, created_at DESC);

-- 2. Governance Operations Table (Claimed Side-Effect Operations)
CREATE TABLE IF NOT EXISTS governance_operations (
    operation_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES governance_proposals(proposal_id) ON DELETE CASCADE,
    operation_type TEXT NOT NULL,             -- 'invoke', 'apply', 'rollback'
    state TEXT NOT NULL DEFAULT 'started',    -- 'started', 'completed', 'failed', 'outcome_unknown'
    idempotency_key TEXT NOT NULL,
    actor_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_operation_idempotency UNIQUE (proposal_id, operation_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_governance_operations_proposal 
    ON governance_operations(proposal_id, created_at DESC);

-- 3. Governance Events Table (Immutable Audit Log of State Changes)
CREATE TABLE IF NOT EXISTS governance_events (
    event_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES governance_proposals(proposal_id) ON DELETE CASCADE,
    operation_id TEXT REFERENCES governance_operations(operation_id) ON DELETE SET NULL,
    proposal_revision INT NOT NULL,
    event_type TEXT NOT NULL,                 -- 'proposal_created', 'proposal_decided', 'operation_started', 'operation_completed', 'operation_failed'
    decision TEXT,
    state TEXT,
    actor_id TEXT,
    traceability JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_governance_event_revision UNIQUE (proposal_id, proposal_revision, event_type)
);

CREATE INDEX IF NOT EXISTS idx_governance_events_proposal 
    ON governance_events(proposal_id, created_at DESC);

-- 4. Sys-Audit Events Table (2-Phase Operational Lifecycle Audit Log)
CREATE TABLE IF NOT EXISTS sys_audit_events (
    event_id TEXT PRIMARY KEY,
    operation_id TEXT,
    proposal_id TEXT,
    request_id TEXT,
    session_id TEXT,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    lifecycle_stage TEXT NOT NULL,            -- 'started', 'completed', 'failed', 'outcome_unknown'
    outcome TEXT,
    actor_id TEXT,
    context TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sys_audit_events_traceability 
    ON sys_audit_events(request_id, session_id, run_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_sys_audit_events_proposal_op 
    ON sys_audit_events(proposal_id, operation_id);

-- 5. System Metadata Table (Cutover & System Flags)
CREATE TABLE IF NOT EXISTS system_metadata (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
