"""Unit tests for the librarian-pipeline -> EvidenceEngine shape adapter (Issue #8)."""

from __future__ import annotations

from services.orchestrator.defs.evidence_adapter import (
    map_context_channels_for_evidence_engine,
    map_source_counts_for_evidence_engine,
)


def test_maps_real_librarian_channel_shape():
    # Real shape returned by librarian_pipeline.load_librarian_context():
    # history/facts/vector/graph/fact_context/relation_context.
    channels = {
        "history": [],
        "facts": [],
        "vector": [],
        "graph": [],
        "fact_context": "[fact_verified:global] name: Mirko",
        "relation_context": "[relation] a -[KNOWS]-> b",
    }
    mapped = map_context_channels_for_evidence_engine(channels)
    assert mapped["fact_context"] == "[fact_verified:global] name: Mirko"
    assert mapped["relation_context"] == "[relation] a -[KNOWS]-> b"
    # Pre-existing, unrelated gap: load_librarian_context never populates these.
    assert mapped["system_context"] == ""
    assert mapped["memory_context"] == ""
    assert mapped["working_context"] == ""


def test_maps_real_librarian_counts_shape():
    counts = {"vector": 3, "facts": 2, "history": 5, "neo4j": 0}
    mapped = map_source_counts_for_evidence_engine(counts)
    assert mapped["facts"] == 2
    assert mapped["qdrant"] == 3
    assert mapped["system"] == 0
    assert mapped["chroma"] == 0


def test_handles_missing_keys_gracefully():
    assert map_context_channels_for_evidence_engine({}) == {
        "system_context": "",
        "fact_context": "",
        "memory_context": "",
        "working_context": "",
        "relation_context": "",
    }
    assert map_source_counts_for_evidence_engine({}) == {
        "system": 0,
        "facts": 0,
        "qdrant": 0,
        "chroma": 0,
    }
