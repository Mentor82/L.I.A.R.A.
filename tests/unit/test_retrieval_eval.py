from __future__ import annotations

from services.memory.retrieval_eval import (
    RetrievalEvalCase,
    evaluate_retrieval_case,
    evaluate_retrieval_cases,
)


def test_evaluate_retrieval_case_normalizes_chunk_hits_to_document_roots() -> None:
    result = evaluate_retrieval_case(
        RetrievalEvalCase(
            name="chunk-hit",
            query="alpha query",
            expected_document_ids=("docA",),
            actual_document_ids=("docA#chunk-2", "docB#chunk-0"),
        ),
        k=2,
    )

    assert result.recall_at_k == 1.0
    assert result.mrr == 1.0
    assert result.answer_quality == 1.0
    assert result.matched_document_ids == ("docA",)


def test_evaluate_retrieval_case_reports_partial_answer_quality_when_late_hit_exists() -> None:
    result = evaluate_retrieval_case(
        RetrievalEvalCase(
            name="late-hit",
            query="beta query",
            expected_document_ids=("docB", "docC"),
            actual_document_ids=("docX", "docB#summary", "docY"),
        ),
        k=3,
    )

    assert result.recall_at_k == 0.5
    assert result.mrr == 0.5
    assert result.answer_quality == 0.5


def test_evaluate_retrieval_cases_aggregates_metrics() -> None:
    summary = evaluate_retrieval_cases(
        [
            RetrievalEvalCase(
                name="perfect",
                query="alpha",
                expected_document_ids=("docA",),
                actual_document_ids=("docA#chunk-0",),
            ),
            RetrievalEvalCase(
                name="miss",
                query="gamma",
                expected_document_ids=("docG",),
                actual_document_ids=("docZ",),
            ),
        ],
        k=1,
    )

    assert summary.case_count == 2
    assert summary.avg_recall_at_k == 0.5
    assert summary.avg_mrr == 0.5
    assert summary.avg_answer_quality == 0.5