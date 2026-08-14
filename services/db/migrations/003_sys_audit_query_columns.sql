-- PostgreSQL Schema Migration (Issue #3, Phase 3)
-- Hybrid schema for sys_audit_events: adds columns for the fields actually used
-- as SQL query predicates or aggregation dimensions by the /admin/sys-audit/*
-- endpoints (risk_level, command_family) and by summarize_entries/
-- find_suspicious_entries (command, is_write, is_network, duration_ms), so
-- filtering by risk/family doesn't require an unindexed JSONB scan at scale.
-- The full logical SysAuditEntry structure still lands in metadata JSONB
-- (sanitized via redact_and_bound_payload) regardless -- these columns are a
-- read-path optimization, not a second copy of unsanitized data.
--
-- blocked_only/source filtering intentionally stays Python-side (existing
-- filter_entries()) over the rows these columns narrow down, rather than
-- adding policy_decision/source as further indexed columns.

ALTER TABLE sys_audit_events ADD COLUMN IF NOT EXISTS command TEXT;
ALTER TABLE sys_audit_events ADD COLUMN IF NOT EXISTS command_family TEXT;
ALTER TABLE sys_audit_events ADD COLUMN IF NOT EXISTS risk_level TEXT;
ALTER TABLE sys_audit_events ADD COLUMN IF NOT EXISTS is_write BOOLEAN;
ALTER TABLE sys_audit_events ADD COLUMN IF NOT EXISTS is_network BOOLEAN;
ALTER TABLE sys_audit_events ADD COLUMN IF NOT EXISTS duration_ms DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_sys_audit_events_risk
    ON sys_audit_events(risk_level, command_family, timestamp DESC);
