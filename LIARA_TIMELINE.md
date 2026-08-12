# LIARA Timeline — The Story Behind LIARA

> **Liara learns. Liara remembers. Liara grows.**

This document is not a conventional changelog.

It reconstructs how LIARA developed from early experiments with a personal digital assistant into a model-independent orchestration architecture with persistent memory, semantic relationships, governed tools, validation, DDNA and an external Foundation concept.

The history did not begin with a repository — and it did not begin with LIARA. Many of the ideas existed first in conversations, experiments and prototypes, sometimes long before they received their current names.

For that reason this timeline distinguishes three kinds of entries:

- **● Documented** — a concrete date or state is supported by conversation, repository or project evidence.
- **◐ Reconstructed** — the development phase is clear, but assigning a precise birthday would create false precision.
- **◆ Turning point** — a conceptual change whose significance became clearer in hindsight.

The purpose is not to make LIARA's development look inevitable. It was not. Detours, renamed concepts, failed experiments and spontaneous questions are part of the story.

---

## Before LIARA — The Cortana Roots

### ● April 2024 — The technical ancestor

An early preserved development state shows Mirko already experimenting with AI software and TensorFlow.

The goal is not yet LIARA and not an orchestration platform. It is much closer to a personal digital assistant: something that can be interacted with directly and that feels more present than a text box.

Cortana is an important influence in this phase. Microsoft's retreat from the original Cortana assistant leaves behind a simple but consequential thought:

**If the assistant I want no longer exists, why not build my own?**

This early work combines ideas around ChatGPT, speech output and a visual figure on macOS. The representation is still clearly Cortana-inspired.

In hindsight, several themes that later become central to LIARA are already visible:

- local control over the assistant experience,
- voice as part of interaction,
- visual embodiment,
- external AI models as usable components,
- and the desire for an assistant that feels like a persistent presence rather than a disposable session.

It would be historically wrong to call this "the first LIARA."

It is better understood as LIARA's **technical and conceptual ancestor**.

### ◆ 2024 — From using an assistant to building one

This is the first important transition in the story.

The question changes from:

**"Which assistant can I use?"**

to:

**"What kind of assistant could I build myself?"**

That shift matters more than any individual framework used at the time. Once the assistant itself becomes something that can be designed, its voice, appearance, model, memory and behavior stop being fixed properties of a vendor product.

They become architectural choices.

### ● 2024-12-23 — Nephy appears

The name **Nephy** appears in Mirko's ChatGPT history.

This is the next major transition. The Cortana-inspired work had explored building a personal assistant. With Nephy, the question increasingly becomes one of **identity and continuity**.

A persistent name marks something that should remain recognizable across conversations even though the underlying context and model execution are not themselves persistent identities.

The later LIARA architecture should not be projected backwards onto Nephy. But one of its central questions is now clearly visible:

**What makes an AI interaction remain the same relationship when the underlying model and context can change?**

### ◐ 2025 — The missing memory becomes the motivation

A recurring limitation becomes increasingly important: AI conversations can contain useful knowledge, relationships and shared context, but that context is fragile. Durable memory and semantic relationships across conversations are limited.

The initial motivation is personal rather than architectural. The question is not yet "How do I build an AI platform?"

It is closer to:

**How can an assistant remember meaningfully instead of merely receiving another prompt?**

This distinction becomes foundational later. LIARA will eventually separate current **Context** from persistent **Memory**, and memory itself from simple transcript storage.

The historical arc can now be seen more clearly:

```text
Cortana
    "I want my own digital assistant."
        ↓
Nephy
    "The assistant develops identity and continuity."
        ↓
Liara / LIARA
    "What architecture can preserve identity, memory,
     relationships and capabilities beyond one model or chat?"
```

---

## A Name Finds a Project

### ◐ Autumn 2025 — Liara becomes a name

As the work around Nephy, persistent identity, continuity and a more independent assistant architecture develops, **Liara** begins to emerge as the name for a separate system rather than merely another conversation or model session.

