# AI Governance Pack (Codex + Copilot)

This folder contains shared instructions, policies, prompts, and workflows
intended to be used consistently by Codex and GitHub Copilot.

## Goals
- Harmonize behavior across Codex and Copilot.
- Keep outputs aligned with project docs and technical accuracy.
- Provide a single source of truth for AI usage in this repo.

## Sources of truth
- Project documentation under `docs/`
- Copilot instructions in `.github/copilot-instructions.md`
- Policies in `ai/POLICY/`
- Agent roles and playbooks in `ai/AGENTS/`
- Tooling contracts in `ai/TOOLS/`
- Prompt templates in `ai/PROMPTS/`

## Shared principles (Codex + Copilot)
- Prefer `docs/*.md` for UI and content generation.
- Be conservative on ambiguity; avoid marketing tone.
- Avoid visual overengineering; favor technical precision.
- Emojis are allowed only as specified in the policy.
- Follow tool naming rules (see `ai/POLICY/AI-POLICY.md`).

## Structure
- `ai/POLICY/` - governance and safety policies
- `ai/AGENTS/` - roles, routing, playbooks
- `ai/TOOLS/` - tool registry and contracts
- `ai/PROMPTS/` - system and template prompts
- `ai/WORKFLOWS/` - editor and workflow settings

## Change process
Edits should:
- Respect existing repo docs and constraints.
- Be reviewed for consistency across Codex and Copilot.
- Update `ai/CHANGELOG.md` for non-trivial changes.
