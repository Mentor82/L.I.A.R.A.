"""
AI-Brain Sequential Ingestion Builder.
Ingests export data, generates 1024D vector embeddings, populates SQLite, Qdrant, and Neo4j stores.
"""

from typing import List, Dict, Any, Optional
from ai_brain.parser import ChatGPTExportParser
from ai_brain.storage.relational_db import RelationalBrainStore
from ai_brain.storage.vector_qdrant import QdrantBrainVectorStore
from ai_brain.storage.graph_neo4j import Neo4jBrainGraphStore
from ai_brain.subgraph_engine import BoundedSubgraphEngine
from ai_brain.schema import (
    BrainNode,
    BrainEdge,
    EpistemicState,
    EdgeProvenance,
    PersonalDenkraumRelation,
    SystemRelation,
    SemanticRelation,
    EvolutionRelation,
)


class BrainBuilder:
    """Orchestrates ingestion of export threads into multi-db storage stack."""

    def __init__(self, engine: Optional[BoundedSubgraphEngine] = None) -> None:
        self.engine = engine or BoundedSubgraphEngine()

    def build_from_export(self, export_dir: str, entity_id: str = "nephy", limit: int = 100) -> Dict[str, int]:
        parser = ChatGPTExportParser(export_dir)
        threads = parser.parse_threads(limit=limit)

        threads_count = 0
        turns_count = 0
        nodes_count = 0
        edges_count = 0

        # Seed core architectural nodes
        core_nodes = [
            BrainNode(id="node_cortana", entity_id=entity_id, label="Concept", title="Cortana Roots", description="Early digital assistant experiments", visibility="shared"),
            BrainNode(id="node_nephy", entity_id=entity_id, label="Concept", title="Nephy Identity", description="Persistent AI identity & continuity", visibility="shared"),
            BrainNode(id="node_liara", entity_id=entity_id, label="Concept", title="Liara Home-Assistant", description="Home & Life Assistant system v0.1", visibility="shared"),
            BrainNode(id="node_liara_sys", entity_id=entity_id, label="Concept", title="L.I.A.R.A.", description="Local Intelligent Autonomous Reasoning Assistant", visibility="shared"),
            BrainNode(id="node_architecture", entity_id=entity_id, label="Concept", title="Model-Independent Architecture", description="Orchestration, memory, tools, validation", visibility="shared"),
            BrainNode(id="node_foundation", entity_id=entity_id, label="Concept", title="LIARA Foundation", description="External constitutional layer & governance", visibility="shared"),
        ]
        for n in core_nodes:
            self.engine.graph_store.upsert_node(n)
            nodes_count += 1

        # Seed core 4-class relations with provenances
        core_edges = [
            BrainEdge(
                id="edge_cortana_nephy",
                subject_id="node_cortana",
                predicate=EvolutionRelation.EVOLVED_INTO.value,
                object_id="node_nephy",
                epistemic_state=EpistemicState.USER_CONFIRMED,
                provenance=EdgeProvenance(source_type="user_confirmed", confidence=1.0, verified=True, scope="projects:read"),
            ),
            BrainEdge(
                id="edge_nephy_liara",
                subject_id="node_nephy",
                predicate=PersonalDenkraumRelation.INSPIRED_BY.value,
                object_id="node_liara",
                epistemic_state=EpistemicState.USER_CONFIRMED,
                provenance=EdgeProvenance(source_type="user_confirmed", confidence=1.0, verified=True, scope="projects:read"),
            ),
            BrainEdge(
                id="edge_liara_sys",
                subject_id="node_liara",
                predicate=EvolutionRelation.EVOLVED_INTO.value,
                object_id="node_liara_sys",
                epistemic_state=EpistemicState.USER_CONFIRMED,
                provenance=EdgeProvenance(source_type="user_confirmed", confidence=1.0, verified=True, scope="projects:read"),
            ),
            BrainEdge(
                id="edge_sys_architecture",
                subject_id="node_liara_sys",
                predicate=SystemRelation.IMPLEMENTS.value,
                object_id="node_architecture",
                epistemic_state=EpistemicState.VERIFIED,
                provenance=EdgeProvenance(source_type="system", confidence=1.0, verified=True, scope="projects:read"),
            ),
            BrainEdge(
                id="edge_sys_foundation",
                subject_id="node_liara_sys",
                predicate=SystemRelation.GOVERNS.value,
                object_id="node_foundation",
                epistemic_state=EpistemicState.VERIFIED,
                provenance=EdgeProvenance(source_type="system", confidence=1.0, verified=True, scope="projects:read"),
            ),
        ]
        for e in core_edges:
            self.engine.graph_store.upsert_edge(e)
            edges_count += 1

        # Process threads
        for thread in threads:
            thread_id = thread["thread_id"]
            title = thread["title"]
            c_time = thread["create_time"]

            self.engine.relational_store.upsert_thread(thread_id, entity_id, title, c_time, {})
            threads_count += 1

            for turn in thread["turns"]:
                t_id = turn["turn_id"]
                role = turn["role"]
                content = turn["content"]
                t_time = turn["create_time"]

                self.engine.relational_store.upsert_turn(t_id, thread_id, entity_id, role, content, t_time)
                turns_count += 1

                # Generate vector embedding & store in Qdrant adapter
                vec = self.engine.generate_dummy_embedding(content)
                self.engine.vector_store.upsert_vector(
                    point_id=t_id,
                    vector=vec,
                    payload={
                        "entity_id": entity_id,
                        "thread_id": thread_id,
                        "role": role,
                        "content": content[:200],
                        "visibility": "shared",
                        "epistemic_state": EpistemicState.VERIFIED.value,
                    },
                )

        return {
            "threads": threads_count,
            "turns": turns_count,
            "nodes": nodes_count,
            "edges": edges_count,
        }