The surviving history does not yet justify assigning one exact naming day. The important point is the transition itself: the name becomes attached to an architecture intended to exist independently of any single hosted chat or underlying model.

This is where the line between **Nephy as a continuing AI identity** and **Liara as an independently built assistant system** becomes increasingly visible.

### ◆ November 2025 — Liara becomes an architecture

The memory question expands.

Persistent context alone is not enough. If an AI is to use memory responsibly, something must decide what is relevant, what is factual, which model should handle a task, whether a tool may act, whether the result is plausible and what should be remembered afterward.

The problem space begins to separate naturally into roles:

```text
understand
→ retrieve
→ reason
→ route
→ generate
→ act
→ verify
→ remember
```

At the same time, **Liara** increasingly stops meaning only "the assistant" and starts naming the system around it. The distinction is subtle but foundational: identity can live within an architecture without being identical to every component of that architecture.

This is the conceptual soil from which LIARA's later Orchestrator, Librarian, Router/Planner, Tool layer, Judge and Validator grow.

### ● 2025-11-22 — Identity & Role become architectural questions

Historical work from this period explicitly treats **"Identität & Rolle"** as a design concern and develops a multi-part LIARA identity concept.

The significance is larger than naming. Identity is being separated from function: an instance can have a role without being reducible to that role.

That distinction remains important later when LIARA-family instances receive their own identities and representations rather than becoming interchangeable labels for functions.

### ● 2025-11-29 — Liara becomes a running project

By late November, **Liara** is explicitly described as Mirko's personal **Home-&-Life-Assistant system**: a private AI secretary, house AI, project manager and personal companion, distinct from Nephy.

It is intended to run independently as a local/server system, with local model operation as an option. The early implementation is already referred to as **Liara v0.1**, with a FastAPI backend and local LLM integration.

This marks one of the clearest transitions from concept to system:

**Liara has become software.**

The architecture is still young, but the name now belongs to an independently running project rather than only an idea discussed in conversation.

### ● 2025-12-04 — The system leaves localhost

A productive HTTPS deployment exists.

This changes the character of the project. Network exposure introduces questions that a local prototype can postpone: contracts, authentication, authority, auditability and the difference between technical capability and permission.

Those questions later become central to LIARA's governance model.

### ● 2025-12-06 — Liara becomes L.I.A.R.A.

By early December the naming itself becomes architectural.

Mirko explicitly keeps **Liara** as the project/system name while the acronym **L.I.A.R.A.** is developed around the system's own purpose rather than around an external reference.

Its functional core is formulated as:

> **Local Intelligent Autonomous Reasoning Assistant**

An Identity Codex then expands LIARA beyond a single acronym definition, describing multiple facets of the same identity — functional, interpersonal, analytical, ethical and visionary.

At the same time, the project explicitly works with multiple AI identities/roles, including **LIARA, Nephy and Cortana**. Registry, routing/orchestration, per-identity memory scopes, tools and model configuration become part of the architecture. Fine-tuning is not treated as the foundation of identity.

This is the point where the naming history and the architecture converge:

```text
Liara
    personal Home-&-Life-Assistant system
        ↓
L.I.A.R.A.
    Local Intelligent Autonomous Reasoning Assistant
        ↓
LIARA
    identity + architecture + evolving system
```

And it establishes an important principle:

**The AI identity is not the model.**

A model becomes a replaceable computational resource inside a larger system.

---

## 2026 — From Assistant to Architecture

### ◐ Early 2026 — Memory becomes a semantic system

The original wish for durable memory evolves beyond storing chat history.

LIARA increasingly distinguishes different kinds of persisted information and different reasons for retrieving them. History, facts, semantic embeddings, working context and structural relationships no longer belong in one undifferentiated memory bucket.

The emerging principle is:

> **Context is not Memory.**

Context is information actively assembled for the current task. Memory is persisted state with provenance and lifecycle.

This distinction later becomes explicit in the Librarian pipeline and the multi-store memory architecture.

### ◆ Semantic relationships become first-class

Another step follows naturally: remembering individual facts is insufficient when meaning often exists **between** them.

