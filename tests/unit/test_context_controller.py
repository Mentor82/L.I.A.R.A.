"""Unit tests for context control strategy (per-step budget, adaptive β, deduplication)."""

from __future__ import annotations

from services.orchestrator.context_controller import ContextController


def test_compressor_removes_exact_duplicates_and_preserves_metadata():
    compressor = ContextController()
    result = compressor.compress(
        previous_context="[fact] Memory uses Postgres\n[fact] Memory uses Postgres",
        new_context="[memory] Qdrant stores semantic memory",
        reasoning_step=2,
    )

    assert result.dropped_items >= 1
    assert result.metadata["source"] == "context_controller"
    assert result.metadata["compression_level"] == "step_control"
    assert result.metadata["reasoning_step"] == 2
    assert "Memory uses Postgres" in result.facts
    assert "Qdrant stores semantic memory" in result.facts


def test_compressor_replaces_raw_only_context_with_summary_only():
    compressor = ContextController()
    result = compressor.compress(
        previous_context="[context] Retrieved note about Postgres facts\n[context] Retrieved note about Qdrant retrieval",
        new_context="[context] Retrieved note about Neo4j relations",
        reasoning_step=2,
    )

    assert result.final_context.startswith("[summary] Compressed context:")
    assert "[context]" not in result.final_context
    assert result.metadata["output_items"] == 1
    assert result.dropped_items >= 2
    assert result.meaningful_reduction is True


def test_compressor_prefers_compressed_entries_over_raw_accumulation():
    compressor = ContextController()
    result = compressor.compress(
        previous_context="[summary] Memory uses Postgres for facts",
        new_context="[context] raw duplicate text\n[fact] Memory uses Postgres for facts",
        reasoning_step=3,
    )

    assert "[context]" not in result.final_context
    assert "[fact] Memory uses Postgres for facts" in result.final_context or "[summary]" in result.final_context
    assert result.no_new_information is True


def test_compressor_detects_no_new_information_and_meaningful_reduction_flags():
    compressor = ContextController()
    result = compressor.compress(
        previous_context="[fact] Postgres stores facts",
        new_context="[fact] Postgres stores facts",
        reasoning_step=1,
    )

    assert result.no_new_information is True
    assert result.meaningful_reduction is True
