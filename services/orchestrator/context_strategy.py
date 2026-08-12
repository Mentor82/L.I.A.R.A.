"""Context Strategy module for LIARA orchestrator.

Per ARCHITECTURE.md, the Context Strategy is the central decision layer
that determines WHICH memory tiers to load, at what scope, and in what order.

Routing decision tree (simplified):
  if query_type == "simple":       use_tool()
  elif query_type == "context":    scope_search(chroma, scope)
  elif query_type == "memory":     semantic_search(qdrant)
  elif query_type == "deep":       build_full_context()

This module formalises that routing as a standalone, testable component.
The orchestrator delegates tier-selection to this module; the actual I/O
still happens in orchestrator._load_librarian_context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class QueryType(str, Enum):
    """Canonical query types per architecture context strategy."""
    SIMPLE   = "simple"     # deterministic: tool answer, no LLM needed
    CONTEXT  = "context"    # scoped short-term context (Chroma + scope filter)
    MEMORY   = "memory"     # long-term semantic retrieval (Qdrant)
    FACT     = "fact"       # explicit factual lookup (Postgres)
    RELATION = "relation"   # graph-based relationship lookup (Neo4j)
    DEEP     = "deep"       # all tiers combined for complex reasoning


class TierPriority(str, Enum):
    """Load priority used by the orchestrator when assembling context."""
    PRIMARY   = "primary"
    SECONDARY = "secondary"
    FALLBACK  = "fallback"
    SKIP      = "skip"


@dataclass(frozen=True)
class TierDirective:
    """Instruction for a single memory tier."""
    tier: str              # 'chroma' | 'qdrant' | 'postgres' | 'neo4j' | 'redis'
    priority: TierPriority
    load: bool
    top_k: int             # max items to retrieve
    scope_filter: bool     # whether to apply session/run scope filter
    reason: str


@dataclass(frozen=True)
class ContextScope:
    """Resolved scope parameters passed to context_search."""
    session_id: str = ""
    run_id: str = ""
    file_path: str = ""
    symbol: str = ""
    time_window: int = 0   # seconds; 0 = no window


@dataclass
class ContextStrategyPlan:
    """Complete context strategy plan for a single orchestrator run step."""
    query_type: QueryType
    tiers: List[TierDirective] = field(default_factory=list)
    scope: ContextScope = field(default_factory=ContextScope)
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def primary_tier(self) -> TierDirective | None:
        for t in self.tiers:
            if t.priority == TierPriority.PRIMARY:
                return t
        return None

    @property
    def active_tiers(self) -> List[TierDirective]:
        return [t for t in self.tiers if t.load]

    @property
    def load_chroma(self) -> bool:
        return any(t.tier == "chroma" and t.load for t in self.tiers)

    @property
    def load_qdrant(self) -> bool:
        return any(t.tier == "qdrant" and t.load for t in self.tiers)

    @property
    def load_facts(self) -> bool:
        return any(t.tier == "postgres" and t.load for t in self.tiers)

    @property
    def load_relations(self) -> bool:
        return any(t.tier == "neo4j" and t.load for t in self.tiers)

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "query_type": self.query_type.value,
            "reasoning": self.reasoning,
            "scope": {
                "session_id": bool(self.scope.session_id),
                "run_id": bool(self.scope.run_id),
                "time_window": self.scope.time_window,
            },
            "tiers": [
                {
                    "tier": t.tier,
                    "priority": t.priority.value,
                    "load": t.load,
                    "top_k": t.top_k,
                    "scope_filter": t.scope_filter,
                }
                for t in self.tiers
            ],
        }


def _tier(
    tier: str,
    *,
    priority: TierPriority,
    load: bool,
    top_k: int = 3,
    scope_filter: bool = False,
    reason: str = "",
) -> TierDirective:
    return TierDirective(
        tier=tier,
        priority=priority,
        load=load,
        top_k=top_k,
        scope_filter=scope_filter,
        reason=reason,
    )


class ContextStrategyResolver:
    """Resolve which memory tiers to load and at what priority.

    Maps LibrarianDecision routes to explicit ContextStrategyPlans.
    Implements the routing tree from ARCHITECTURE.md § Context Strategy.

    Usage::
        resolver = ContextStrategyResolver()
        plan = resolver.resolve(
            route="FACT_LOOKUP",
            session_id=session_id,
            run_id=run_id,
            force_context=False,
            load_flags={"load_facts": True},
        )
    """

    # Route → QueryType mapping (from LibrarianRouter routes)
    _ROUTE_TO_QUERY_TYPE: Dict[str, QueryType] = {
        "FACT_LOOKUP": QueryType.FACT,
        "SESSION_RECALL": QueryType.CONTEXT,
        "SEMANTIC_MEMORY": QueryType.MEMORY,
        "RUN_CONTEXT": QueryType.CONTEXT,
        "RELATION_LOOKUP": QueryType.RELATION,
    }

    def resolve(
        self,
        *,
        route: str,
        session_id: str = "",
        run_id: str = "",
        force_context: bool = False,
        load_flags: Dict[str, bool] | None = None,
        top_k: int = 3,
    ) -> ContextStrategyPlan:
        """Build a ContextStrategyPlan from a LibrarianRouter route.

        Args:
            route:         LibrarianDecision.route string.
            session_id:    Active session identifier for scope resolution.
            run_id:        Active run identifier for scope resolution.
            force_context: True when a validation-retry gap forces broader loading.
            load_flags:    Explicit load_* booleans from LibrarianDecision.
            top_k:         Base number of items to fetch per tier.
        """
        flags = load_flags or {}
        query_type = self._ROUTE_TO_QUERY_TYPE.get(route, QueryType.DEEP)
        scope = ContextScope(session_id=session_id, run_id=run_id)
        expanded_top_k = top_k + 2 if force_context else top_k

        if query_type == QueryType.FACT:
            return self._plan_fact(scope, flags, expanded_top_k, force_context)

        if route == "SESSION_RECALL":
            return self._plan_session_recall(scope, flags, expanded_top_k, force_context)

        if route == "RUN_CONTEXT":
            return self._plan_run_context(scope, flags, expanded_top_k, force_context)

        if query_type == QueryType.RELATION:
            return self._plan_relation(scope, flags, expanded_top_k, force_context)

        if query_type == QueryType.MEMORY:
            return self._plan_semantic_memory(scope, flags, expanded_top_k, force_context)

        # Default: DEEP — all tiers
        return self._plan_deep(scope, expanded_top_k)

    # ── plan builders ───────────────────────────────────────────────────────

    @staticmethod
    def _plan_fact(
        scope: ContextScope,
        flags: Dict[str, bool],
        top_k: int,
        force: bool,
    ) -> ContextStrategyPlan:
        return ContextStrategyPlan(
            query_type=QueryType.FACT,
            scope=scope,
            reasoning="FACT_LOOKUP: postgres facts are primary source of truth",
            tiers=[
                _tier("postgres", priority=TierPriority.PRIMARY,   load=True,          top_k=top_k, reason="fact_primary"),
                _tier("chroma",   priority=TierPriority.SECONDARY, load=force,         top_k=3,     scope_filter=True, reason="context_fallback"),
                _tier("qdrant",   priority=TierPriority.FALLBACK,  load=False,         top_k=0),
                _tier("neo4j",    priority=TierPriority.FALLBACK,  load=flags.get("load_relations", False), top_k=2),
                _tier("redis",    priority=TierPriority.SKIP,      load=False,         top_k=0),
            ],
        )

    @staticmethod
    def _plan_session_recall(
        scope: ContextScope,
        flags: Dict[str, bool],
        top_k: int,
        force: bool,
    ) -> ContextStrategyPlan:
        return ContextStrategyPlan(
            query_type=QueryType.CONTEXT,
            scope=scope,
            reasoning="SESSION_RECALL: conversation history is primary; facts as supplement",
            tiers=[
                _tier("postgres", priority=TierPriority.PRIMARY,   load=True,  top_k=top_k, reason="history_primary"),
                _tier("chroma",   priority=TierPriority.SECONDARY, load=force, top_k=3,     scope_filter=True, reason="context_supplement"),
                _tier("qdrant",   priority=TierPriority.FALLBACK,  load=False, top_k=0),
                _tier("neo4j",    priority=TierPriority.FALLBACK,  load=flags.get("load_relations", False), top_k=2, reason="relations_supplement"),
                _tier("redis",    priority=TierPriority.SKIP,      load=False, top_k=0),
            ],
        )

    @staticmethod
    def _plan_run_context(
        scope: ContextScope,
        flags: Dict[str, bool],
        top_k: int,
        force: bool,
    ) -> ContextStrategyPlan:
        return ContextStrategyPlan(
            query_type=QueryType.CONTEXT,
            scope=scope,
            reasoning="RUN_CONTEXT: Chroma scope-filtered context is primary",
            tiers=[
                _tier("chroma",   priority=TierPriority.PRIMARY,   load=True,  top_k=top_k, scope_filter=True, reason="run_context_primary"),
                _tier("neo4j",    priority=TierPriority.SECONDARY, load=flags.get("load_relations", force), top_k=3, reason="relations_supplement"),
                _tier("postgres", priority=TierPriority.FALLBACK,  load=False, top_k=0),
                _tier("qdrant",   priority=TierPriority.FALLBACK,  load=False, top_k=0),
                _tier("redis",    priority=TierPriority.SKIP,      load=False, top_k=0),
            ],
        )

    @staticmethod
    def _plan_relation(
        scope: ContextScope,
        flags: Dict[str, bool],
        top_k: int,
        force: bool,
    ) -> ContextStrategyPlan:
        return ContextStrategyPlan(
            query_type=QueryType.RELATION,
            scope=scope,
            reasoning="RELATION_LOOKUP: Neo4j graph traversal is primary source",
            tiers=[
                _tier("neo4j",    priority=TierPriority.PRIMARY,   load=True,  top_k=top_k, reason="relation_primary"),
                _tier("postgres", priority=TierPriority.SECONDARY, load=flags.get("load_facts", force), top_k=3, reason="facts_supplement"),
                _tier("chroma",   priority=TierPriority.FALLBACK,  load=False, top_k=0),
                _tier("qdrant",   priority=TierPriority.FALLBACK,  load=False, top_k=0),
                _tier("redis",    priority=TierPriority.SKIP,      load=False, top_k=0),
            ],
        )

    @staticmethod
    def _plan_semantic_memory(
        scope: ContextScope,
        flags: Dict[str, bool],
        top_k: int,
        force: bool,
    ) -> ContextStrategyPlan:
        return ContextStrategyPlan(
            query_type=QueryType.MEMORY,
            scope=scope,
            reasoning="SEMANTIC_MEMORY: Qdrant long-term semantic retrieval is primary",
            tiers=[
                _tier("qdrant",   priority=TierPriority.PRIMARY,   load=True,  top_k=top_k, reason="semantic_primary"),
                _tier("chroma",   priority=TierPriority.SECONDARY, load=flags.get("load_context", force), top_k=3, scope_filter=True, reason="context_supplement"),
                _tier("postgres", priority=TierPriority.FALLBACK,  load=False, top_k=0),
                _tier("neo4j",    priority=TierPriority.FALLBACK,  load=flags.get("load_relations", False), top_k=2, reason="relations_supplement"),
                _tier("redis",    priority=TierPriority.SKIP,      load=False, top_k=0),
            ],
        )

    @staticmethod
    def _plan_deep(scope: ContextScope, top_k: int) -> ContextStrategyPlan:
        return ContextStrategyPlan(
            query_type=QueryType.DEEP,
            scope=scope,
            reasoning="DEEP: all available tiers loaded for complex multi-hop reasoning",
            tiers=[
                _tier("postgres", priority=TierPriority.PRIMARY,   load=True, top_k=top_k, reason="facts_primary"),
                _tier("chroma",   priority=TierPriority.PRIMARY,   load=True, top_k=top_k, scope_filter=True, reason="context_primary"),
                _tier("qdrant",   priority=TierPriority.PRIMARY,   load=True, top_k=top_k, reason="semantic_primary"),
                _tier("neo4j",    priority=TierPriority.SECONDARY, load=True, top_k=4,     reason="relations_secondary"),
                _tier("redis",    priority=TierPriority.SKIP,      load=False, top_k=0),
            ],
        )
