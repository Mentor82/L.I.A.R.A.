# Audit Source Of Truth

## Purpose

This document defines which files are authoritative for code and audit reviews in LIARA.

## Authoritative Code Paths

Use these paths as primary source for code-level audit findings:

- services/**
- workers/**
- shared/**
- tests/**
- scripts/**
- docs/**
- frontend/** source files, excluding generated `dist`/build trees
- src/emeddingserver/** source files, excluding native build outputs and vendored binaries

## Non-Authoritative Paths

The following paths are generated artifacts or packaging outputs and must not be treated as source-of-truth for code findings:

- build/**
- build/lib/**
- frontend/**/dist/**
- liara.egg-info/**
- artifacts/**
- backups/**
- logs/**
- **/__pycache__/**
- src/llama.cpp/** (vendored upstream source; relevant to native builds, not LIARA application logic)

Notes:
- These paths may contain stale copies of source files.
- They are useful for runtime/package debugging, but not for primary code-review citations.

## Audit Citation Rule

When writing findings:

1. Cite source files from authoritative paths.
2. Only cite generated paths when explicitly documenting packaging/runtime behavior.
3. If both exist, prefer source path and add generated path only as secondary evidence.

## Review Workflow (Recommended)

1. Collect evidence from logs/audits and logs/services.
2. Map findings to authoritative source files under services/workers/shared/tests.
3. Validate behavior with tests/tasks.
4. Record summary in audit reports with source-path citations first.

## Related Runbook

- Use docs/AUDIT_RUN_CHECKLIST.md as the standard run template for baseline + post-fix audits.
