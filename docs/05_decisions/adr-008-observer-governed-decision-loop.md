# ADR-008: Governed Decision Loop & Observer Boundary

**Status**: Accepted  
**Date**: 2026-09-15  
**Deciders**: Mirko, Nephy, LIARA Architecture Core  
**Scope**: System-level architecture, Observer role, governed decision loop, assurance boundaries, domain integration

---

## Context

LIARA has evolved beyond a simple model-centric orchestration pattern. The current architecture already separates inference, memory, tools, planning, validation, evidence, governance and DDNA, and the existing ADRs establish strong invariants around tool evidence, retrieval, vision evidence and capability-bounded access.

The architectural question is therefore not whether LIARA needs another supervisor or control subsystem, but how the responsibilities that already exist should be described as the system expands toward partially autonomous, continuously observing and domain-integrated operation.

A recurring pattern is already present:

```text
observe -> interpret -> plan -> authorize -> execute -> verify -> remember
```

The existing Self-Observer is a central part of this pattern. Repository review confirms that it is implemented as a **read-only cyclic observer**: it samples and evaluates state, derives health/attention conditions and may raise a request for further inspection, but it does not own the authority to execute actions.

The control loop is therefore a higher-level architectural description of existing LIARA responsibilities, not a new service and not a replacement for the current Orchestrator.

---

## Decision

LIARA adopts a **Governed Decision Loop** as a system-level architectural model, with an explicit **Observer Boundary**.

This model does **not** replace the existing request/response pipeline. It generalizes it.

The canonical loop is:

```text
Observe
   ↓
Interpret
   ↓
Plan
   ↓
Authorize
   ↓
Execute
   ↓
Verify
   ↓
Remember
   ↺
```

The loop is an architectural model rather than a requirement that every deployment execute these stages synchronously or in a single process.

### 1. Observer is the observation boundary, not the action authority

The Observer continuously describes relevant system and environment state and may emit state transitions, anomalies and attention signals.

Typical states remain compatible with the existing Self-Observer contract:

```text
healthy
attention
degraded
critical
unknown
```

The Observer answers:

> What is happening now?

It does **not** independently answer:

> What should be executed?

and it does not acquire additional capabilities merely because it detects a condition.

A self-inspection trigger emitted from the Observer is an **escalation/request signal**, not an authorization and not a capability grant. Any resulting inspection or action must cross the separate assurance/governance boundary and remain subject to policy, capability and audit constraints.

> **Invariant: The Observer may escalate attention, never privilege.**

### 2. Responsibilities remain separated

The loop maps onto distinct LIARA responsibilities:

```text
Observe
  -> Self-Observer / Input Profiler / sensors / external observations

Interpret
  -> Situation Analysis / Reasoning Control / evidence interpretation

Plan
  -> Planner / Router / agent coordination

Authorize
  -> Capability Governance / policy / Pre-Action Judge / assurance gates / approval context

Execute
  -> Tools / agents / LiNeP-connected workers / external systems

Verify
  -> Validator / Post-Result Judge / observed state change / health feedback / evidence integrity

Remember
  -> Memory / Knowledge Graph / trace / provenance / DDNA-relevant continuity
```

No component is permitted to collapse these boundaries merely for convenience.

### 3. Domain logic is outside the LIARA Core

LIARA must remain usable across domains without embedding domain-specific business logic into the core.

The architectural separation is:

```text
LIARA CORE
- epistemic state
- provenance
- policy / governance
- capability boundaries
- validation
- audit / trace
- continuity / DDNA invariants

LIARA RUNTIME
- planning
- routing
- inference
- agents
- tools
- execution coordination
- distributed resource access

DOMAIN SYSTEMS / ADAPTERS
- industrial automation
- Earth observation
- IT operations
- robotics
- research
- personal assistant
- future mission-specific systems
```

A domain system may provide specialized observations, models, tools and actions, but it must not redefine LIARA's core epistemic or governance invariants or bypass the authorization boundary.

### 4. The existing request pipeline remains valid

The current linear chat/request pipeline remains a request-scoped expression of one pass through the broader loop:

```text
Input Situation Profile
-> Context / Librarian
-> Router / Planner
-> Tool Discovery / Execution
-> Generation / Inference
-> Validation / Judge
-> Memory Commit
```

This ADR therefore does not require a rewrite of the current Orchestrator.

Continuous or event-driven operation may execute the same responsibilities repeatedly, while request/response operation may execute them once per interaction.

### 5. Decision authority and execution authority remain distinct

A recommendation, plan, observation or model output is not an authorization.

A valid action requires both a meaningful plan and an allowed execution context:

