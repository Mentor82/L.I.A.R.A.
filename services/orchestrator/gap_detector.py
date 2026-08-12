"""Deterministic gap detection for reasoning retries."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List

from services.config import Settings


MIN_GAP_CONFIDENCE = 0.6
MAX_REASONING_STEPS = max(1, int(getattr(Settings, "MAX_REASONING_STEPS", 5)))


class GapType(str, Enum):
    NONE = "NONE"
    FACT_GAP = "FACT_GAP"
    CONTEXT_GAP = "CONTEXT_GAP"
    MEMORY_GAP = "MEMORY_GAP"
    RELATION_GAP = "RELATION_GAP"
    SESSION_GAP = "SESSION_GAP"


class GapAction(str, Enum):
    STOP = "STOP"
    LOAD_FACTS = "LOAD_FACTS"
    LOAD_CONTEXT = "LOAD_CONTEXT"
    LOAD_MEMORY = "LOAD_MEMORY"
    LOAD_RELATIONS = "LOAD_RELATIONS"
    LOAD_SESSION = "LOAD_SESSION"


GAP_ACTION_MAP = {
    GapType.NONE: GapAction.STOP,
    GapType.FACT_GAP: GapAction.LOAD_FACTS,
    GapType.CONTEXT_GAP: GapAction.LOAD_CONTEXT,
    GapType.MEMORY_GAP: GapAction.LOAD_MEMORY,
    GapType.RELATION_GAP: GapAction.LOAD_RELATIONS,
    GapType.SESSION_GAP: GapAction.LOAD_SESSION,
}


@dataclass
class GapDecision:
    gap_detected: bool
    gap_type: str
    missing: List[str]
    confidence: float
    action: str
    reasoning_step: int
    trigger: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class GapDetector:
    """Structured, deterministic gap detector used in retry loop."""

    @staticmethod
    def detect(
        *,
        query: str,
        validation_issues: List[str],
        context_sources: Dict[str, int],
        reasoning_step: int,
        previous_gap_types: List[str],
    ) -> GapDecision:
        issues = [str(issue) for issue in (validation_issues or []) if str(issue).strip()]
        issue_text = " ".join(issues).lower()
        q = (query or "").strip()
        ql = q.lower()

        if reasoning_step >= MAX_REASONING_STEPS:
            return GapDecision(
                gap_detected=False,
                gap_type=GapType.NONE.value,
                missing=[],
                confidence=1.0,
                action=GapAction.STOP.value,
                reasoning_step=reasoning_step,
                trigger="max_reasoning_steps_reached",
            )

        if not issues:
            return GapDecision(
                gap_detected=False,
                gap_type=GapType.NONE.value,
                missing=[],
                confidence=1.0,
                action=GapAction.STOP.value,
                reasoning_step=reasoning_step,
                trigger="no_detectable_gap",
            )

        if any(marker in issue_text for marker in {"unsafe", "policy", "security", "forbidden", "command mismatch"}):
            return GapDecision(
                gap_detected=False,
                gap_type=GapType.NONE.value,
                missing=[],
                confidence=0.2,
                action=GapAction.STOP.value,
                reasoning_step=reasoning_step,
                trigger="policy_or_safety_violation",
            )

        source_counts = {
            "chroma": int((context_sources or {}).get("chroma", 0) or 0),
            "qdrant": int((context_sources or {}).get("qdrant", 0) or 0),
            "postgres": int((context_sources or {}).get("postgres", 0) or 0),
            "neo4j": int((context_sources or {}).get("neo4j", 0) or 0),
            "redis": int((context_sources or {}).get("redis", 0) or 0),
        }

        relation_keywords = (
            "relation", "relationship", "dependency", "depends", "graph", "neo4j", "verkn",
        )
        session_keywords = (
            "vorhin", "earlier", "previous", "zuvor", "letzten", "session", "history", "discussed",
        )
        context_keywords = (
            "dieses", "that", "above", "oben", "aktuell", "current context", "run context",
        )
        fact_keywords = (
            "was ist", "what is", "define", "wer ist", "who is", "dimension", "version", "port",
        )

        gap_type = GapType.NONE
        confidence = 0.0
        missing: List[str] = []
        trigger = "no_detectable_gap"

        if any(token in issue_text for token in {"grounding", "ungrounded", "source attribution", "source linkage", "factual"}):
            if any(token in ql for token in relation_keywords) and source_counts["neo4j"] == 0:
                gap_type = GapType.RELATION_GAP
                confidence = 0.82
                missing = [f"relationship detail missing for query: {q[:120]}"]
                trigger = "missing_relation_evidence"
            elif any(token in ql for token in session_keywords) and source_counts["redis"] == 0:
                gap_type = GapType.SESSION_GAP
                confidence = 0.79
                missing = ["missing recent session history for requested reference"]
                trigger = "missing_session_history"
            elif any(token in ql for token in context_keywords) and source_counts["chroma"] == 0:
                gap_type = GapType.CONTEXT_GAP
                confidence = 0.76
                missing = ["missing run-scoped context snippet for current request"]
                trigger = "missing_run_context"
            elif any(token in ql for token in fact_keywords):
                gap_type = GapType.FACT_GAP
                confidence = 0.74
                missing = [f"missing concrete fact to answer query: {q[:120]}"]
                trigger = "missing_fact_evidence"
            else:
                gap_type = GapType.MEMORY_GAP
                confidence = 0.71
                missing = ["missing semantically related evidence from memory index"]
                trigger = "missing_semantic_memory"
        elif any(token in issue_text for token in {"too short", "too long", "empty", "invalid json", "malformed markdown"}):
            gap_type = GapType.NONE
            confidence = 0.4
            missing = []
            trigger = "format_or_length_issue_no_retrieval_needed"
        elif any(token in issue_text for token in {"contradiction"}):
            gap_type = GapType.FACT_GAP
            confidence = 0.68
            missing = ["missing verifiable fact to resolve response contradiction"]
            trigger = "contradiction_requires_fact_check"

        action = GAP_ACTION_MAP[gap_type].value

        if gap_type != GapType.NONE and confidence < MIN_GAP_CONFIDENCE:
            return GapDecision(
                gap_detected=False,
                gap_type=GapType.NONE.value,
                missing=[],
                confidence=confidence,
                action=GapAction.STOP.value,
                reasoning_step=reasoning_step,
                trigger="gap_confidence_below_threshold",
            )

        current_gap_name = gap_type.value
        if current_gap_name != GapType.NONE.value:
            repeated = len(previous_gap_types) >= 1 and previous_gap_types[-1] == current_gap_name
            if repeated:
                return GapDecision(
                    gap_detected=False,
                    gap_type=GapType.NONE.value,
                    missing=[],
                    confidence=confidence,
                    action=GapAction.STOP.value,
                    reasoning_step=reasoning_step,
                    trigger="repeated_identical_gap",
                )

        return GapDecision(
            gap_detected=gap_type != GapType.NONE,
            gap_type=gap_type.value,
            missing=missing,
            confidence=round(confidence, 2),
            action=action,
            reasoning_step=reasoning_step,
            trigger=trigger,
        )
