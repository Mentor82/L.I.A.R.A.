# TOOLING-RULES

Rules for defining, documenting, and using tools in this repo.

## Naming
- Follow the tool naming policy in `ai/POLICY/AI-POLICY.md`.
- Names are stable identifiers, not descriptions.

## Contracts
- Every tool must have a documented input/output schema.
- Error conditions must be explicit and predictable.
- Prefer backward-compatible changes.

## Safety and permissions
- Do not design tools that bypass repo policies.
- Respect sandbox and network constraints.
- Require explicit user approval for sensitive actions.

## Documentation
- Register tools in `ai/TOOLS/tools.yaml`.
- Keep `ai/TOOLS/registry.md` in sync with actual tools.
- Update `ai/TOOLS/contracts/` when behavior changes.