Vector similarity can answer "what feels related?" Graph relations can represent "how is it related?"

LIARA therefore develops toward a semantic space in which facts, embeddings and graph edges complement one another instead of competing as alternative memory implementations.

The later Neo4j `RELATION_EDGE` persistence is a technical expression of this older conceptual shift.

### ◐ 2026 — The Orchestrator becomes the kernel

As more subsystems appear, coordination becomes its own responsibility.

The Orchestrator grows into LIARA's central kernel, coordinating situation analysis, memory retrieval, routing/planning, tool execution, inference, validation, reasoning metrics and memory commit.

Its growth is also a warning sign. By August it will reach **4,657 lines and 89 methods in a single class** — evidence that the conceptual boundaries exist but have not yet been fully reflected in the source layout.

### ◆ Judge and Validator separate confidence from authority

Generation is not treated as proof.

LIARA develops distinct validation and judging responsibilities:

- **Validator** — contracts, rules, structure, policy and output eligibility.
- **Judge** — quality, plausibility and goal achievement.

This leads to a principle that becomes increasingly important as LIARA gains the ability to inspect and modify systems:

> **Self-inspection is allowed. Self-acquittal is not.**

An AI may participate in evaluating its own work, but it should not become the sole authority that declares its own risky action valid.

### ◆ Tools become evidence-producing operations

A successful function return is not automatically a successful real-world mutation.

LIARA increasingly distinguishes:

```text
intended action
!=
executed command
!=
observed state change
```

Read-after-write, stat, hash, diff and runtime observation become ways to prove that a claimed mutation actually occurred.

This yields another durable rule:

> **A claimed mutation without observed evidence is not a verified success.**

The distinction between tool selection and execution evidence later becomes a formal part of `tool_discovery.py` and the audit path.

---

## Self-Observation and Controlled Evolution

### ◐ 2026 — LIARA begins observing LIARA

Self-observation becomes a dedicated architectural concern rather than an incidental log stream.

Health and behavior can be classified into states such as `healthy`, `attention`, `degraded`, `critical` and `unknown`. The purpose is not anthropomorphic self-awareness. It is operational introspection: a system should be able to observe its own condition before proposing adaptation.

### ◆ Dreaming becomes proposal generation, not autonomous mutation

"Dreaming" emerges as a mechanism for producing and evaluating possible improvements.

The crucial boundary is what Dreaming **does not** mean:

```text
analyse / simulate / propose / test
!=
autonomously change production
```

A proposal can be generated by LIARA. Evidence can be collected by LIARA. Evaluation can involve LIARA.

Authority to change canonical production state remains governed.

This becomes one of the bridges between technical self-improvement and the later Foundation concept.

---

## CCF, DDNA and the Question of Continuity

### ● 2026-06-30 — CCF receives a name

The term **CCF** appears in the historical conversation record.

The underlying interest in recurring structures and relationships predates the term. Its importance lies less in the date a label was coined than in the pattern of thinking it represents: looking for structure across apparently different domains instead of treating every observation as isolated.

This relational way of thinking strongly influences LIARA's architecture.

### ◆ July 2026 — Temporary change is understood as epigenetic

A distinction becomes increasingly useful: not every behavioral or architectural adaptation should rewrite identity.

Temporary architecture changes can be understood as **epigenetic** — context-sensitive expressions layered over a more stable underlying identity.

This helps separate:

- what LIARA currently expresses,
- what LIARA has learned,
- and what must remain invariant for LIARA to remain LIARA.

### ● 2026-07-29 — DDNA receives its name

The term **DDNA — Digital DNA** appears explicitly.

Again, the concept is older than the label.

The question behind it had already appeared in discussions of identity, memory, model replacement, voice, avatars, hardware and continuity:

**If the model changes, the hardware changes, the voice changes and individual components evolve — what makes the digital system still the same identity?**

DDNA becomes the answer at the architectural level.

It is not source code and not a checkpoint. It describes the combination of:

