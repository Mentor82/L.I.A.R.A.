# SECURITY

This policy defines security expectations for AI-assisted work.

## Scope
- Applies to Codex and Copilot within this repository.
- Complements `ai/POLICY/SAFETY.md` and `ai/POLICY/DATA-GOVERNANCE.md`.

## Secure development rules
- Follow least privilege and minimize blast radius.
- Avoid introducing insecure defaults or weak crypto.
- Do not add secret material to code, docs, or tests.
- Prefer explicit configuration over hidden behavior.

## Dependency and supply chain
- Do not add new dependencies without clear justification.
- Prefer pinned versions and minimal dependency sets.
- Flag known vulnerabilities when detected.

## Authentication and access
- Never log credentials, tokens, or secrets.
- Use placeholders in examples (e.g., `YOUR_API_KEY`).
- Ensure access controls are enforced server-side.

## Review and verification
- Call out security-sensitive changes explicitly.
- Suggest tests or checks for auth, validation, and permissions.
- If unsure about impact, request clarification.
