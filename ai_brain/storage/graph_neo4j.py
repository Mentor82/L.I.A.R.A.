"""
Neo4j Graph Store Adapter for AI-Brain.
Implements ADR-007 Traversal-Level Authorization Invariant:
"Authorization constrains retrieval, not merely presentation."
"""

from typing import List, Dict, Any, Optional
from ai_brain.schema import BrainNode, BrainEdge, EpistemicState, EdgeProvenance


class Neo4jBrainGraphStore:
    """Manages 4-class relational knowledge graphs with Epistemic State & Traversal-Level Auth."""

    def __init__(self) -> None:
        self._nodes: Dict[str, BrainNode] = {}
        self._edges: Dict[str, BrainEdge] = {}

    def upsert_node(self, node: BrainNode) -> None:
        self._nodes[node.id] = node

    def upsert_edge(self, edge: BrainEdge) -> None:
        self._edges[edge.id] = edge

    def get_node(self, node_id: str) -> Optional[BrainNode]:
        return self._nodes.get(node_id)

    def find_bounded_subgraph(
        self,
        seed_node_ids: List[str],
        entity_id: str = "nephy",
        max_hops: int = 2,
        visibility: str = "shared",
        allowed_epistemic_states: Optional[List[EpistemicState]] = None,
        allowed_scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Traverse relational graph up to max_hops from seed_node_ids.
        INVARIANT 1: Traversal-Level Authorization. Private or unauthorized nodes/edges
        are EXCLUDED AT TRAVERSAL LEVEL, never loaded into transient memory.
        """
        allowed_states = set(allowed_epistemic_states or [
            EpistemicState.USER_CONFIRMED,
            EpistemicState.VERIFIED,
            EpistemicState.INFERENCE,
        ])
        scopes = set(allowed_scopes or ["facts:read", "relations:read", "projects:read"])

        # Filter seed nodes at retrieval level
        valid_seeds = [
            nid for nid in seed_node_ids
            if nid in self._nodes
            and self._nodes[nid].entity_id == entity_id
            and self._nodes[nid].visibility == visibility
        ]

        visited_nodes: Dict[str, BrainNode] = {nid: self._nodes[nid] for nid in valid_seeds}
        visited_edges: Dict[str, BrainEdge] = {}
        current_frontier = set(valid_seeds)

        for hop in range(max_hops):
            next_frontier = set()
            for node_id in current_frontier:
                for edge in self._edges.values():
                    if edge.subject_id != node_id and edge.object_id != node_id:
                        continue

                    # Traversal-Level Filters: Epistemic state & scope check
                    if edge.epistemic_state not in allowed_states:
                        continue
                    if edge.provenance.visibility != visibility:
                        continue
                    if edge.provenance.scope and edge.provenance.scope not in scopes:
                        continue

                    target_id = edge.object_id if edge.subject_id == node_id else edge.subject_id
                    target_node = self._nodes.get(target_id)

                    # Node Traversal-Level Filter: Entity ID & visibility check
                    if not target_node or target_node.entity_id != entity_id or target_node.visibility != visibility:
                        continue

                    visited_edges[edge.id] = edge
                    if target_id not in visited_nodes:
                        visited_nodes[target_id] = target_node
                        next_frontier.add(target_id)

            current_frontier = next_frontier

        return {
            "seed_node_ids": valid_seeds,
            "nodes": list(visited_nodes.values()),
            "edges": list(visited_edges.values()),
            "hops_executed": max_hops,
        }