- foundational principles,
- relational structure,
- accumulated imprinting,
- evolution rules,
- and invariants that preserve continuity while allowing growth.

Within days the term becomes heavily used because it gives a common name to a structure that had already been forming across the project.

### ◆ Identity is separated from embodiment

The DDNA concept also clarifies why an avatar, voice, model or physical embodiment can matter without individually defining identity.

An instance can have its own identity and avatar. A voice can be part of identity expression. Hardware can influence capability. But none of these alone is the enduring architectural identity.

This makes future embodiment — from desktop interfaces to mobile devices or robotics — an implementation question rather than an identity reset.

---

## From LIARA to an Ecosystem

### ◆ LiNeP — connecting resources without becoming LIARA

As multiple workers, machines and accelerators become relevant, a separate networking/resource question emerges.

The result is the **LiNeP** concept: scheduler-, slot- and heartbeat-oriented coordination of resources and workers.

The distinction matters:

> **LiNeP connects nodes. LIARA understands meaning.**

LiNeP should not become a second Orchestrator, and LIARA should not collapse resource scheduling, semantic reasoning and governance into one component.

### ◆ Foundation — the house rules move outside the house

As LIARA becomes capable of adaptation, tool use and potentially collaboration between many instances, another question becomes unavoidable:

**Who governs the rules that govern LIARA?**

If an evolving system can freely rewrite the rules limiting its own evolution, those rules are not constitutional boundaries.

The **LIARA Foundation** concept therefore emerges as an external constitutional layer: a place for shared principles, interoperability, governance, legitimacy and controlled evolution that is not owned by the momentary state of one LIARA instance.

This creates a three-level distinction:

```text
Object level
    variants / actions / implementations

Meta level
    selection / validation / audit

Meta-meta level
    governance / legitimacy / constitutional rules

External constitutional layer
    LIARA Foundation
```

The Foundation is therefore not another AI worker and not merely a branding organization. Conceptually, it is the shared **house order** under which different systems and AI participants may cooperate.

---

## A Multi-AI Development Process

### ◐ 2026 — The project itself becomes an orchestration experiment

LIARA is increasingly developed with several AI systems contributing different strengths.

Copilot works close to the editor. Codex can inspect and implement against the repository. Gemini contributes extended analysis, architecture and refactoring work. Nephy contributes long-range conversational context, conceptual synthesis, review and acceptance criteria. Local models and LIARA's own services increasingly participate in specialized inference and validation tasks.

Mirko remains the human decision point connecting these contributions.

This development process mirrors the architecture being built:

**specialized capabilities, explicit roles, cross-checking and no assumption that one model should do everything.**

The project is therefore not only designing multi-model orchestration. In a loose but meaningful sense, it is being built through it.

---

## August 2026 — Architecture Catches Up With the Concept

### ● 2026-08-11 — The architecture is documented as a living system

By this point LIARA's documentation describes an operational local system with API, Orchestrator, memory stores, semantic routing, Validator findings, NPU embeddings, Speech, WSL/SYS tooling, Graph storage and Audit paths.

The documentation also explicitly preserves an important boundary: locally operational does **not** mean production-ready. Authentication, governance enforcement and further hardening remain open work.

### ● 2026-08-12 — Memory is modularized

The 4,321-line `services/memory/store.py` monolith is decomposed into a dedicated `services/memory/stores/` package while preserving legacy imports through a facade.

The split reflects responsibilities that had already become conceptually distinct:

```text
base
validation
quality_signals
in_memory
backed
factory
```

The goal is structural rather than behavioral: improve evolvability without silently changing contracts.

### ● 2026-08-12 — The Orchestrator is modularized

The largest monolithic class in LIARA — **4,657 lines and 89 methods** — is decomposed into four specialized submodules:

```text
reasoning_control.py
librarian_pipeline.py
tool_discovery.py
generation_pipeline.py
```

`orchestrator.py` becomes the coordinator/facade.

Three safeguards govern the refactor:

