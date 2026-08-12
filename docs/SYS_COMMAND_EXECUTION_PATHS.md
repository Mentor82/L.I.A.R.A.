# Sys Command Execution Paths in LIARA

## Overview

All LLMs on the Linux platform in WSL Debian can use sys commands, but only within restricted command and argument permissions.

There are two execution paths:

1. Direct API invocation via `POST /tools/sys/invoke`
2. Orchestrator-mediated execution via scout, planner, router, validator, and executor

Both paths can target a registered native WSL test/simulation workspace by
supplying `workspace_session_id`. The executor then derives the confined
`workdir`, execution user and snapshot hash from the session registry. A caller
cannot replace that workdir with a path outside the selected session.

## Direct API Path

### Flow

```text
Client -> POST /tools/sys/invoke
  -> ToolCoordinator.execute_tool(...)
  -> WslExecutorTool.execute(...)
  -> _policy_check(command, args)
  -> ToolExecutionResult
```

### Response Schema

Direct `sys` calls always return a valid `ToolExecutionResult` shape.

```json
{
  "tool_name": "sys",
  "status": "success|failed|partial",
  "output": "...",
  "error": null,
  "execution_ms": 123.45
}
```

For blocked calls, the schema stays valid and only `status` and `error` change.

```json
{
  "tool_name": "sys",
  "status": "failed",
  "output": {
    "status": "failed"
  },
  "error": "Flag '-d' is not permitted (write/upload/auth/proxy/insecure).",
  "execution_ms": 9.8
}
```

### Policy Semantics

For direct `sys` requests, the policy buckets mean this:

| Bucket | Runtime meaning | Typical examples |
| --- | --- | --- |
| Whitelist | Explicitly allowed | `date`, `python3 -c`, `ls /home/liara/workspace` |
| Greylist | Also allowed, but more sensitive | `curl -m 2`, `ls /tmp`, `tee -a ...` |
| Blacklist | Denied | `curl -d`, `grep -r`, writes into `/etc` |

Greylist is not an automatic deny in the current implementation. It is a separate allowed classification for more sensitive flags and paths.

### Verified Direct API Examples

These direct `sys` calls were verified successfully against the running API:

| Command | Outcome |
| --- | --- |
| `ls /home/liara/workspace` | Success |
| `python3 -c "print('Hello from Python')"` | Success |
| `date` | Success |
| `time echo test` | Success |
| `curl -s -m 2 https://example.com` | Success |
| `ls /tmp` | Success |
| `tee -a /home/liara/temp/grey-test.txt` with `stdin_text` | Success |
| `find /home/liara/workspace -maxdepth 1 -print` | Success |
| `grep -r test /home/liara/workspace` | Blocked |
| `tee /tmp/test.txt` without `stdin_text` | Blocked |

So the direct API path is usable externally for safe requests inside the command profile.

## Orchestrator Path

### Orchestrator Flow

```text
User query
  -> Scout
  -> Planner
  -> Router
  -> Validator
  -> Executor
  -> WslExecutorTool
```

### Role of the Components

#### Scout

Collects context from memory, workspace state, and other system facts.

#### Planner

Chooses which sys commands are needed and with which arguments.

#### Router

Chooses the appropriate execution path or service.

#### Validator

Applies safety checks before execution proceeds.

#### Executor

Runs the selected command path and returns the structured result.

### Why the Orchestrator Still Matters

The orchestrator path is better for:

- Ambiguous user intent
- Multi-step tasks
- Tool sequencing
- Cases where planning is needed before choosing command arguments

It does not replace the low-level policy gate. The same runtime policy still applies when execution reaches `WslExecutorTool`.

## Comparison

| Aspect | Direct API | Orchestrator |
| --- | --- | --- |
| Path | `/tools/sys/invoke` | Chat -> Orchestrator |
| Context | None | Full task context |
| Policy | Strict, profile-based | Planned, then enforced at execution |
| Best for | Known-safe low-level calls | Ambiguous or multi-step tasks |
| Response schema | Always valid | Always valid |

## Key Takeaway

External callers can use `POST /tools/sys/invoke` for sys commands in the safe policy envelope. Whitelist and greylist requests are allowed. Blacklist and unknown requests are rejected cleanly, still with a valid response schema.

The orchestrator exists to plan and coordinate more complex work, not because direct `sys` invocation is universally disabled.

For code experiments and Julia compute, create and collect the workspace via
`WslSessionTool`; continue to execute commands through `sys`. Session lifecycle
does not create a second shell or bypass this policy path. See
`docs/WSL_SESSION_RUNTIME.md`.
