"""
Qdrant Vector Store Adapter for AI-Brain.
Stores 1024D OpenVINO embeddings in collection `ai_brain_vectors` with payload filtering.
"""

import math
from typing import List, Dict, Any, Optional
from ai_brain.config import QDRANT_COLLECTION_NAME


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


class QdrantBrainVectorStore:
    """Manages 1024D vector embeddings for AI-Brain with payload filtering (entity_id, visibility)."""

    def __init__(self, collection_name: str = QDRANT_COLLECTION_NAME) -> None:
        self.collection_name = collection_name
        self._in_memory_vectors: List[Dict[str, Any]] = []

    def upsert_vector(
        self,
        point_id: str,
        vector: List[float],
        payload: Dict[str, Any],
    ) -> None:
        """Upsert a vector point with metadata payload."""
        point = {
            "id": point_id,
            "vector": vector,
            "payload": payload,
        }
        # Update existing or append
        self._in_memory_vectors = [p for p in self._in_memory_vectors if p["id"] != point_id]
        self._in_memory_vectors.append(point)

    def search_similar(
        self,
        query_vector: List[float],
        entity_id: str = "nephy",
        visibility: str = "shared",
        top_k: int = 5,
        allowed_epistemic_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search similar vectors enforcing retrieval-level payload filtering."""
        results = []
        for point in self._in_memory_vectors:
            payload = point.get("payload", {})
            if payload.get("entity_id") != entity_id:
                continue
            if payload.get("visibility") != visibility:
                continue
            if allowed_epistemic_states and payload.get("epistemic_state") not in allowed_epistemic_states:
                continue

            score = cosine_similarity(query_vector, point["vector"])
            results.append({
                "id": point["id"],
                "score": score,
                "payload": payload,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
