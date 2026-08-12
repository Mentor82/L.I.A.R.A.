"""
Unit tests for AI-Brain Traversal-Level Authorization Invariant (ADR-007 Invariant 1).
Rule: Authorization constrains retrieval, not merely presentation. Private nodes/edges excluded at graph traversal level.
"""

import pytest
from ai_brain.storage.graph_neo4j import Neo4jBrainGraphStore
from ai_brain.schema import BrainNode, BrainEdge, EpistemicState, EdgeProvenance


def test_traversal_level_authorization_excludes_private_nodes():
    graph_store = Neo4jBrainGraphStore()

    # Shared nodes
    node_public_1 = BrainNode(id="node_pub1", entity_id="nephy", title="Public Concept 1", visibility="shared")
    node_public_2 = BrainNode(id="node_pub2", entity_id="nephy", title="Public Concept 2", visibility="shared")

    # Private node (must NEVER be retrieved)
    node_private = BrainNode(id="node_priv", entity_id="nephy", title="Private Secret", visibility="private")

    graph_store.upsert_node(node_public_1)
    graph_store.upsert_node(node_public_2)
    graph_store.upsert_node(node_private)

    # Edges
    edge_pub = BrainEdge(
        id="edge_pub",
        subject_id="node_pub1",
        predicate="RELATES_TO",
        object_id="node_pub2",
        epistemic_state=EpistemicState.USER_CONFIRMED,
        provenance=EdgeProvenance(visibility="shared", scope="facts:read"),
    )
    edge_priv = BrainEdge(
        id="edge_priv",
        subject_id="node_pub1",
        predicate="RELATES_TO",
        object_id="node_priv",
        epistemic_state=EpistemicState.USER_CONFIRMED,
        provenance=EdgeProvenance(visibility="private", scope="facts:read"),
    )

    graph_store.upsert_edge(edge_pub)
    graph_store.upsert_edge(edge_priv)

    # Perform traversal with visibility="shared"
    subgraph = graph_store.find_bounded_subgraph(
        seed_node_ids=["node_pub1"],
        entity_id="nephy",
        max_hops=2,
        visibility="shared",
    )

    retrieved_node_ids = {n.id for n in subgraph["nodes"]}
    retrieved_edge_ids = {e.id for e in subgraph["edges"]}

    # Verify that private node and private edge were excluded at retrieval level
    assert "node_pub1" in retrieved_node_ids
    assert "node_pub2" in retrieved_node_ids
    assert "node_priv" not in retrieved_node_ids
    assert "edge_pub" in retrieved_edge_ids
    assert "edge_priv" not in retrieved_edge_ids
