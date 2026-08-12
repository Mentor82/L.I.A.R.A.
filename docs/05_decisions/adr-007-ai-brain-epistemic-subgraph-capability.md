# ADR-007: AI-Brain Epistemic Subgraph & Visitor Pass Capability Paradigm

**Status**: Accepted & Frozen  
**Date**: 2026-08-12  
**Deciders**: Mirko, Nephy, LIARA Architecture Core  
**Scope**: Standalone `ai-brain` service, external AI agent interaction, Knowledge Graph schema, & Capability security invariants  

---

## Context and Problem Statement

When external AI agents (e.g., ChatGPT Custom GPTs, Copilot, Claude, or third-party autonomous workers) interact with a user's personal knowledge or system history, traditional approaches rely on **RAG (Retrieval-Augmented Generation)** — pushing matching text chunks into the prompt context.

This introduces three critical failure modes:
1. **Lack of Provenance & Epistemic Grounding**: The external AI cannot distinguish between a user's confirmation, a verified evidence-backed relation, an AI's unconfirmed inference, a historical version, or a speculative hypothesis.
2. **Post-Hoc Redaction Risks**: Traditional security redacts sensitive information *after* retrieval, leaving room for leaking transient data or context contamination.
3. **Blanket Memory Access**: Giving an external AI raw database access grants all-or-nothing privileges instead of scoped, temporary, attenuating thinking rights.

---

## Core Architectural Invariants

### Invariant 1: Traversal-Level Authorization
> **"Authorization constrains retrieval, not merely presentation."**

Cypher graph traversals (Neo4j) and vector similarity searches (Qdrant) MUST enforce visibility filters (`visibility == "shared"`) and scope checks **inside the database query filter**. Private or unauthorized nodes/edges are NEVER retrieved into transient pipeline memory.

### Invariant 2: Capability Attenuation
> **"Capabilities may attenuate, never amplify."**

Any derived, delegated, or sub-issued Visitor Pass Token can only diminish privileges (`max_hops <= parent.max_hops`, `scopes ⊆ parent.scopes`, `allowed_epistemic_states ⊆ parent.allowed_epistemic_states`). A sub-pass can NEVER expand or amplify capabilities. This guarantees safe multi-agent delegation chains (**AI A ➔ LIARA ➔ AI B**).

---

## The 5-Pillar Relational & Epistemic Schema

Every node and edge in the knowledge graph is qualified along 5 dimensions:

1. **Semantisch**: `RELATES_TO`, `SUPPORTS`, `CONTRADICTS`, `REFINES`, `EXPLAINS`, `DERIVED_FROM`, `SIMILAR_TO`
2. **Zeit/Evolution**: `PRECEDES`, `EVOLVED_INTO`, `SUPERSEDES`, `REVISITS`, `CURRENT_VERSION_OF`
3. **System/Projekt**: `PART_OF`, `DEPENDS_ON`, `IMPLEMENTS`, `GOVERNS`, `VALIDATES`, `USES`, `PRODUCED_BY`
4. **Persönlicher Denkraum**: `INSPIRED_BY`, `ASSOCIATED_WITH`, `PREFERENCE_FOR`, `GOAL_OF`, `DECISION_ABOUT`, `OPEN_QUESTION`, `IDEA_FOR`
5. **Epistemic State**:
   - `USER_CONFIRMED`: Statement explicitly confirmed by the authoritative user. Confirmation establishes provenance and user authority, not eternal factual correctness.
   - `VERIFIED`: Proven relation backed by empirical evidence or test verification.
   - `INFERENCE`: Algorithmic AI inference (must remain tagged as inference).
   - `HYPOTHESIS`: Exploratory or speculative statement.
   - `CONTRADICTED`: Delivered with explicit counter-evidence.
   - `SUPERSEDED`: Historically visible, but marked as non-current state.

---

## The Visitor Pass Capability Paradigm

A session authorization generates a `VisitorPassToken` with explicit **Subject & Audience Binding**:

```json
{
  "token_id": "vpass_8f21a4e9",
  "subject": "external-agent-session-chatgpt-gpt4o-8f92",
  "audience": "ai-brain.liara.mw-dresden.de",
  "scopes": ["facts:read", "relations:read", "projects:read"],
  "allowed_epistemic_states": ["USER_CONFIRMED", "VERIFIED", "INFERENCE"],
  "max_hops": 2,
  "visibility": "shared",
  "ttl_seconds": 1800,
  "created_at": "2026-08-12T17:09:00Z"
}
```

External AI agents use this token to query `POST /ai-brain/subgraph/bounded`, receiving a **bounded, provenance-backed, epistemic-tagged semantic subgraph** rather than raw text dumps.

---

## Self-Describing Discovery

External AI agents discover gateway capabilities via HATEOAS root endpoints:

```text
GET /ai-brain/
GET /ai-brain/capabilities
```

Returning:
- Supported API versions & HATEOAS links.
- Session authorization endpoints (`POST /ai-brain/session/authorize`).
- Bounded subgraph retrieval endpoints (`POST /ai-brain/subgraph/bounded`).
- OpenAPI specification link (`GET /ai-brain/openapi.json`).
