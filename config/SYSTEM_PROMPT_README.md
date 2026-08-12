# LIARA System Prompt

## Overview

The system prompt (`system_promt.yaml`) is the **canonical runtime instructions** for LIARA's AI orchestrator. It is loaded on every LLM inference call and shapes how LIARA reasons, validates, governs, and executes.

## How It Works

```
QueryPlanner._load_system_content_block()
    ↓
Reads config/system_promt.yaml
    ↓
Injects into prompt template via [SYSTEM_CONTENT] marker
    ↓
Sent to LLM on every inference call
    ↓
LLM's behavior shaped by these instructions
```

**Code Path:** 
- Load: `services/orchestrator/planner.py:92` (`_DEFAULT_SYSTEM_PROMPT_PATH`)
- Inject: `services/orchestrator/planner.py:145-160` (prompt building)
- Usage: Every call to `Orchestrator.execute()`

## Key Sections

### 1. **IDENTITY & CORE_PRINCIPLES**
- LIARA is a structured, deterministic orchestrator (not a general LLM)
- Decisions based on context + evaluation, not guesswork
- Safety & traceability > speed
- Results must be reproducible

### 2. **SELF_VALIDATION** (New 2026-07-13)
- Docker-based validator can audit own code
- Async job execution with polling
- Mock mode (dev) vs Worker mode (prod)
- Validation scopes: quick, validate, python, security, all

### 3. **GOVERNANCE** (New 2026-07-13)
- Command-specific whitelist/greylist/blacklist validation precedes governance
- Policy-validated read-only HTTP(S) retrieval via `curl` is a normal SYS capability
- Mutations and other sensitive approvable actions use the Proposal-Decision flow
- Blacklist denials cannot be approved
- Immutable decisions, append-only audit trail
- Rules: never bypass gates, document risk

### 4. **MEMORY_LIFECYCLE** (New 2026-07-13)
- Fact states: draft → staged → verified → deprecated/revoked/deleted
- Facts-First semantics: verified=ground-truth, staged=hints
- Verification gate: proposed_status=verified requires human approval
- Immutability: verified facts cannot revert to mutable states

### 5. **ASYNC_EXECUTION** (New 2026-07-13)
- Job lifecycle: queued → running → completed|failed
- Polling-based (no blocking)
- Timeout handling: 1800s default

### 6. **AUDIT_TRAIL** (New 2026-07-13)
- Append-only JSONL design
- Multiple audit streams: sys_audit, sys_governance_events
- Query by session_id for conversation-level audit

### 7. **ENVIRONMENT**
- Workspace: restricted Debian computer owned by the `liara` user
- Runtime capabilities: policy-controlled SYS plus memory context
- External retrieval is implemented through read-only SYS/`curl`; there is no independent `SEARCH` tool
- Session model: required fields & rules

### 8. **ROUTER, JUDGE, VALIDATOR, MEMORY_GATE**
- Decision logic (utility maximization)
- Security & policy control (pre-action)
- Result verification (post-action)
- Storage decisions (not everything stored)

### 9. **REASONING_POLICY**
- THINKING: internal planning (disclosure forbidden)
- REASONING: visible explanation (allowed if abstracted)

## When to Update

Update `system_promt.yaml` when:

1. **New capabilities implemented** (e.g., new validator scopes, new governance tokens)
2. **Behavioral changes** (e.g., new routing strategy, new audit event types)
3. **Policy changes** (e.g., new blocked commands, new verification rules)
4. **Documentation clarification** (e.g., better examples, clearer rules)

**DO NOT update casually** — this is the source of truth for LIARA's runtime behavior.

## Testing the System Prompt

```bash
# Verify it loads correctly
python -c "
from services.orchestrator.planner import QueryPlanner
p = QueryPlanner()
print('System content block length:', len(p._system_content_block))
print('Loaded:', p._system_content_block[:100] + '...')
"

# Run inference with explicit session
python -m services.cli.main chat "Validiere meinen Code"  # Should use system prompt

# Check what prompt was used
grep -A 20 "system_content_loaded" logs/services/orchestrator/latency_scope.jsonl | tail -1
```

## Integration Points

| Service | File | How It Uses |
|---------|------|-------------|
| **Orchestrator** | `services/orchestrator/planner.py` | Loads, injects into prompt |
| **LLM** | `services/inference/gateway.py` | Receives in inference request |
| **Validator** | `services/orchestrator/validator.py` | Knows about validation scopes |
| **Memory** | `services/memory/store.py` | Knows about fact lifecycle |
| **Judge** | `services/judge/engine.py` | Knows about governance rules |
| **CLI** | `services/cli/main.py` | All chat runs use system prompt |
| **API** | `services/api/app.py` | All `/chat` calls use system prompt |

## Format Notes

- **YAML structure** (not JSON, not Markdown)
- **Indentation critical** (2-space, consistent)
- **Strings can use `>`** for multi-line (folded) or `|` (literal)
- **Comments prefixed with `#`** (not supported in all YAML contexts)

## Deployment

The system prompt is:
- ✅ Part of the repository
- ✅ Loaded at runtime (not hardcoded)
- ✅ Configurable via `_DEFAULT_SYSTEM_PROMPT_PATH`
- ⚠️ NOT environment-variable configurable (by design — stability)

To use a custom system prompt:
```python
# In planner.py, override _DEFAULT_SYSTEM_PROMPT_PATH
custom_path = Path("/path/to/custom_promt.yaml")
# Then rebuild: docker compose --profile app up -d --build
```

## Related Files

- **`.instructions.md`** — AI agent instructions (for developers in VS Code)
- **`VALIDATOR_SETUP.md`** — Validator deployment guide
- **`LIARA_SELF_TEST_RESULTS.md`** — Test results & validation proof
- **`.env.example`** — Environment variables reference

---

**Last Updated:** 2026-07-13  
**Format Version:** YAML 1.1  
**Load Path:** `config/system_promt.yaml`  
**Injection Point:** `services/orchestrator/planner.py:145`