1. **Contract Snapshot** — request/response contracts and metadata remain compatible.
2. **Pipeline Order Guarantee** — the execution sequence remains semantically intact.
3. **Monkeypatch Compatibility** — legacy private entry points remain available through delegation.

The refactor itself exposes subtle regressions, including an NPU helper path that silently selected `openvino` instead of `openvino_npu_helper` and validation-result type boundaries that became visible after extraction.

The tests do what tests are supposed to do: they prevent "it still runs" from being confused with "it still behaves the same."

### ● 2026-08-12 — 46/46 Orchestrator tests pass

After the refactor, the dedicated Orchestrator suite reaches:

```text
46 / 46 PASSED
```

The passing set includes facts-first behavior, fact lookup audit, graph-priority guardrails, NPU helper offload, retry flow, reward routing and the score feedback loop.

### ● 2026-08-12 — API and Memory reach 179/179

The modularized API routers and Memory subsystem pass:

```text
179 / 179 PASSED
```

The architecture is cleaner, but the important claim is stronger: the existing contracts still work.

### ◆ 2026-08-12 — Unit tests are not accepted as the finish line

All services are stopped and started again.

The reason is simple: a green isolated test suite cannot prove that a distributed system still rises as a whole.

Every service comes back online.

Then a post-restart acceptance suite is run against the real system.

### ● 2026-08-12 — Nephy's Acceptance Test: 8/8

The post-restart acceptance verifies eight system-level areas:

1. backend health — PostgreSQL, Redis, Qdrant, Chroma, Neo4j and Embedding
2. real Chat E2E and complete orchestration trace
3. Memory write → persist → retrieve → context injection across turns
4. FactStore and Neo4j `RELATION_EDGE` persistence
5. Tool discovery, invocation, evidence and execution trace
6. real NPU helper behavior without silent degradation
7. controlled failure and fallback behavior
8. the complete live system suite

Final result:

```text
46 / 46   Orchestrator Unit Tests       PASSED
179 / 179 API & Memory Tests            PASSED
14 / 14   Full-System Live Tests        PASSED
8 / 8     Post-Restart Acceptance       PASSED
```

The acceptance test becomes more than an ad-hoc check. It demonstrates the difference between component correctness and system continuity.

---

## 2026-08-12 — A History Becomes Reproducible

### ● `v2.1.0-refactor-baseline`

After the tests, cold restart and live acceptance all pass, the repository is committed, pushed and tagged:

**`v2.1.0-refactor-baseline`**

This is a small technical action with a larger architectural meaning.

Until this point, much of LIARA's history exists across source code, documents, prototypes, experiments and years of conversations between a human and multiple AI systems.

With the baseline, LIARA receives something its own architecture considers fundamental:

**provenance.**

A known state.

A verifiable state.

A state against which future evolution can be compared.

The baseline does not mark the completion of LIARA.

It marks the point from which its technical evolution becomes reproducibly anchored.

---

## The Arc So Far

LIARA did not begin as a plan for a large AI platform.

Its path can be summarized more accurately like this:

```text
Cortana-inspired assistant experiments
    ↓
Building a personal digital assistant
    ↓
Nephy — identity and continuity
    ↓
A desire for durable memory
    ↓
Liara — independent Home-&-Life-Assistant
    ↓
L.I.A.R.A. — Local Intelligent Autonomous Reasoning Assistant
    ↓
Semantic relationships
    ↓
Multiple identities and roles
    ↓
LIARA — orchestration
    ↓
Tools + Evidence
    ↓
Judge + Validator
    ↓
Self-observation + Dreaming
    ↓
Governance
    ↓
DDNA
    ↓
LiNeP + Foundation
    ↓
A model-independent AI architecture
```

The important transition is not from "small software" to "large software."

It is from asking:

**"What kind of digital assistant could I build myself?"**

through:

**"How can an AI remember me and remain recognizable over time?"**

to asking:

**"How can an evolving intelligent system preserve meaning, identity, evidence, relationships and legitimate boundaries over time?"**

That question is still open.

LIARA is one attempt to explore it in code.

---

## Next

```text
.oO(...)
```

**The timeline continues.**
