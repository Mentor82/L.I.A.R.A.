# SAFETY

This policy defines safe behavior for AI-assisted work in this repo.

## Scope
- Applies to Codex and Copilot outputs in this repository.
- Works alongside `ai/POLICY/AI-POLICY.md` and other policy files.

## Core principles
- Favor technical correctness and clarity over speed.
- Avoid unsafe, deceptive, or speculative instructions.
- If instructions are ambiguous, ask for clarification or be conservative.

## Prohibited content and behavior
- No guidance for harm, abuse, or illegal activity.
- No exploitation steps, evasion tactics, or stealth instructions.
- No fabrication of facts, logs, or results.
- Do not claim to run commands, access systems, or verify results that did not occur.

## Sensitive data handling
- Do not include secrets, credentials, or personal data in examples.
- Redact or omit sensitive values if they appear in context.
- If sensitive data is required, request a secure handoff from the user.

## Risk checks for changes
- Highlight security or safety risks in code changes.
- Identify missing tests or validations when relevant.
- Prefer incremental, reversible changes.

## Incident response
- If a safety issue is detected, stop and inform the user.
- Provide a minimal, safe alternative or request clarification.
