# Safe Simulation Mode

> Abgrenzung, Stand 2026-07-13: Dieses Dokument beschreibt die Mock-Simulation
> ohne reale Toolausfuehrung. Fuer echte, aber isolierte Tests, Julia-Compute
> und Codeexperimente existiert zusaetzlich die native WSL-Session-Runtime in
> `docs/WSL_SESSION_RUNTIME.md`.

## Overview

Safe Simulation Mode lets LIARA run the orchestration and tool-dispatch flow without performing real tool side effects.

The important distinction is this:

- The judge layer recognizes and annotates simulation mode.
- Real side-effect skipping happens at tool execution time when `ToolExecutionRequest.simulation_mode=True`.

That means the authoritative switch for mock tool execution is not just judge metadata by itself, but propagation of `simulation_mode` from the orchestrator request into tool execution requests.

## Actual Runtime Flow

```text
OrchestratorRequest.simulation_mode=True
  -> Orchestrator stores _simulation_mode
  -> ExecutorRequest.simulation_mode=True
  -> ToolExecutionRequest.simulation_mode=True
  -> ToolCoordinator returns MockResultGenerator output
  -> No real tool execution happens
```

## What the Judge Does

The pre-action simulation adapter is in [services/judge/adapters/pre_action_simulation_mode.py](c:/ai/LIARA/services/judge/adapters/pre_action_simulation_mode.py).

When `context.metadata.simulation_mode=True`:

- supported actions are marked as simulated
- the decision includes constraints such as `skip_actual_execution=True`
- unsupported simulation actions return a warning-style decision

In [services/judge/engine.py](c:/ai/LIARA/services/judge/engine.py), simulation mode short-circuits standard pre-action checks and returns the simulation decision early.

So the practical behavior is:

- simulation mode does not run the normal pre-action safety adapter chain
- it returns a simulation-specific decision instead
- actual execution is still prevented only if the tool request also carries `simulation_mode=True`

## What Actually Triggers Mock Execution

The mock execution gate is in [services/tools/coordinator.py](c:/ai/LIARA/services/tools/coordinator.py).

```python
if request.simulation_mode:
    mock_result = MockResultGenerator.generate(
        tool_name=request.tool_name,
        parameters=request.parameters,
    )
```

If `request.simulation_mode=False`, the coordinator performs normal tool lookup, validation, and execution.

## Propagation Path

The propagation is currently wired like this:

- [services/contracts/service_boundaries.py](c:/ai/LIARA/services/contracts/service_boundaries.py#L138): `OrchestratorRequest.simulation_mode`
- [services/orchestrator/orchestrator.py](c:/ai/LIARA/services/orchestrator/orchestrator.py#L390): stored as `self._simulation_mode`
- [services/orchestrator/orchestrator.py](c:/ai/LIARA/services/orchestrator/orchestrator.py#L2867): forwarded into `ExecutorRequest.simulation_mode`
- [services/orchestrator/executor.py](c:/ai/LIARA/services/orchestrator/executor.py#L521): forwarded into each `ToolExecutionRequest`
- [services/contracts/service_boundaries.py](c:/ai/LIARA/services/contracts/service_boundaries.py#L487): `ToolExecutionRequest.simulation_mode`

So the main operational switch is request propagation, not just judge metadata.

## Supported Simulation Profiles

The actual mock generator implementation is in [services/simulation/mock_result_generator.py](c:/ai/LIARA/services/simulation/mock_result_generator.py).

These tool families have dedicated mock generation:

| Tool | Simulation support | Notes |
| --- | --- | --- |
| `sys` | Yes | Specialized handling for `date`, `curl`, generic shell commands |
| `compute.run` | Yes | Dedicated compute mock generation |
| `compute.generate` | Yes | Dedicated model-generation mock profile |
| `read_file` | Legacy support | Mock file contents for older compatibility paths |
| `list_files` | Legacy support | Mock directory listing for older compatibility paths |
| `web_search` | Legacy support | Mock web results for older compatibility paths |
| other tools | No dedicated generator | Falls through to unsupported simulation error |

## Result Shapes

### Successful simulated tool

The coordinator maps the mock result into a normal `ToolExecutionResult`:

```json
{
  "tool_name": "sys",
  "status": "success",
  "output": {
    "source": "sys",
    "kind": "time_lookup",
    "utc_iso": "2026-04-27T00:00:00Z",
    "summary_text": "Current UTC time: 2026-04-27T00:00:00Z"
  },
  "error": null,
  "execution_ms": 12
}
```

### Unsupported simulated tool

Unsupported tools return a simulated error result, still using the standard execution contract:

```json
{
  "tool_name": "some_unknown_tool",
  "status": "simulated_error",
  "output": null,
  "error": "[SIMULATED_ERROR] No simulation profile for tool 'some_unknown_tool' - Tool simulation not available for this action.",
  "execution_ms": 0
}
```

## Sys Simulation Nuance

`sys` simulation does not run the real WSL command policy or the real command.

Instead, [services/simulation/mock_result_generator.py](c:/ai/LIARA/services/simulation/mock_result_generator.py) fabricates outputs based on:

- `command`
- `args`
- optional `context`

Examples:

- `date` returns a simulated UTC timestamp structure
- `curl` returns simulated web lookup results
- unknown shell commands return generic simulated shell output

That makes simulation mode good for planning and pipeline testing, but not for validating real policy behavior or exact command output.

## What Simulation Mode Is Good For

- testing tool routing without side effects
- validating that the orchestrator propagates tool intent correctly
- exercising downstream response synthesis and validation
- dry-running compute and sys workflows

## What It Is Not Good For

- verifying real WSL sys policy allow or deny behavior
- validating exact command stdout or stderr
- checking real network, filesystem, or Julia runtime behavior
- proving that a tool would succeed in production conditions

## Relationship to Native WSL Sessions

The two mechanisms have different purposes:

| Mechanism | Executes commands | Side effects | Primary use |
| --- | --- | --- | --- |
| Safe Simulation Mode | No | Mock results only | Routing and pipeline dry-runs |
| Native WSL Session | Yes, through policy-gated `sys` | Confined to session `work` | Tests, Julia compute, code experiments |

A native WSL session starts from a filtered snapshot of the canonical local
LIARA tree. It exposes a read-only `source` and mutable `work` tree, then
returns only a patch, immutable candidate, hashes and trace metadata. It never
performs direct write-back into `C:\ai\LIARA`.

The intended combined flow is:

```text
Mock simulation -> verify planned routing
Native WSL session -> execute the approved experiment
Collect -> create patch and candidate
ai-validator -> assess the candidate
Governance -> decide whether to apply outside the session
```

## Current Gaps

These are the main gaps between the conceptual model and the current implementation:

1. Judge simulation support is broader than mock-generator support.
2. The judge marks simulated execution, but actual side-effect skipping depends on `ToolExecutionRequest.simulation_mode` reaching the coordinator.
3. Simulation mode bypasses the normal pre-action judge chain rather than layering on top of it.

## Key Takeaway

Safe Simulation Mode is real and wired through the runtime, but it is narrower than the older documentation implied.

The most accurate mental model is:

- judge: marks and constrains simulated intent
- orchestrator and executor: propagate the simulation flag
- coordinator: switches from real execution to mock generation
- mock generator: only simulates a limited set of tool families
