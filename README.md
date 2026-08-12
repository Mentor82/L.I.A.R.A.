# LIARA — Model-Independent AI Orchestration Architecture

> **Liara learns. Liara remembers. Liara grows.**

LIARA is an experimental, model-independent AI orchestration architecture for combining **inference, persistent memory, semantic relationships, tools, reasoning control, validation, evidence and governed evolution** in one auditable system.

LIARA is not a single LLM and its identity is not tied to one model, provider, accelerator or user interface. Models are computational resources inside a larger architecture. The long-term goal is a system that can change its models, hardware and capabilities while preserving meaningful continuity, provenance and architectural boundaries.

> **Current status:** LIARA is locally operational and under active development. It is **not production-ready**. Authentication, governance enforcement, configuration hardening and other documented gaps remain open.

## Why LIARA?

Most AI applications begin with a model and add tools or memory around it. LIARA approaches the problem from the opposite direction:

**What must remain around the model so that an intelligent system can remember, reason, act, verify and evolve without becoming dependent on one model?**

That leads to several core ideas:

- **Model independence** — inference providers and hardware are replaceable resources.
- **Context ≠ Memory** — current working context is assembled for a task; persistent memory has provenance and lifecycle.
- **Relationships matter** — vector similarity and explicit graph relations complement factual storage.
- **Generation is not proof** — Judge and Validator responsibilities are separated from generation.
- **Actions require evidence** — intended action, executed operation and observed state change are distinct.
- **Evolution is governed** — self-observation and improvement proposals do not imply unrestricted self-modification.
- **Identity is architectural** — DDNA describes continuity beyond a particular model, voice, avatar or machine.

## Architecture at a Glance

```text
Client / Frontend / CLI
          │
          ▼
      LIARA API
          │
          ▼
┌───────────────────────────────┐
│        ORCHESTRATOR           │
│                               │
│  Input Situation Profile      │
│          ↓                    │
│  Librarian / Context          │
│          ↓                    │
│  Router / Planner             │
│          ↓                    │
│  Tool Discovery / Execution   │
│          ↓                    │
│  Generation / Inference       │
│          ↓                    │
│  Validation / Judge           │
│          ↓                    │
│  Memory Commit                │
└───────────────────────────────┘
      │        │        │
      ▼        ▼        ▼
   Memory    Tools    Inference
      │                 │
      ▼                 ▼
Postgres / Redis     CPU / GPU / NPU
Qdrant / Chroma      local / external
Neo4j
```

The pipeline order is intentional. Retrieval, action, generation, validation and persistence are separate responsibilities with traceable boundaries.

## Core Components

### `services/api/` — API boundary

The API is the canonical external entry point. Feature routers currently cover system health, chat and streaming, tools, governance, speech, compute, operations and artifacts.

```text
routers/
├── system.py
├── chat.py
├── tools.py
├── governance.py
├── speech.py
├── compute.py
├── operations.py
└── artifacts.py
```

The API boundary should expose contracts rather than contain model or tool decision logic.

### `services/orchestrator/` — coordination kernel

The Orchestrator coordinates the runtime pipeline while specialized modules own cohesive responsibilities:

```text
orchestrator.py        Coordinator & compatibility facade
reasoning_control.py   Belief, Utility, Stability, Decision & hybrid control
librarian_pipeline.py  History, Facts, Vector, Graph & memory persistence
tool_discovery.py      Tool selection, execution & discovery
generation_pipeline.py Inference, prompting, fallback, validation & Judge trace
input_profiler.py      Input situation profiling
router.py / planner.py Routing and execution planning
```

The previous 4,657-line / 89-method Orchestrator monolith was modularized while preserving request/response contracts, pipeline order and legacy method compatibility.

### `services/memory/` — persistent semantic memory

LIARA does not treat chat history as the complete memory model. The memory architecture combines multiple forms of persistence and retrieval:

- **PostgreSQL** — structured and factual persistence
- **Redis** — transient/runtime state
- **Qdrant / Chroma** — semantic vector retrieval
- **Neo4j** — explicit structural relationships

The Librarian assembles relevant information into working context rather than blindly injecting stored history.

### `services/inference/` — model and hardware abstraction

The inference layer separates LIARA from individual model providers and execution hardware.

Current provider abstractions include Ollama, OpenVINO, OpenAI-compatible providers and vLLM-oriented paths. Routing can use CPU, GPU and NPU resources according to the selected workload and runtime configuration.

### `services/tools/` — governed capability

Tools extend what LIARA can observe or do, but capability and authority are intentionally separate concepts.

Tool execution is designed around traceability and evidence. A successful function return is not automatically equivalent to a verified real-world mutation.

```text
intended action
    !=
executed operation
    !=
observed state change
```

### Validator & Judge — trust boundaries

LIARA treats generated output as a candidate result, not automatic truth.

- **Validator** checks contracts, rules, policy and output eligibility.
- **Judge** evaluates quality, plausibility and goal achievement.

This separation also applies to system changes: self-inspection and proposal generation do not grant the system unrestricted authority to approve its own mutations.

## DDNA — Digital DNA

**DDNA** is LIARA's concept for the enduring identity of a digital system.

