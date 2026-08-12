# RAG-RULES

Rules for retrieval-augmented generation and document grounding.

## Source selection
- Prefer `docs/*.md` as the primary grounding source.
- Use repo-local sources unless the user explicitly provides others.
- If sources conflict, favor the most specific and most recent.

## Grounding and citations
- Keep outputs grounded in retrieved sources.
- Do not invent facts beyond available documents.
- When unsure, ask for clarification or cite uncertainty.

## Context hygiene
- Retrieve only what is needed for the task.
- Avoid large, irrelevant context dumps.
- Summarize long sources instead of copying verbatim.

## Sensitive data
- Do not surface sensitive data from retrieval.
- Redact identifiers and secrets when necessary.

## Change impact
- If a generated change depends on retrieved content, mention it.
