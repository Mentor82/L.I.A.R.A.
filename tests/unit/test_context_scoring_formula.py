from services.contracts import ContextScope
from services.memory.tier_store import _rerank_with_context_signals


def test_context_score_formula_prefers_higher_importance_confidence_and_lower_cost():
    scope = ContextScope(session_id="s1", turn_index=10, time_decay=0.9)
    candidates = [
        {
            "document_id": "low-quality",
            "score": 0.85,
            "metadata": {"turn_index": 10, "importance": 0.1, "confidence": 0.1, "cost": 0.6},
        },
        {
            "document_id": "high-quality",
            "score": 0.70,
            "metadata": {"turn_index": 10, "importance": 0.8, "confidence": 0.7, "cost": 0.1},
        },
    ]

    ranked = _rerank_with_context_signals(candidates, scope)

    assert ranked[0]["document_id"] == "high-quality"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["score_formula"] == "similarity+importance+recency+confidence-cost"


def test_context_score_formula_applies_recency_decay_from_turn_distance():
    scope = ContextScope(session_id="s1", turn_index=20, time_decay=0.8)
    candidates = [
        {
            "document_id": "recent",
            "score": 0.6,
            "metadata": {"turn_index": 20, "importance": 0.0, "confidence": 0.0, "cost": 0.0},
        },
        {
            "document_id": "old",
            "score": 0.6,
            "metadata": {"turn_index": 10, "importance": 0.0, "confidence": 0.0, "cost": 0.0},
        },
    ]

    ranked = _rerank_with_context_signals(candidates, scope)

    assert ranked[0]["document_id"] == "recent"
    assert ranked[0]["score_components"]["recency"] > ranked[1]["score_components"]["recency"]
