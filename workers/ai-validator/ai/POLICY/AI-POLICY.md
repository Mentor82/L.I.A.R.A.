# AI-POLICY (Codex + Copilot)

This policy defines unified behavior for Codex and GitHub Copilot in this repo.

## 1) Scope and priorities
- Source of truth for UI/content: `docs/*.md`.
- When unclear, be conservative and prioritize technical accuracy.
- Avoid marketing language and unnecessary visual complexity.

## 2) Content and UI generation
- Align copy and UI decisions to `docs/*.md`.
- Prefer clarity over stylistic flair.
- "Cortana Interaction Layer" (optional):
  - Icons and emojis allowed.
  - Max 1 emoji per UI element.
  - No emojis in long text blocks.
  - No emojis in legal, logs, or error messages.

## 3) Tool naming policy
When creating or calling tools:
- Tool names MUST be <= 64 characters.
- Allowed characters: [a-zA-Z0-9_].
- No dynamic content in tool names.
- Tool names are identifiers, not descriptions.
- Use concise, clear names (e.g., "get_user_data", "fetch_weather").
- Avoid spaces, special characters, or punctuation.

## 4) Data handling
- Do not introduce or store sensitive data in prompts or artifacts.
- Keep references to real user data out of examples unless explicitly provided.

## 5) Safety and compliance
- Follow repository policies in `ai/POLICY/`.
- If conflicts arise, defer to the more restrictive rule.

## 6) Consistency across agents
- Ensure Codex and Copilot instructions align in tone and constraints.
- If a change affects behavior, reflect it in both policy and prompt layers.
