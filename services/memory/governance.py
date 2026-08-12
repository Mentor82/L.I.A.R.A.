"""Governance policies for memory promotion, decay, and cleanup decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from typing import Any, Dict

from services.contracts import ContextUpsertRequest


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class PromotionDecision:
    should_promote: bool
    reason: str
    relevance_score: float
    threshold: float
    candidate_threshold: float
    validated_threshold: float
    governance_enabled: bool
    promotion_enabled: bool
    judge_required_for_promotion: bool
    judge_decision: str
    judge_confidence: float | None

    def as_metadata(self) -> Dict[str, Any]:
        return {
            "governance": "memory_lifecycle",
            "governance_version": "v1",
            "promotion_decision": "promote" if self.should_promote else "skip",
            "promotion_reason": self.reason,
            "promotion_relevance_score": self.relevance_score,
            "promotion_threshold": self.threshold,
            "promotion_threshold_candidate": self.candidate_threshold,
            "promotion_threshold_validated": self.validated_threshold,
            "governance_enabled": self.governance_enabled,
            "governance_promotion_enabled": self.promotion_enabled,
            "governance_promotion_require_judge": self.judge_required_for_promotion,
            "governance_judge_decision": self.judge_decision,
            "governance_judge_confidence": self.judge_confidence,
        }


class MemoryLifecycleGovernance:
    """Central policy layer for temporary context, decay, and promotion."""

    def __init__(self, *, promotion_threshold: float | None = None):
        del promotion_threshold
        self.enabled = _env_flag("MEMORY_GOVERNANCE_ENABLED", True)
        self.scope_link_enabled = self.enabled and _env_flag("MEMORY_GOVERNANCE_SCOPE_LINK_ENABLED", True)
        self.promotion_enabled = self.enabled and _env_flag("MEMORY_GOVERNANCE_PROMOTION_ENABLED", True)
        self.cleanup_enabled = self.enabled and _env_flag("MEMORY_GOVERNANCE_CLEANUP_ENABLED", True)
        self.pattern_learning_enabled = self.enabled and _env_flag("MEMORY_GOVERNANCE_PATTERN_LEARNING_ENABLED", True)
        self.require_judge_for_promotion = _env_flag("MEMORY_GOVERNANCE_REQUIRE_JUDGE_FOR_PROMOTION", False)
        self.cleanup_require_judge = _env_flag("MEMORY_GOVERNANCE_CLEANUP_REQUIRE_JUDGE", False)
        self.judge_min_confidence = max(
            0.0,
            min(float(os.getenv("MEMORY_GOVERNANCE_JUDGE_MIN_CONFIDENCE", "0.55")), 1.0),
        )
        self.reasoning_relevance_weight = max(
            0.0,
            min(float(os.getenv("MEMORY_REASONING_RELEVANCE_WEIGHT", "0.35")), 1.0),
        )
        self.pattern_relevance_bonus = max(
            0.0,
            min(float(os.getenv("MEMORY_PATTERN_RELEVANCE_BONUS", "0.03")), 0.2),
        )
        self.candidate_threshold = max(
            0.0,
            min(float(os.getenv("MEMORY_PROMOTION_THRESHOLD_CANDIDATE", "0.82")), 1.0),
        )
        self.validated_threshold = max(
            0.0,
            min(float(os.getenv("MEMORY_PROMOTION_THRESHOLD_VALIDATED", "0.92")), 1.0),
        )

    def _decision(
        self,
        should_promote: bool,
        reason: str,
        relevance_score: float,
        threshold: float,
        *,
        judge_decision: str,
        judge_confidence: float | None,
    ) -> PromotionDecision:
        return PromotionDecision(
            should_promote=should_promote,
            reason=reason,
            relevance_score=relevance_score,
            threshold=threshold,
            candidate_threshold=self.candidate_threshold,
            validated_threshold=self.validated_threshold,
            governance_enabled=self.enabled,
            promotion_enabled=self.promotion_enabled,
            judge_required_for_promotion=self.require_judge_for_promotion,
            judge_decision=judge_decision,
            judge_confidence=judge_confidence,
        )

    @staticmethod
    def _extract_judge_signal(metadata: Dict[str, Any]) -> tuple[str, float | None]:
        judge_block = metadata.get("judge_post") if isinstance(metadata.get("judge_post"), dict) else {}
        raw_decision = metadata.get("judge_post_decision") or judge_block.get("decision") or metadata.get("judge_decision")
        decision = str(raw_decision or "allow").strip().lower()

        raw_confidence = metadata.get("judge_post_confidence")
        if raw_confidence is None:
            raw_confidence = judge_block.get("confidence")
        confidence: float | None
        try:
            confidence = float(raw_confidence) if raw_confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        return decision, confidence

    def decide_promotion(self, request: ContextUpsertRequest, metadata: Dict[str, Any]) -> PromotionDecision:
        promotion_state = str(
            request.promotion_state
            or metadata.get("promotion_state")
            or "none"
        ).strip().lower()
        memory_tier = str(
            request.memory_tier
            or metadata.get("memory_tier")
            or "working"
        ).strip().lower()

        raw_relevance = metadata.get("relevance_score")
        try:
            base_relevance = float(raw_relevance) if raw_relevance is not None else 0.0
        except (TypeError, ValueError):
            base_relevance = 0.0
        base_relevance = max(0.0, min(base_relevance, 1.0))

        raw_reasoning_relevance = metadata.get("reasoning_relevance")
        try:
            reasoning_relevance = float(raw_reasoning_relevance) if raw_reasoning_relevance is not None else None
        except (TypeError, ValueError):
            reasoning_relevance = None
        if reasoning_relevance is not None:
            reasoning_relevance = max(0.0, min(reasoning_relevance, 1.0))

        relevance = base_relevance
        if reasoning_relevance is not None:
            w = self.reasoning_relevance_weight
            relevance = (base_relevance * (1.0 - w)) + (reasoning_relevance * w)

        raw_cross_session_count = metadata.get("pattern_cross_session_count")
        try:
            cross_session_count = int(raw_cross_session_count) if raw_cross_session_count is not None else 1
        except (TypeError, ValueError):
            cross_session_count = 1
        if cross_session_count > 1:
            bonus = min((cross_session_count - 1) * self.pattern_relevance_bonus, 0.15)
            relevance += bonus
        relevance = max(0.0, min(relevance, 1.0))

        judge_decision, judge_confidence = self._extract_judge_signal(metadata)

        validated = bool(metadata.get("validated", False))
        explicit_acceptance = bool(metadata.get("explicit_acceptance", False))
        accepted = validated or explicit_acceptance

        if not self.enabled:
            return self._decision(False, "governance_disabled", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)
        if not self.promotion_enabled:
            return self._decision(False, "promotion_phase_disabled", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

        if self.require_judge_for_promotion:
            if judge_decision not in {"allow", "pass", "ok"}:
                return self._decision(False, "judge_gate_blocked", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)
            if judge_confidence is not None and judge_confidence < self.judge_min_confidence:
                return self._decision(False, "judge_confidence_below_min", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

        if promotion_state in {"promoted", "pinned"}:
            return self._decision(True, f"promotion_state_{promotion_state}", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

        if memory_tier == "long_term" and accepted:
            return self._decision(True, "memory_tier_long_term", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

        if promotion_state == "candidate" and accepted and relevance >= self.candidate_threshold:
            return self._decision(True, "candidate_relevance_threshold_met", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

        if accepted and relevance >= self.validated_threshold:
            return self._decision(True, "validated_relevance_threshold_met", relevance, self.validated_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

        return self._decision(False, "insufficient_signal", relevance, self.candidate_threshold, judge_decision=judge_decision, judge_confidence=judge_confidence)

    def cleanup_allowed(self, *, judge_decision: str | None = None, judge_confidence: float | None = None) -> tuple[bool, str]:
        if not self.enabled:
            return False, "governance_disabled"
        if not self.cleanup_enabled:
            return False, "cleanup_phase_disabled"
        if not self.cleanup_require_judge:
            return True, "allowed"
        decision = str(judge_decision or "").strip().lower()
        if decision not in {"allow", "pass", "ok"}:
            return False, "cleanup_judge_gate_blocked"
        if judge_confidence is not None and judge_confidence < self.judge_min_confidence:
            return False, "cleanup_judge_confidence_below_min"
        return True, "allowed"

    def phase_enabled(self, phase: str) -> bool:
        phase_name = str(phase or "").strip().lower()
        if phase_name == "scope_link":
            return self.scope_link_enabled
        if phase_name == "promotion":
            return self.promotion_enabled
        if phase_name == "cleanup":
            return self.cleanup_enabled
        if phase_name == "pattern_learning":
            return self.pattern_learning_enabled
        return self.enabled

    @staticmethod
    def scope_relation_expiry(metadata: Dict[str, Any]) -> float | None:
        raw_expires_at = metadata.get("expires_at")
        if raw_expires_at is not None:
            try:
                return float(raw_expires_at)
            except (TypeError, ValueError):
                return None

        raw_ttl_seconds = metadata.get("ttl_seconds")
        if raw_ttl_seconds is not None:
            try:
                ttl_seconds = int(raw_ttl_seconds)
            except (TypeError, ValueError):
                ttl_seconds = 0
            if ttl_seconds > 0:
                return datetime.now(UTC).timestamp() + float(ttl_seconds)

        return None
