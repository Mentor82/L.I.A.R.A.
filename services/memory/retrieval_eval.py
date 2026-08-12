"""Lightweight retrieval evaluation helpers for Recall@k, MRR, and answer-quality proxy metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class RetrievalEvalCase:
    name: str
    query: str
    expected_document_ids: tuple[str, ...]
    actual_document_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalEvalResult:
    name: str
    recall_at_k: float
    mrr: float
    answer_quality: float
    matched_document_ids: tuple[str, ...]
    expected_document_ids: tuple[str, ...]
    actual_document_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalEvalSummary:
    case_count: int
    avg_recall_at_k: float
    avg_mrr: float
    avg_answer_quality: float
    results: tuple[RetrievalEvalResult, ...]


def evaluate_retrieval_case(case: RetrievalEvalCase, *, k: int | None = None) -> RetrievalEvalResult:
    expected = tuple(dict.fromkeys(_root_document_id(doc_id) for doc_id in case.expected_document_ids if doc_id))
    actual = tuple(dict.fromkeys(_root_document_id(doc_id) for doc_id in case.actual_document_ids if doc_id))
    limited_actual = actual[:k] if k is not None else actual

    matched = tuple(doc_id for doc_id in limited_actual if doc_id in expected)
    recall = 0.0 if not expected else len(set(matched)) / len(set(expected))

    reciprocal_rank = 0.0
    for idx, doc_id in enumerate(limited_actual, start=1):
        if doc_id in expected:
            reciprocal_rank = 1.0 / idx
            break

    answer_quality = _answer_quality_proxy(expected=expected, matched=matched)
    return RetrievalEvalResult(
        name=case.name,
        recall_at_k=round(recall, 6),
        mrr=round(reciprocal_rank, 6),
        answer_quality=round(answer_quality, 6),
        matched_document_ids=matched,
        expected_document_ids=expected,
        actual_document_ids=limited_actual,
    )


def evaluate_retrieval_cases(cases: Sequence[RetrievalEvalCase], *, k: int | None = None) -> RetrievalEvalSummary:
    results = tuple(evaluate_retrieval_case(case, k=k) for case in cases)
    if not results:
        return RetrievalEvalSummary(
            case_count=0,
            avg_recall_at_k=0.0,
            avg_mrr=0.0,
            avg_answer_quality=0.0,
            results=(),
        )

    return RetrievalEvalSummary(
        case_count=len(results),
        avg_recall_at_k=round(sum(item.recall_at_k for item in results) / len(results), 6),
        avg_mrr=round(sum(item.mrr for item in results) / len(results), 6),
        avg_answer_quality=round(sum(item.answer_quality for item in results) / len(results), 6),
        results=results,
    )


def _root_document_id(document_id: str) -> str:
    value = (document_id or "").strip()
    if not value:
        return ""
    if "#chunk-" in value:
        return value.split("#chunk-", 1)[0]
    if value.endswith("#summary"):
        return value[:-8]
    return value


def _answer_quality_proxy(*, expected: Iterable[str], matched: Iterable[str]) -> float:
    expected_set = {item for item in expected if item}
    matched_set = {item for item in matched if item}
    if not expected_set:
        return 0.0
    coverage = len(expected_set & matched_set) / len(expected_set)
    if coverage >= 1.0:
        return 1.0
    if coverage > 0.0:
        return 0.5
    return 0.0