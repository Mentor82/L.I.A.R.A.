"""
Bounded Semantic Subgraph Engine for AI-Brain (ADR-007).
Orchestrates Qdrant seed vector retrieval and Neo4j traversal-authorized graph search.
"""

from typing import List, Dict, Any, Optional
from ai_brain.auth import pass_auth_manager
from ai_brain.schema import (
    BoundedSubgraphRequest,
    BoundedSubgraphResponse,
    BrainNode,
    BrainEdge,
    EpistemicState,
)
from ai_brain.storage.vector_qdrant import QdrantBrainVectorStore
from ai_brain.storage.graph_neo4j import Neo4jBrainGraphStore
from ai_brain.storage.relational_db import RelationalBrainStore


class BoundedSubgraphEngine:
    """Engine executing the Bounded Semantic Subgraph Pipeline enforcing ADR-007 invariants."""

    def __init__(
        self,
        vector_store: Optional[QdrantBrainVectorStore] = None,
        graph_store: Optional[Neo4jBrainGraphStore] = None,
        relational_store: Optional[RelationalBrainStore] = None,
    ) -> None:
        self.vector_store = vector_store or QdrantBrainVectorStore()
        self.graph_store = graph_store or Neo4jBrainGraphStore()
        self.relational_store = relational_store or RelationalBrainStore()

    def generate_dummy_embedding(self, text: str) -> List[float]:
        """Derive reproducible 1024D vector representation."""
        hash_val = sum(ord(c) for c in text)
        return [((hash_val + i) % 100) / 100.0 for i in range(1024)]

    def query_bounded_subgraph(self, request: BoundedSubgraphRequest) -> BoundedSubgraphResponse:
        """
        Execute Bounded Subgraph pipeline:
        Token Verification -> Seed Location -> Traversal-Level Auth Graph Search -> Subgraph Response.
        """
        # 1. Verify Visitor Pass Token
        token = pass_auth_manager.verify_pass(request.token_id)
        if not token:
            raise ValueError(f"Visitor Pass Token '{request.token_id}' is invalid or expired.")

        # Determine effective max_hops (bounded by token.max_hops)
        effective_hops = token.max_hops
        if request.requested_max_hops is not None:
            effective_hops = min(request.requested_max_hops, token.max_hops)

        # 2. Locate Seed Nodes via Vector Similarity & Graph Lookup
        query_vector = self.generate_dummy_embedding(request.query)
        vector_matches = self.vector_store.search_similar(
            query_vector=query_vector,
            entity_id=request.entity_id,
            visibility=token.visibility,
            top_k=request.top_k_seeds,
            allowed_epistemic_states=[s.value for s in token.allowed_epistemic_states],
        )

        valid_seed_node_ids = [m["id"] for m in vector_matches if m["id"] in self.graph_store._nodes]

        # If vector points are not direct graph nodes, fallback to graph nodes for the entity
        if not valid_seed_node_ids:
            valid_seed_node_ids = [
                node.id for node in self.graph_store._nodes.values()
                if node.entity_id == request.entity_id and node.visibility == token.visibility
            ][:request.top_k_seeds]

        # 3. Traversal-Level Graph Search
        subgraph = self.graph_store.find_bounded_subgraph(
            seed_node_ids=valid_seed_node_ids,
            entity_id=request.entity_id,
            max_hops=effective_hops,
            visibility=token.visibility,
            allowed_epistemic_states=token.allowed_epistemic_states,
            allowed_scopes=token.scopes,
        )

        nodes: List[BrainNode] = subgraph["nodes"]
        edges: List[BrainEdge] = subgraph["edges"]

        # Calculate Epistemic Breakdown
        breakdown: Dict[str, int] = {}
        for edge in edges:
            state_key = edge.epistemic_state.value
            breakdown[state_key] = breakdown.get(state_key, 0) + 1

        return BoundedSubgraphResponse(
            status="success",
            entity_id=request.entity_id,
            query=request.query,
            seed_nodes=subgraph["seed_node_ids"],
            nodes=nodes,
            edges=edges,
            epistemic_breakdown=breakdown,
            token_id=token.token_id,
            bounded_hops=effective_hops,
        )
