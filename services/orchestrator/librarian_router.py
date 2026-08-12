"""Upfront librarian routing for memory-aware context selection.

This router classifies a query before context assembly so the orchestrator can
prefer facts, semantic memory, relations, or working context explicitly instead
of merging everything into one undifferentiated retrieval path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .gap_detector import GapAction

if TYPE_CHECKING:
    from services.contracts import InputSituationProfile


@dataclass(frozen=True)
class LibrarianDecision:
    route: str
    reason: str
    primary_source: str
    fact_key: str | None = None
    fact_namespaces: list[str] = field(default_factory=list)
    load_context: bool = False
    load_relations: bool = False
    load_retrieval: bool = False
    load_facts: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

class LibrarianRouter:
    """Classify a query into a primary retrieval lane before prompt building."""

    def route(
        self,
        *,
        query: str,
        gap_action: str | None = None,
        force_context: bool = False,
        conversation_history: str = "",
        session_id: str = "",
        user_id: str = "",
        input_profile: "InputSituationProfile | None" = None,
    ) -> LibrarianDecision:
        _ = query
        _ = session_id
        _ = user_id

        if gap_action == GapAction.LOAD_FACTS.value:
            return self._fact_decision(
                reason="gap_action_load_facts",
                forced=force_context,
            )
        if gap_action == GapAction.LOAD_MEMORY.value:
            return LibrarianDecision(
                route="SEMANTIC_MEMORY",
                reason="gap_action_load_memory",
                primary_source="qdrant",
                load_retrieval=True,
                load_context=force_context,
                metadata={"gap_action": gap_action},
            )
        if gap_action == GapAction.LOAD_RELATIONS.value:
            return LibrarianDecision(
                route="RELATION_LOOKUP",
                reason="gap_action_load_relations",
                primary_source="neo4j",
                load_relations=True,
                load_facts=force_context,
                metadata={"gap_action": gap_action},
            )
        if gap_action == GapAction.LOAD_CONTEXT.value:
            return LibrarianDecision(
                route="RUN_CONTEXT",
                reason="gap_action_load_context",
                primary_source="chroma",
                load_context=True,
                load_relations=force_context,
                metadata={"gap_action": gap_action},
            )
        if gap_action == GapAction.LOAD_SESSION.value:
            return LibrarianDecision(
                route="SESSION_RECALL",
                reason="gap_action_load_session",
                primary_source="postgres",
                metadata={"gap_action": gap_action, "history_available": bool(conversation_history.strip())},
            )

        if force_context:
            return LibrarianDecision(
                route="RUN_CONTEXT",
                reason="force_context",
                primary_source="chroma",
                load_context=True,
                load_relations=True,
            )

        # The input profiler already knows whether a turn concerns LIARA's
        # own architecture.  Carry that evidence into retrieval instead of
        # discarding it and relying on the generic Qdrant-only default.  This
        # broadens only the read path; it grants no tool or mutation rights.
        profile_topics = {
            str(topic).strip().lower()
            for topic in (getattr(input_profile, "topics", None) or [])
            if str(topic).strip()
        }
        profile_domain = str(getattr(input_profile, "domain", "") or "").strip().lower()
        profile_confidence = float(getattr(input_profile, "confidence", 0.0) or 0.0)
        internal_architecture_context = (
            input_profile is not None
            and profile_domain in {"ai_architecture", "software"}
            and "liara" in profile_topics
            and profile_confidence >= 0.55
        )
        if internal_architecture_context:
            return LibrarianDecision(
                route="SEMANTIC_MEMORY",
                reason="input_profile_internal_architecture",
                primary_source="qdrant",
                load_retrieval=True,
                load_context=True,
                load_relations=True,
                metadata={
                    "profile_domain": profile_domain,
                    "profile_topics": sorted(profile_topics),
                    "profile_confidence": round(profile_confidence, 3),
                    "read_only_expansion": True,
                },
            )

        # Default route: semantic memory without text-keyword hardcoding.
        return LibrarianDecision(
            route="SEMANTIC_MEMORY",
            reason="default_semantic_memory",
            primary_source="qdrant",
            load_retrieval=True,
            load_context=force_context,
        )

    def _fact_decision(
        self,
        *,
        reason: str,
        forced: bool,
    ) -> LibrarianDecision:
        return LibrarianDecision(
            route="FACT_LOOKUP",
            reason=reason,
            primary_source="postgres",
            fact_key=None,
            fact_namespaces=["global"],
            load_facts=True,
            load_relations=forced,
            metadata={
                "fact_key": "",
                "namespace_strategy": "global_only",
            },
        )