It is not a model checkpoint, source tree or avatar. DDNA describes the combination of foundational principles, relational structure, accumulated imprinting and evolution rules that preserve continuity while still allowing the system to grow.

This creates a useful distinction:

```text
Model / Hardware / Voice / Avatar
        can change
             │
             ▼
Expression and capability change
             │
             ▼
DDNA preserves architectural continuity
```

Temporary or context-dependent changes can therefore be understood as expressions layered over a more stable identity rather than automatic rewrites of that identity.

## Governance and the LIARA Foundation Concept

As a system gains tools, self-observation and adaptation mechanisms, technical capability alone is not a sufficient authority model.

LIARA therefore distinguishes multiple levels:

```text
Object level       actions / variants / implementations
Meta level         selection / validation / audit
Meta-meta level    governance / legitimacy / constitutional rules
External layer     LIARA Foundation concept
```

The **LIARA Foundation** is conceived as an external constitutional and interoperability layer: a shared set of rules that should not be freely rewritten by the current state of one LIARA instance.

## LiNeP

**LiNeP** addresses coordination of distributed resources, workers, slots and heartbeats without becoming a second semantic Orchestrator.

> **LiNeP connects nodes. LIARA understands meaning.**

Keeping these responsibilities separate prevents resource scheduling, semantic reasoning and governance from collapsing into one subsystem.

## Quick Start

### Python environment

```bash
# Minimal / sandbox
pip install -r requirements-sandbox.txt

# Core + database/vector/graph backends
pip install -r requirements-core.txt -r requirements-db.txt

# Development / tests
pip install -r requirements-dev.txt
```

`pyproject.toml` extras are also available (`.[db]`, `.[optional]`, `.[all]`, `.[dev]`).

### Infrastructure

For local store integration:

```powershell
docker compose up -d
```

API and Memory can be managed as host services with the repository's service tooling. See the documentation index for the current operational procedure.

### CLI

```bash
python -m services.cli.main chat "Wie spaet ist es?"
python -m services.cli.main stream "Erklaer mir den aktuellen Status"
python -m services.cli.main repl

# machine-readable
python -m services.cli.main --output json health
```

### Server management GUI

```bash
python server_management_gui.py
```

For the native C/GTK4 server manager and packaging details, see [`frontend/server-manager/README.md`](frontend/server-manager/README.md).

## Current Verified Baseline

The architecture was modularized and cold-start/live-tested before the current baseline was tagged.

```text
46 / 46   Orchestrator Unit Tests       PASSED
179 / 179 API & Memory Tests            PASSED
14 / 14   Full-System Live Tests        PASSED
8 / 8     Post-Restart Acceptance       PASSED
```

Baseline tag:

**`v2.1.0-refactor-baseline`**

These numbers describe that verified baseline; they are not a permanent claim that every future commit has the same result.

## Project History

LIARA did not begin as a plan for a large AI platform. Its roots go back to early Cortana-inspired personal-assistant experiments, followed by Nephy and the questions of identity, continuity and persistent memory. Those questions gradually expanded into semantic relationships, orchestration, evidence, governance and DDNA.

The reconstructed development history is documented separately:

**[`LIARA_TIMELINE.md`](LIARA_TIMELINE.md) — The Story Behind LIARA**

The timeline distinguishes documented events, reconstructed phases and conceptual turning points rather than pretending the architecture appeared fully formed.

## Documentation

Start here for the code-based current state and detailed subsystem documentation:

- [`docs/00_index.md`](docs/00_index.md) — documentation index / current-state handoff
- [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — API reference
- [`docs/SERVER_MANAGEMENT_GUI.md`](docs/SERVER_MANAGEMENT_GUI.md) — server management
- [`docs/WSL_SESSION_RUNTIME.md`](docs/WSL_SESSION_RUNTIME.md) — native WSL test and simulation sessions
- [`docs/09_reference/SYS_AUDIT.md`](docs/09_reference/SYS_AUDIT.md) — `/sys` audit and traceability
- [`LIARA_TIMELINE.md`](LIARA_TIMELINE.md) — project origin and evolution

Detailed validator execution modes, environment variables, CLI options, audit tooling and operational procedures belong in the subsystem documentation rather than being duplicated in this overview.

## Design Principles

1. **Models are replaceable resources.**
2. **Context and persistent memory are different things.**
3. **Relationships are first-class information.**
4. **Tools require contracts, authority and evidence.**
5. **Generation is not validation.**
6. **The Orchestrator coordinates; specialized modules own responsibilities.**
7. **Communication crosses explicit schemas and contracts.**
8. **Self-observation does not imply unrestricted self-modification.**
9. **Evolution should preserve provenance and DDNA invariants.**

## Development Status

LIARA is an active experimental architecture. The repository contains working local services and verified integration paths, but the project should not yet be represented as a hardened production platform.

The codebase is currently organized around the canonical `services/` structure, with further service/worker decoupling and governance hardening remaining architectural work.

---

**LIARA is not defined by the model currently answering.**

It is defined by the architecture that decides what to remember, what to use, what to trust, what may act — and what must remain when everything else changes.

`.oO(...)`
