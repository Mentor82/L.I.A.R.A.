# ADR-008: Observer-led Governed Decision Loop & Domain Separation

**Status**: Proposed  
**Date**: 2026-09-15  
**Deciders**: Mirko, Nephy, LIARA Architecture Core  
**Scope**: System-level architecture, Observer role, decision loop, governance boundaries, domain integration

---

## Context

LIARA has evolved beyond a simple model-centric orchestration pattern. The current architecture already separates inference, memory, tools, planning, validation, evidence, governance and DDNA, and the existing ADRs establish strong invariants around tool evidence, retrieval, vision evidence and capability-bounded access.

The open architectural question is therefore not whether LIARA needs a fundamentally new architecture, but how the existing components should be framed as the system expands toward partially autonomous, continuously observing and domain-integrated operation.

A recurring pattern is already present:

```text
observe -> interpret -> plan -> authorize -> execute -> verify -> remember
```

The existing Self-Observer is a central part of this pattern. It must not be duplicated or replaced by a new control subsystem. Instead, the control loop should be understood as a higher-level description of how existing LIARA components interact.

---

## Decision

LIARA adopts an **Observer-led Governed Decision Loop** as a system-level architectural model.

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

### 1. Observer is the observation boundary, not the action authority

The Observer continuously describes relevant system and environment state and may emit state transitions, anomalies and attention signals.

Typical states remain compatible with the existing Self-Observer model:

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
  -> Capability Governance / policy / Pre-Action Judge / approval context

Execute
  -> Tools / agents / LiNeP-connected workers / external systems

Verify
  -> Validator / Post-Result Judge / observed state change / health feedback

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

A domain system may provide specialized observations, models, tools and actions, but it must not redefine LIARA's core epistemic or governance invariants.

### 4. The existing request pipeline remains valid

The current linear chat/request pipeline remains an implementation of one pass through the broader loop:

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

A recommendation, plan or model output is not an authorization.

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
- **ADR-007** remains frozen and continues to define epistemic subgraphs and capability attenuation.

ADR-008 generalizes these decisions into a system-level loop and clarifies where the Observer fits.

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
- The Self-Observer receives a clear first-class role without becoming a new monolithic controller.
- New domains can integrate through adapters without contaminating core semantics or governance.
- Autonomous behavior remains bounded by the same evidence and capability rules already established by LIARA.
- The architecture can scale from chat and IT operations to industrial, geospatial, robotic or mission-specific systems without changing its core identity.

### Costs / Risks

- Continuous operation requires explicit lifecycle, scheduling, event and backpressure semantics beyond the current request pipeline.
- The boundary between `Interpret` and `Plan`, and between `Verify` and `Remember`, must remain explicit in implementation contracts.
- Domain adapters need clear capability manifests and evidence contracts.
- A future implementation must prevent the Observer from becoming an implicit privileged control plane.

---

## Non-Goals

This ADR does not:

- replace the current Orchestrator,
- introduce a new Observer implementation,
- authorize autonomous self-modification,
- move domain-specific logic into the LIARA Core,
- supersede ADR-007,
- require all LIARA deployments to run continuously.

---

## Short Form

> **The LIARA control loop is not a new subsystem. It is the higher-level architectural description of how the existing Observer, reasoning, planning, governance, execution, verification and memory components cooperate.**

The Observer establishes what is happening. The Planner proposes what to do. Governance determines what may be done. Execution performs the allowed action. Verification establishes what actually happened. Memory preserves the result with provenance and epistemic state.
