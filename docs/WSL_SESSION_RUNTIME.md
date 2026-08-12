# Native WSL Session Runtime

## Boundary

LIARA's canonical project remains on Windows:

```text
C:\ai\LIARA
```

Tests, Julia compute, code experiments, and simulations run in temporary native
Debian workspaces:

```text
/home/liara/workspace/sessions/<session-id>/
├── source/       immutable snapshot
├── work/         mutable execution copy
├── artifacts/    generated session artifacts
├── reports/      snapshot and execution reports
└── tmp/          session-local temporary files
```

No session operation writes into `C:\ai\LIARA`. A collection creates an
immutable local candidate below `artifacts/wsl_sessions/`; the existing
`ai-validator` can mount that candidate read-only.

## Lifecycle

```text
canonical Windows source
→ filtered byte snapshot
→ native WSL source + work copy
→ policy-gated /sys commands
→ patch and candidate hashes
→ ai-validator job
→ governance decision
→ explicit session cleanup
```

The first implementation runs session commands as the existing WSL user
`liara`. Isolation is provided by a per-session root and immutable snapshot.
The record contains `execution_user` so OS-user isolation can be added after
Julia is installed in a shared system location; the current Julia installation
under `/home/liara/.juliaup` is intentionally not opened to additional users.

## Direct commands

Inspect the exact filtered snapshot before creating it:

```powershell
python scripts\wsl_session_cli.py plan
```

Create:

```powershell
C:\ai\LIARA\.venv\Scripts\python.exe scripts\wsl_session_cli.py `
  --request-id req-1 --run-id run-1 create --label translator-test