```text
Meaningful(action)
AND EvidenceSufficient(action)
AND CapabilityGranted(action)
AND PolicyAllows(action)
AND RuntimeEnforces(action)
```

The Observer, Planner, Solver, Judge or model may not self-grant missing capabilities.

---

## Relationship to Existing ADRs

This ADR is **additive** and does not supersede ADR-004 through ADR-007.

- **ADR-004** remains the evidence boundary for actual tool execution.
- **ADR-005** remains the separation between discovery and grounding evidence.
- **ADR-006** remains the canonical vision-evidence path.
- **ADR-007** remains **Accepted & Frozen** and continues to define epistemic subgraphs, traversal-level authorization and capability attenuation.

ADR-008 generalizes these decisions into a system-level decision loop and clarifies where the Observer and assurance boundaries fit.

---

## Repository Alignment / Evidence

The decision has been checked against the current implementation and does not require a production-code migration.

- `services/self_observer/core.py` defines the Self-Observer as a **read-only cyclic self-observer**. It samples state, derives conditions and may request further inspection, but does not directly start arbitrary work.
- `services/self_observer/assurance.py` provides the separate `SelfInspectionGate`, demonstrating that observer-triggered inspection crosses an assurance boundary outside Observer authority.
- `services/contracts/self_observer.py` defines the structured Observer states `healthy`, `attention`, `degraded`, `critical` and `unknown`.
- `tests/unit/test_self_observer.py` exercises Self-Observer state and inspection/gate behavior.
- `services/orchestrator/` already implements the request-scoped coordination pipeline. ADR-008 describes the broader architectural semantics around that implementation rather than replacing it.
- ADR-004 through ADR-007 already provide the evidence, retrieval, vision and capability constraints required by the `Authorize`, `Verify` and `Remember` portions of this loop.

The repository review therefore supports acceptance of ADR-008 as an architectural clarification of existing behavior.

---

## Architectural Invariants

1. **Observation is not authorization.**
2. **Planning is not execution.**
3. **Execution is not proof of intended effect.**
4. **Observed effect is not automatically semantic truth.**
5. **Epistemic state and provenance survive across loop iterations.**
6. **Capabilities may be granted explicitly and attenuated, never inferred from technical reachability.**
7. **Domain adapters may extend capability but may not redefine core governance invariants.**
8. **The Observer may escalate attention, never privilege.**
9. **Every autonomous or semi-autonomous action must remain auditable.**
10. **DDNA continuity is defined by architectural invariants, not by one model, device, UI or execution provider.**

---

## Consequences

### Positive

- The existing architecture gains a coherent model for both conversational and continuously operating systems.
- The Self-Observer receives a clear first-class role without becoming a monolithic controller or implicit authority plane.
- New domains can integrate through adapters without contaminating core semantics or governance.
- Autonomous behavior remains bounded by the same evidence, assurance and capability rules already established by LIARA.
- The architecture can scale from chat and IT operations to industrial, geospatial, robotic or mission-specific systems without changing its core identity.
- The current implementation remains valid; no forced Orchestrator rewrite follows from this decision.

### Costs / Risks

- Continuous operation requires explicit lifecycle, scheduling, event and backpressure semantics beyond the current request pipeline.
- The boundary between `Interpret` and `Plan`, and between `Verify` and `Remember`, must remain explicit in implementation contracts.
- Domain adapters need clear capability manifests and evidence contracts.
- The term `Observer` must not be allowed to drift into an implicit privileged control-plane meaning.
- Future self-inspection or remediation paths must continue to preserve the independent assurance boundary.

---

## Non-Goals

This ADR does not:

- replace the current Orchestrator,
- introduce a new Observer implementation,
- make the Observer an action authority,
- authorize autonomous self-modification,
- move domain-specific logic into the LIARA Core,
- supersede ADR-007,
- require all LIARA deployments to run continuously.

---

## System-Level Framing

ADR-008 supports the following architectural description without requiring an immediate rename of the project or README title:

> **LIARA is a model-independent governed decision architecture that transforms observations and evidence into authorized, auditable actions while preserving epistemic provenance and continuity.**

This framing describes the architectural role; the established name **Model-Independent AI Orchestration Architecture** remains valid as the project identity.

---

## Short Form

> **The LIARA control loop is not a new subsystem. It is the higher-level architectural description of how the existing Observer, reasoning, planning, governance, execution, verification and memory components cooperate.**

The Observer establishes what is happening. Interpretation establishes what the evidence means. The Planner proposes what to do. Governance determines what may be done. Execution performs the allowed action. Verification establishes what actually happened. Memory preserves the result with provenance and epistemic state.
