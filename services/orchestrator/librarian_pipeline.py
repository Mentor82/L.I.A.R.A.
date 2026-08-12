"""Librarian Context & Relation Pipeline Submodule for LIARA Orchestrator.

Handles:
- Loading Librarian context (Session, Fact, Retrieval, Graph)
- Vector embedding queries & candidate reranking
- Temporary & working context document upserts
- Structural semantic triple extraction & RELATION_EDGE graph persistence
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from services.contracts import (
    ContextScope,
    ContextUpsertRequest,
    MemoryFactQueryRequest,
    MemoryHistoryQueryRequest,
    MemoryRetrievalQueryRequest,
    RelationExpandRequest,
    RelationType,
    RelationUpsertRequest,
)
from services.shared.types import MemoryTier
from .librarian_router import LibrarianDecision, LibrarianRouter
from .defs.context_channels import merge_context_channels, load_conversation_history
from .defs.embedding_query import (
    infer_active_topic,
    summarize_history_for_embedding,
    compact_embedding_text,
    build_embedding_query,
    rewrite_retrieval_query,
)
from .defs.context_upsert import (
    build_context_upsert_metadata,
    is_safe_for_context_upsert,
    touch_working_context_activity,
    upsert_temp_context_note,
    upsert_working_context_doc,
)
from .defs.context_formatting import format_tool_context, build_working_context_summary
from .defs.relation_keys import relation_node_key

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

_LOGGER = logging.getLogger("liara.orchestrator.librarian_pipeline")
_TEMP_CONTEXT_TTL_SECONDS = 3600
_RECALL_REFRESH_CONFIDENCE_THRESHOLD = 0.58
_SESSION_TOPIC_SWITCH_OVERLAP_MIN = 0.30


def graph_priority_guardrail_line(orchestrator: Orchestrator, session_id: Optional[str] = None) -> str:
    """Generate graph priority guardrail string if available."""
    if hasattr(orchestrator, "memory") and hasattr(orchestrator.memory, "graph_store"):
        try:
            if hasattr(orchestrator.memory.graph_store, "get_priority_guardrail"):
                val = orchestrator.memory.graph_store.get_priority_guardrail(session_id)
                if val:
                    return str(val)
        except Exception as exc:
            _LOGGER.debug("Graph priority guardrail check failed: %s", exc)
    return "[graph_guardrail] Direct graph relations are authoritative: precedence over semantic vector similarity."


def retrieval_rerank(
    orchestrator: Orchestrator,
    *,
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Re-rank vector retrieval candidates by composite relevance & recency score."""
    if not candidates:
        return []

    q_terms = set(re.findall(r"\w+", query.lower()))
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for cand in candidates:
        text = str(cand.get("content") or cand.get("text") or "").lower()
        t_terms = set(re.findall(r"\w+", text))

        overlap = len(q_terms & t_terms) / max(1, len(q_terms))
        sim = float(cand.get("similarity") or cand.get("score") or 0.0)

        composite = 0.6 * sim + 0.4 * overlap
        scored.append((composite, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def should_run_recall_refresh(
    orchestrator: Orchestrator,
    *,
    decision: LibrarianDecision,
    user_message: str,
    active_topic: Optional[str],
) -> bool:
    """Determine if a recall refresh should be triggered for active context."""
    if not decision.needs_history:
        return False

    if decision.confidence < _RECALL_REFRESH_CONFIDENCE_THRESHOLD:
        return True

    if active_topic and user_message:
        u_terms = set(re.findall(r"\w+", user_message.lower()))
        t_terms = set(re.findall(r"\w+", active_topic.lower()))
        if u_terms and t_terms:
            overlap = len(u_terms & t_terms) / max(1, len(t_terms))
            if overlap < _SESSION_TOPIC_SWITCH_OVERLAP_MIN:
                return True

    return False


def extract_graph_relations_from_context(relation_context: str) -> List[Dict[str, str]]:
    relation_re = re.compile(
        r"\[relation\]\s*(?P<source>.+?)\s*-\[(?P<relation>[^\]]+)\]->\s*(?P<target>.+)",
        re.IGNORECASE,
    )
    relations: List[Dict[str, str]] = []
    for match in relation_re.finditer(relation_context or ""):
        source = match.group("source").strip()
        relation = match.group("relation").strip()
        target = match.group("target").strip()
        if source and relation and target:
            relations.append({"source": source, "relation": relation, "target": target})
    return relations


async def load_librarian_context(
    orchestrator: Orchestrator,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    query: str = "",
    librarian_decision: Optional[Any] = None,
    librarian: Optional[Any] = None,
    run_id: Optional[str] = None,
    conversation_history: Optional[str] = None,
    force_context: bool = False,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Load context across explicit channels selected by the Librarian."""
    decision = librarian_decision or librarian or LibrarianDecision(
        route="AUTO",
        reason="default",
        primary_source="memory",
        load_context=True,
        load_facts=True,
        load_retrieval=True,
        load_relations=False,
    )

    channels: Dict[str, Any] = {
        "history": [],
        "facts": [],
        "vector": [],
        "graph": [],
    }

    if not session_id and not user_id and not query:
        counts = {k: 0 for k in channels}
        return channels, counts

    # 1. History
    if getattr(decision, "load_context", True):
        try:
            hist_req = MemoryHistoryQueryRequest(session_id=session_id or "", limit=10)
            hist_res = await orchestrator.memory.query_history(hist_req)
            if hist_res and hasattr(hist_res, "items"):
                channels["history"] = hist_res.items
        except Exception as exc:
            _LOGGER.warning("Failed to load history context: %s", exc)

    # 2. Facts
    if getattr(decision, "load_facts", True) or getattr(decision, "route", "") == "FACT_LOOKUP":
        try:
            fact_namespaces = list(getattr(decision, "fact_namespaces", None) or (["global", f"user:{user_id}"] if user_id else ["global"]))
            fact_key = getattr(decision, "fact_key", None)
            seen_fact_rows = set()
            fact_rows = []
            all_facts = []

            for namespace in fact_namespaces:
                fact_req = MemoryFactQueryRequest(namespace=namespace, key=fact_key, limit=10)
                fact_res = await orchestrator.memory.query_facts(fact_req)
                items = getattr(fact_res, "items", getattr(fact_res, "facts", [])) or []
                for item in items:
                    row_id = (namespace, getattr(item, "key", ""), str(getattr(item, "value", "")))
                    if row_id in seen_fact_rows:
                        continue
                    seen_fact_rows.add(row_id)
                    all_facts.append(item)

                    raw_status = getattr(item, "status", None)
                    status_str = getattr(raw_status, "value", raw_status) or "verified"
                    status = str(status_str).lower()
                    fact_rows.append((namespace, getattr(item, "key", ""), str(getattr(item, "value", "")), status))

            verified_rows = [r for r in fact_rows if r[3] == "verified"]
            candidate_rows = [r for r in fact_rows if r[3] not in {"verified", "staged", "deprecated", "revoked"}]

            fact_lines = []
            if verified_rows:
                for namespace, key, value, _status in verified_rows:
                    fact_lines.append(f"[fact_verified:{namespace}] {key}: {value[:220]}")
            elif candidate_rows:
                for namespace, key, value, status in candidate_rows:
                    fact_lines.append(f"[fact_hint:{namespace}:{status}] {key}: {value[:220]}")

            channels["fact_context"] = "\n".join(fact_lines)
            channels["facts"] = all_facts
            counts["facts"] = len(seen_fact_rows)
        except Exception as exc:
            _LOGGER.warning("Failed to load fact context: %s", exc)

    if "fact_context" not in channels:
        channels["fact_context"] = ""

    # 3. Vector Search
    if getattr(decision, "load_retrieval", True) and query:
        try:
            vec_req = MemoryRetrievalQueryRequest(query=query, limit=5)
            vec_res = await orchestrator.memory.query_retrieval(vec_req)
            if vec_res and hasattr(vec_res, "items"):
                candidates = [item.dict() if hasattr(item, "dict") else dict(item) for item in vec_res.items]
                channels["vector"] = retrieval_rerank(orchestrator, query=query, candidates=candidates)
        except Exception as exc:
            _LOGGER.warning("Failed to load vector context: %s", exc)

    counts = {
        "vector": len(channels.get("vector", [])),
        "facts": len(channels.get("facts", [])),
        "history": len(channels.get("history", [])),
        "neo4j": 0,
    }

    # 4. Graph
    if (getattr(decision, "load_relations", False) or getattr(decision, "route", "") == "RELATION_LOOKUP") and session_id:
        try:
            graph_res = None
            if hasattr(orchestrator.memory, "relation_expand"):
                graph_req = RelationExpandRequest(seed_node=f"session:{session_id}", max_depth=2, limit=15)
                graph_res = await orchestrator.memory.relation_expand(graph_req)
            elif hasattr(orchestrator.memory, "expand_relations"):
                graph_req = RelationExpandRequest(seed_node=f"session:{session_id}", max_depth=2, limit=15)
                graph_res = await orchestrator.memory.expand_relations(graph_req)

            rel_items = []
            if graph_res:
                rel_items = getattr(graph_res, "items", getattr(graph_res, "edges", getattr(graph_res, "relations", []))) or []

            lines = []
            for item in rel_items:
                src = str(getattr(item, "source", "") or "")
                rel = getattr(item, "relation", "")
                if hasattr(rel, "value"):
                    rel = rel.value
                rel_str = str(rel or "RELATED")
                tgt = str(getattr(item, "target", "") or "")
                if src and tgt:
                    lines.append(f"[relation] {src} -[{rel_str}]-> {tgt}")

            relation_context = "\n".join(lines)
            if lines:
                guardrail = graph_priority_guardrail_line(orchestrator, session_id)
                if guardrail:
                    relation_context = f"{guardrail}\n{relation_context}"

            channels["relation_context"] = relation_context
            channels["graph"] = rel_items
            counts["neo4j"] = len(rel_items)
        except Exception as exc:
            _LOGGER.warning("Failed to load graph context: %s", exc)

    if "relation_context" not in channels:
        channels["relation_context"] = ""

    return channels, counts


async def upsert_memory_commit_embedding(
    orchestrator: Orchestrator,
    *,
    session_id: str,
    user_id: str,
    run_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Commit response content into vector retrieval index."""
    if not content or not content.strip():
        return False
    try:
        upsert_req = ContextUpsertRequest(
            scope=ContextScope.SESSION,
            session_id=session_id,
            content=content,
            metadata={
                "user_id": user_id,
                "run_id": run_id,
                "source": "memory_commit",
                **(metadata or {}),
            },
        )
        res = await orchestrator.memory.upsert_context(upsert_req)
        return bool(res and getattr(res, "status", None) == "success")
    except Exception as exc:
        _LOGGER.warning("Failed to commit memory embedding: %s", exc)
        return False


def extract_content_relations(
    orchestrator: Orchestrator,
    *,
    session_id: str,
    text: str,
    source: str = "orchestrator",
) -> List[Dict[str, Any]]:
    """Extract semantic (subject, relation, object) triples from text."""
    if not text or not text.strip():
        return []

    triples: List[Dict[str, Any]] = []
    patterns = [
        r"(\w+)\s+(is|hat|nutzt|unterstützt|erstellt|bearbeitet)\s+(\w+)",
        r"(\w+)\s+depends\s+on\s+(\w+)",
        r"(\w+)\s+uses\s+(\w+)",
    ]

    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        for match in matches:
            if len(match) == 3:
                subj, rel, obj = match
                triples.append({
                    "subject": relation_node_key(subj),
                    "relation": rel.upper(),
                    "object": relation_node_key(obj),
                    "source": source,
                    "session_id": session_id,
                })
            elif len(match) == 2:
                subj, obj = match
                triples.append({
                    "subject": relation_node_key(subj),
                    "relation": "USES",
                    "object": relation_node_key(obj),
                    "source": source,
                    "session_id": session_id,
                })

    return triples


async def upsert_validated_relations(
    orchestrator: Orchestrator,
    *,
    session_id: str,
    relations: List[Dict[str, Any]],
) -> int:
    """Persist validated structural relations into RELATION_EDGE store."""
    if not relations:
        return 0

    count = 0
    for rel in relations:
        try:
            rel_type = getattr(RelationType, rel.get("relation", "ASSOCIATED_WITH"), RelationType.ASSOCIATED_WITH)
            upsert_req = RelationUpsertRequest(
                source_node=rel.get("subject", ""),
                target_node=rel.get("object", ""),
                relation_type=rel_type,
                weight=float(rel.get("weight", 1.0)),
                properties={"session_id": session_id, "source": rel.get("source", "extracted")},
            )
            res = await orchestrator.memory.upsert_relation(upsert_req)
            if res and getattr(res, "status", None) == "success":
                count += 1
        except Exception as exc:
            _LOGGER.debug("Failed to upsert validated relation: %s", exc)

    return count