```

Execute through the existing `/sys` policy:

```powershell
python scripts\wsl_session_cli.py exec <session-id> -- ls -la
python scripts\wsl_session_cli.py exec <session-id> -- julia --version
python scripts\wsl_session_cli.py exec <session-id> -- julia simulation.jl
"probe" | python scripts\wsl_session_cli.py exec <session-id> --stdin -- tee /home/liara/workspace/sessions/<session-id>/work/probe.txt
```

Collect and clean up:

```powershell
python scripts\wsl_session_cli.py collect <session-id>
python scripts\wsl_session_cli.py destroy <session-id>
```

### Textual frontend

`frontend/tex-ui` can call the same policy-gated executor directly:

```text
/sys mkdir -p /home/liara/workspace/example
/sys tee /home/liara/workspace/example/main.py
print("hello")
```

For `tee`, the lines after the first newline are transported as structured
`stdin_text`. The frontend can be bound to an existing controlled session with
`--workspace-session-id`; the executor then confines the workdir to that
session's `work` tree.

Mutation success is evidence-based. The executor performs a WSL read-after-
write check and returns target type, size and SHA-256 where applicable. The UI
must not translate a model statement into write success without this evidence.

## Agentic workspace tasks

Normal `/sys` input remains a direct command path. A natural-language request
is promoted to the bounded workspace agent only when it contains both an
implementation action and a workspace/code target:

```text
complex request detected
-> model returns a typed JSON plan
-> one typed step is translated to one existing sys request
-> C(a), U(a), entropy, RDS and actionable risk are evaluated
-> advisory / soft / hard control decides whether the step is released
-> sys policy and path confinement run
-> mutations require read-after-write evidence
-> only a successful, verified observation releases the next dependency
-> final candidate is submitted to the existing ai-validator handshake
```

The implementation lives in `services/orchestrator/workspace_agent.py`. It
accepts `list`, `read`, `mkdir`, `write`, `touch`, a runtime-injected
`install` step, and the narrow `run` operations (`python3` or `julia`). The
planner cannot emit arbitrary installation commands. After parsing generated
Python and `pyproject.toml`, the runtime may inject one typed `venv-pip` step
for approved missing dependencies. It does not accept shell strings, pipes,
redirects, deletion, arbitrary package installation, URLs, VCS sources, local
package paths, or general network operations. Step and resource limits are not
fixed workflow constants. They follow the existing LIARA model:

```text
C(a) = alpha*depth + beta*tokens + gamma*tools + delta*entropy
U(a) = goal_progress - C(a)
U'   = U(a) * (1-H)
RDS  = log2(1 + D*B) + lambda*H
```

Below the calibrated soft budget the loop remains advisory. Above it, positive
confidence-adjusted utility is required. Exceeding cost or actionable-risk
hard limits blocks the next step. Thresholds come from
`Settings.reasoning_threshold_profile()` and `MAX_REASONING_STEPS`; each
decision and its cost components are returned in the step audit. The schema's
64-step/1-MiB bounds are emergency parser/transport ceilings only, not runtime
resource allocations.

The budget calculation uses LIARA's existing **local** `JuliaBridge` with the
allowlisted `services/simulation/models/workspace_budget.jl` model. The audit
therefore reports `compute_backend=julia` and `compute_path=primary`. Python
implements the parity fallback only; a Julia failure is surfaced as
`fallback_reason`. This local math path is separate from Python/Julia commands
that a workspace step may execute inside Debian WSL.

### Validator follow-up flow

Every terminal workspace run is written as a session-scoped short-term context
artifact after the validator result returns. The compact artifact contains the
run id, step statuses and evidence, mathematical decisions, validator job,
summary, artifacts and normalized findings. Obvious credential-shaped values
are redacted before persistence.

```text
workspace run + validator result
-> ContextUpsertRequest(workspace_agent_run:<session>:<run>)
-> session-scoped short-term context
-> follow-up intent ("Was fehlte?", "Welche Findings?")
-> ContextSearchRequest(session_id)
-> newest workspace_agent_run artifact
-> deterministic finding report
```

The follow-up does not infer an error from chat prose. If no artifact exists,
LIARA says so explicitly. If one exists, file, line, severity, message, failed
step and validator summary are taken directly from the persisted payload.

### Planner provider fallback

Before planning, the orchestrator's central provider selection chooses the
planner provider with `force_context=true` and `tools_used=["sys"]`. Complex
workspace planning is therefore main-path work and is never delegated to the
migrated `openvino_npu_helper`, whose contract is limited to short typed helper
tasks. Workspace planning uses `Settings.DEFAULT_LLM_PROVIDER` when the request
does not explicitly select a provider. It no longer hard-codes `hybrid`: an
unconfigured fast OpenVINO failure must not cancel a still-running Ollama
planner at the hybrid race timeout. If an explicitly preferred provider fails,
the planner tries the configured default and then the logical Ollama path.
Each attempt records requested/result/winner provider, model, status and error
inside `plan.planning`. Invalid JSON or a contract-invalid plan also releases
the next provider attempt instead of ending as an opaque planning failure. The
planner-local chain is an execution-failure fallback; it does not replace or
bypass the orchestrator's scheduling decision.

Configuration:

```text
LIARA_AGENT_WORKSPACE_ROOT=/home/liara/workspace
LIARA_AGENT_VALIDATOR_WORKSPACE=\\wsl.localhost\Debian\home\liara\workspace
LIARA_AGENT_VALIDATOR_TIMEOUT_SECONDS=180
LIARA_AGENT_SYS_MAX_ATTEMPTS=2
LIARA_AGENT_PLANNER_MAX_TOKENS=32768
LIARA_AGENT_DEPENDENCY_ALLOWLIST=pydantic,pytest
LIARA_AGENT_DEPENDENCY_TIMEOUT_SECONDS=180
LIARA_AGENT_TEST_TIMEOUT_SECONDS=120
LIARA_AGENT_COST_ALPHA=0.35
LIARA_AGENT_COST_BETA=0.00025
LIARA_AGENT_COST_GAMMA=0.75
LIARA_AGENT_COST_DELTA=1.5
```

The request's `max_tokens` value is passed through to workspace planning. The
Textual and CLI default is 32768, so a multi-file plan can include complete
file bodies without a per-session `/max-tokens` command. Setting
`LIARA_AGENT_PLANNER_MAX_TOKENS` explicitly overrides that request value for
deployments that require a fixed planner ceiling.

Idempotent workspace mutations (`mkdir`, `touch`, and overwrite writes) use a
bounded timeout recovery path. A timed-out WSL client is terminated, the target
is read back and hash-verified, and an already completed mutation is accepted
with reconciliation evidence. If the target does not match, the workspace
agent retries the same typed mutation once. Append writes and arbitrary run
commands are never retried automatically.

On a Windows host, mutation reconciliation can use the confined
`\\wsl.localhost\<distro>\...` view if the second WSL verification process also
times out. This is a verification-only fallback: the mutation itself still
runs inside WSL, the resolved path must remain below the configured WSL root,
and the returned size/SHA-256 must match the requested state.

### Workspace dependency recovery

Dependency recovery is deliberately narrower than shell-level `pip` access:

```text
generated Python / pyproject.toml
-> AST and project metadata inspection
-> missing package intersects configured allowlist
-> venv-pip install --disable-pip-version-check --no-input <spec...>
-> venv-pip show <package...>
-> python -m pytest inside workspace .venv
```

The install is audited as a high-risk environment mutation and network call
with `/home/liara/workspace/.venv` as its target. `show` is a separate,
read-only verification action. A declared dependency outside
`LIARA_AGENT_DEPENDENCY_ALLOWLIST` blocks planning instead of being installed.
Python test steps resolve to the workspace-local interpreter, so host Python
and the global WSL environment remain untouched.

`LIARA_AGENT_VALIDATOR_WORKSPACE` is the host-visible read-only mount source
for the existing Docker validator. Set it explicitly if the WSL distribution
or validator host differs. The Textual Runtime card displays agent steps and
validator state separately; its existing `turns` value remains the count of
user and assistant chat turns, not reasoning steps.

`collect` returns:

- original snapshot hash;
- candidate manifest hash;
- unified patch hash and path;
- immutable candidate workspace path;
- ready-to-submit `/validator/submit` payload.

## LIARA tool calls

Lifecycle tool:

```json
{"tool_name":"wsl_session","parameters":{"action":"create","label":"translator-test"}}
```

Direct execution stays on `sys`:

```json
{
  "tool_name":"sys",
  "parameters":{
    "command":"julia",
    "args":["--version"],
    "workspace_session_id":"sess-...",
    "request_id":"req-...",
    "run_id":"run-...",
    "session_id":"trace-...",
    "source":"orchestrator",
    "context":"simulation"
  }
}
```

The executor resolves `workspace_session_id` from the local registry. A caller
cannot redirect the selected session to another working directory.

## Snapshot policy

The default snapshot excludes credentials, runtime state, large generated
trees, caches, models, databases, logs, backups, and Windows virtual
environments. `.env.example` remains included while `.env` and `.env.*` are
excluded. Symlinks and Windows reparse points are never copied.

Limits are fail-closed:

```text
LIARA_WSL_SESSION_MAX_SNAPSHOT_BYTES = 256 MiB
LIARA_WSL_SESSION_MAX_FILE_BYTES     = 32 MiB
LIARA_WSL_SESSION_MAX_PATCH_BYTES    = 8 MiB
```

If creation fails, the partial WSL session is removed and an audit event is
written.

## Validator handshake

After collection, submit the returned `validator_request` to the existing
memory-service endpoint:

```text
POST /validator/submit
```

The request metadata binds validation to:

```text
snapshot_hash
candidate_hash
patch_hash
session_id
request_id
run_id
```

`WslSessionTool(action="validate")` performs this submission directly. It does
not apply the candidate to the canonical source.

## Persistence and audit

```text
logs/services/wsl_sessions/<session-id>.json
logs/services/wsl_sessions.jsonl
artifacts/wsl_sessions/<session-id>/collections/<collection-id>/
```

Registry records are atomically replaced. Audit events are append-only and
fsynced. Local collections survive WSL session deletion for validation and
governance review.
