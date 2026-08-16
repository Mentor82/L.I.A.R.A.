"""Adapts real librarian-pipeline context/count shapes into what
EvidenceEngine.analyze() expects (Issue #8 pipeline wiring).

librarian_pipeline.load_librarian_context() returns channel keys
(history, facts, vector, graph, fact_context, relation_context) and count
keys (vector, facts, history, neo4j) that don't line up with
EvidenceEngine._collect_evidence/_select_sources' expected keys
(system_context/fact_context/memory_context/working_context/
relation_context and system/facts/qdrant/chroma respectively). Without this
adapter, EvidenceEngine would silently treat every non-tool_output source as
absent even when facts/history data exists.

system_context/memory_context/working_context are left empty here because
load_librarian_context never populates them -- a pre-existing, unrelated gap
in the real pipeline, not something Issue #8 introduces or is expected to
fix. This only matters for evidence sourced from those channels; the
search-miss/connector-state scenarios this issue is about are entirely
tool_output-sourced, which this adapter passes through unaffected.
"""

from __future__ import annotations

from typing import Any, Dict


def map_context_channels_for_evidence_engine(channels: Dict[str, Any]) -> Dict[str, str]:
    return {
        "system_context": "",
        "fact_context": str(channels.get("fact_context", "") or ""),
        "memory_context": "",
        "working_context": "",
        "relation_context": str(channels.get("relation_context", "") or ""),
    }


def map_source_counts_for_evidence_engine(counts: Dict[str, Any]) -> Dict[str, int]:
    return {
        "system": 0,
        "facts": int(counts.get("facts", 0) or 0),
        "qdrant": int(counts.get("vector", 0) or 0),
        "chroma": 0,
    }
