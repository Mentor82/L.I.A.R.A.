"""Unit tests for deterministic gap detection."""

from __future__ import annotations

from services.orchestrator.gap_detector import GapAction, GapDetector, GapType


def test_detect_session_gap_uses_redis_source_availability():
    decision = GapDetector.detect(
        query="Was haben wir vorhin besprochen?",
        validation_issues=["Factual answer appears ungrounded: no context or tool evidence"],
        context_sources={"chroma": 0, "qdrant": 0, "postgres": 2, "neo4j": 0, "redis": 0},
        reasoning_step=1,
        previous_gap_types=[],
    )

    assert decision.gap_detected is True
    assert decision.gap_type == GapType.SESSION_GAP.value
    assert decision.action == GapAction.LOAD_SESSION.value
    assert decision.trigger == "missing_session_history"
    assert decision.missing == ["missing recent session history for requested reference"]


def test_detect_session_gap_stops_when_identical_gap_repeats():
    decision = GapDetector.detect(
        query="What did we discuss earlier?",
        validation_issues=["Factual answer appears ungrounded: no context or tool evidence"],
        context_sources={"redis": 0},
        reasoning_step=2,
        previous_gap_types=[GapType.SESSION_GAP.value],
    )

    assert decision.gap_detected is False
    assert decision.gap_type == GapType.NONE.value
    assert decision.action == GapAction.STOP.value
    assert decision.trigger == "repeated_identical_gap"
