# AGENTS

## Shell Preference

For this workspace, prefer `WSL` with POSIX `sh`/`bash` for:
- live checks
- SSE/HTTP stream tests
- `curl`-based diagnostics
- log parsing
- smoke/integration scripts
- general system-level automation

Use `PowerShell` only for:
- Windows-specific process control
- GUI launchers
- Windows-only path or service tasks

## Script Preference

Prefer these scripts when applicable:
- `scripts/live_chat_memory_demo.sh`
- `scripts/run_live_chat_memory_checks.sh`

Only fall back to:
- `scripts/live_chat_memory_demo.ps1`
- `scripts/run_live_chat_memory_checks.ps1`

if WSL is unavailable or the task is explicitly Windows-specific.

## Path Note

When running in WSL, use the repo via the WSL-mounted workspace path rather than
rewriting the flow back into PowerShell.

## Goal

Default to the simplest reproducible Linux-style workflow for diagnostics and
automation in this repository.
