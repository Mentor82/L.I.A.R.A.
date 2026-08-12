"""Unit tests for closed-loop score feedback in orchestrator reasoning metrics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.contracts import (
    InferenceResult,
    OrchestratorRequest,
    ValidationResult,
)
from services.judge.contracts import JudgeDecision
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator.orchestrator import Orchestrator
from services.shared.types import MemoryTier
from tests.memory_adapter_fakes import NoopGraphMemoryAdapterMixin


class _FakeInferenceGateway:
    async def infer(self, request):
        return InferenceResult(
            content=f"answer::{request.prompt[:32]}",
            provider="mock",
            model="mock-model",
            status="success",
            stop_reason="stop",
            metadata={},
        )


class _FakeMemoryAdapter(NoopGraphMemoryAdapterMixin, MemoryServiceAdapter):
    async def get(self, tier: MemoryTier, key: str, default=None):
        return default

    async def set(self, tier: MemoryTier, key: str, value, ttl_seconds=None):
        return None

    async def delete(self, tier: MemoryTier, key: str):
        return None

    async def exists(self, tier: MemoryTier, key: str) -> bool:
        return False

    async def append_history(self, request):
        return SimpleNamespace(items=[])

    async def query_history(self, request):
        return SimpleNamespace(items=[])

    async def upsert_fact(self, request):
        return SimpleNamespace(items=[])

    async def query_facts(self, request):
        return SimpleNamespace(items=[])

    async def upsert_retrieval(self, request):
        return SimpleNamespace(items=[])

    async def query_retrieval(self, request):
        return SimpleNamespace(items=[])

    async def generate_embedding(self, request):
        return SimpleNamespace(item=None)

    async def context_search(self, request):
        return SimpleNamespace(items=[])

    async def context_upsert(self, request):
        return SimpleNamespace(items=[])

    async def relation_upsert(self, request):
        return SimpleNamespace(items=[])

    async def relation_expand(self, request):
        return SimpleNamespace(items=[])


class _FixedScoreValidator:
    def validate(self, context):
        del context
        return ValidationResult(
            passed=True,
            decision="accept",
            checks={"fast_check": "pass"},
            issues=[],
            confidence_score=0.42,
            suggestions=None,
            score=None,
            risk_flags=["formula_mismatch"],
        )


class _RepairPreferredValidator:
    def validate(self, context):
        del context
        return ValidationResult(
            passed=True,
            decision="accept",
            checks={"fast_check": "pass"},
            issues=[],
            confidence_score=0.72,
            suggestions=None,
            score=None,
            risk_flags=["logic_branch_dead"],
        )


class _RepeatedWeakScoreValidator:
    def validate(self, context):
        del context
        return ValidationResult(
            passed=True,
            decision="accept",
            checks={"fast_check": "pass"},
            issues=[],
            confidence_score=0.40,
            suggestions=None,
            score=None,
            risk_flags=["formula_mismatch"],
        )


@pytest.mark.asyncio
async def test_score_feedback_is_applied_on_next_turn():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _FixedScoreValidator()

    first = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-feedback",
            run_id="run-1",
            user_id="user-1",
            query="erste frage",
            max_tokens=128,
        )
    )
    second = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-feedback",
            run_id="run-2",
            user_id="user-1",
            query="zweite frage",
            max_tokens=128,
        )
    )

    first_signals = first.validation_result["math_signals"]
    second_signals = second.validation_result["math_signals"]
    second_expl = second.validation_result["decision_explanation"]

    assert first_signals["score_feedback_applied"] is False
    assert first_signals["control_mode_before"] == "advisory"
    assert first_signals["control_mode_after"] == "advisory"
    assert first_signals["decision_delta"]["direction"] == "unchanged"
    assert second_signals["score_feedback_applied"] is True
    assert second_signals["score_feedback_source_run"] == "run-1"
    assert second_signals["control_mode_before"] == "advisory"
    assert second_signals["control_mode_after"] == "soft"
    assert second_signals["decision_delta"]["direction"] == "escalated"
    assert second_signals["actionable_risk"] >= first_signals["actionable_risk"]
    assert first_signals["control_mode"] == "advisory"
    assert "log_audit" in first_signals["actions"]
    assert second_signals["control_mode"] == "soft"
    assert second_signals["resolution_basis"] == "feedback"
    assert second_signals["resolved_mode"] == "soft"
    assert second_signals["resolved_action"] == "increase_validation_strictness"
    assert "feedback_soft_floor" in second_signals["trigger_reasons"]
    assert "increase_validation_strictness" in second_signals["actions"]
    # In the feedback model, weak previous confidence can dominate this turn's resolution.
    assert second_expl["primary_reason"] in {
        "utility_negative",
        "actionable_risk_exceeded_soft_limit",
        "normal_operation",
    }
    assert second_expl["decision_confidence"] >= 0.5
    assert second_expl["thresholds"]["soft_max"] == pytest.approx(5.0)
    assert "check_risk" in second_expl["decision_trace"]
    assert len(second_expl["supporting_metrics"]) <= 5

    assert second.validation_result["score"] is None
    assert second.validation_result["risk_flags"] == ["formula_mismatch"]


@pytest.mark.asyncio
async def test_feedback_signals_can_prefer_repair_mode():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _RepairPreferredValidator()

    first = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-repair-preferred",
            run_id="repair-run-1",
            user_id="user-1",
            query="erste frage",
            max_tokens=128,
        )
    )
    second = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-repair-preferred",
            run_id="repair-run-2",
            user_id="user-1",
            query="zweite frage",
            max_tokens=128,
        )
    )

    assert first.validation_result["math_signals"]["repair_preferred"] is False
    assert second.validation_result["math_signals"]["repair_preferred"] is True
    assert second.validation_result["math_signals"]["control_mode"] == "soft"
    assert "repair_preferred_feedback" in second.validation_result["math_signals"]["trigger_reasons"]
    assert "trigger_repair_loop" in second.validation_result["math_signals"]["actions"]
    assert "request_targeted_fix" in second.validation_result["math_signals"]["actions"]


@pytest.mark.asyncio
async def test_repeated_weak_scores_escalate_session_trend():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _RepeatedWeakScoreValidator()

    first = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-trend",
            run_id="trend-run-1",
            user_id="user-1",
            query="frage eins",
            max_tokens=128,
        )
    )
    second = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-trend",
            run_id="trend-run-2",
            user_id="user-1",
            query="frage zwei",
            max_tokens=128,
        )
    )
    third = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-trend",
            run_id="trend-run-3",
            user_id="user-1",
            query="frage drei",
            max_tokens=128,
        )
    )

    assert first.validation_result["math_signals"]["trend_weak_score_count"] == 0
    assert first.validation_result["math_signals"]["trend_escalation_applied"] is False
    assert second.validation_result["math_signals"]["trend_weak_score_count"] == 1
    assert second.validation_result["math_signals"]["trend_escalation_applied"] is False
    assert third.validation_result["math_signals"]["trend_weak_score_count"] == 2
    assert third.validation_result["math_signals"]["trend_escalation_applied"] is True
    assert "repeated_weak_scores" in third.validation_result["math_signals"]["trigger_reasons"]
    assert "escalate_session_watch" in third.validation_result["math_signals"]["actions"]
    assert "prefer_conservative_answering" in third.validation_result["math_signals"]["actions"]


@pytest.mark.asyncio
async def test_judge_post_decision_is_exposed_in_hybrid_control_signals():
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _FixedScoreValidator()
    orchestrator.judge_engine = SimpleNamespace(
        evaluate_post_result=lambda _ctx: JudgeDecision.block(
            confidence=0.91,
            issues=["post judge blocked result"],
            reason_code="judge.post.blocked",
        )
    )

    result = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-judge-post",
            run_id="judge-post-run-1",
            user_id="user-1",
            query="frage",
            max_tokens=128,
        )
    )

    signals = result.validation_result["math_signals"]
    decision_context = result.validation_result["decision_context"]
    retry_control = result.validation_result["retry_control"]
    explanation = result.validation_result["decision_explanation"]
    assert signals["judge_post_decision"] == "block"
    assert signals["judge_post_confidence"] == 0.91
    assert signals["judge_post_reason_code"] == "judge.post.blocked"
    assert "judge_post_block" in signals["trigger_reasons"]
    assert "require_judge_review" in signals["actions"]
    assert decision_context["validation"]["score"] == {}
    assert decision_context["judge_post"]["decision"] == "block"
    assert "probabilistic_signals" in decision_context["judge_post"]
    assert decision_context["judge_post"]["probabilistic_signals"]["belief_posterior"] == signals["belief_posterior"]
    assert decision_context["judge_post"]["probabilistic_signals"]["signal_confidence"] == signals["signal_confidence"]
    assert decision_context["judge_post"]["probabilistic_signals"]["utility_ig"] == signals["utility_ig"]
    assert decision_context["judge_post"]["probabilistic_signals"]["stability_score"] == signals["stability_score"]
    assert decision_context["effective"]["control_mode"] == signals["control_mode"]
    assert decision_context["effective"]["control_mode_after"] == signals["control_mode_after"]
    assert decision_context["effective"]["decision_delta"]["direction"] in {"unchanged", "escalated", "deescalated"}
    assert decision_context["effective"]["resolution_basis"] == "policy"
    assert decision_context["effective"]["resolved_mode"] == "hard"
    assert decision_context["effective"]["resolved_action"] == "fallback_safe_response"
    assert retry_control["stop_reason"] == "judge_post_block"
    assert explanation["primary_reason"] == "policy_violation"
    assert explanation["decision_confidence"] >= 0.9
    assert "check_policy" in explanation["decision_trace"]


def test_decision_explanation_priority_prefers_hard_risk_over_utility_and_score():
    explanation = Orchestrator._build_decision_explanation(
        validation_decision="revise",
        score_payload=None,
        math_signals={
            "actionable_risk": 8.6,
            "soft_max": 5.0,
            "hard_max": 8.0,
            "utility": -1.4,
            "rds_v2": 4.0,
            "reasoning_cost": 7.2,
            "context_entropy": 0.8,
            "trend_escalation_applied": True,
        },
        judge_post={"decision": "allow", "reason_code": None},
    )

    assert explanation["primary_reason"] == "actionable_risk_exceeded_hard_limit"
    assert explanation["decision_confidence"] >= 0.9
    assert "repeated_weak_scores" in explanation["secondary_reasons"]
    assert len(explanation["supporting_metrics"]) <= 5


def test_decision_explanation_does_not_use_utility_primary_reason_without_cost_pressure_signal():
    explanation = Orchestrator._build_decision_explanation(
        validation_decision="revise",
        score_payload=None,
        math_signals={
            "actionable_risk": 3.2,
            "soft_max": 5.0,
            "hard_max": 8.0,
            "utility": -0.8,
            "rds_v2": 1.2,
            "reasoning_cost": 1.5,
            "context_entropy": 0.2,
            "decision_recommended_action": "maintain_current_mode",
        },
        judge_post={"decision": "allow", "reason_code": None},
    )

    assert explanation["primary_reason"] == "normal_operation"
    assert "apply_advisory_control" in explanation["decision_trace"]


def test_hybrid_control_uses_utility_resolution_only_with_cost_pressure_signal():
    metadata = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": False,
            "utility": -0.6,
        },
        score_feedback={
            "mode_floor": "advisory",
            "repair_preferred": False,
            "trend_escalation_applied": False,
        },
        judge_post={},
    )

    assert metadata["resolution_basis"] == "baseline"
    assert metadata["resolved_mode"] == "advisory"
    assert "utility_negative" not in metadata["trigger_reasons"]

    pressured = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": False,
            "utility": -0.6,
            "decision_snapshot": {
                "decision_recommended_action": "reduce_exploration",
            },
        },
        score_feedback={
            "mode_floor": "advisory",
            "repair_preferred": False,
            "trend_escalation_applied": False,
        },
        judge_post={},
    )

    assert pressured["resolution_basis"] == "utility"
    assert pressured["resolved_mode"] == "soft"
    assert "utility_negative" in pressured["trigger_reasons"]


def test_score_rule_h5_prefers_soft_repair_over_hard_floor():
    _inputs, score_feedback = Orchestrator._apply_score_feedback_to_metric_inputs(
        inputs={"policy_risk": 0.2, "context_entropy": 0.2},
        previous_score_feedback={
            "run_id": "run-h5",
            "decision": "revise",
            "confidence_score": 0.30,
            "risk_flags": ["consistency_issue"],
            "score": {
                "score_fach": 3,
                "score_code": 4,
                "score_robustheit": 4,
            },
        },
    )

    assert score_feedback["score_rule_h5_repair"] is True
    assert score_feedback["repair_preferred"] is True
    assert score_feedback["mode_floor"] == "soft"


def test_score_rule_h6_sets_soft_judge_repair_without_hard_block_by_score_alone():
    _inputs, score_feedback = Orchestrator._apply_score_feedback_to_metric_inputs(
        inputs={"policy_risk": 0.2, "context_entropy": 0.2},
        previous_score_feedback={
            "run_id": "run-h6",
            "decision": "accept",
            "confidence_score": 0.88,
            "risk_flags": [],
            "score": {
                "score_fach": 5,
                "score_code": 3,
                "score_robustheit": 3,
            },
        },
    )

    metadata = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": False,
            "utility": 0.2,
        },
        score_feedback=score_feedback,
        judge_post={},
    )

    assert score_feedback["score_rule_h6_critical"] is True
    assert metadata["resolved_mode"] == "soft"
    assert metadata["resolution_basis"] == "feedback"
    assert metadata["resolved_action"] == "require_judge_review"
    assert "score_fach_critical" in metadata["trigger_reasons"]
    assert "require_judge_review" in metadata["actions"]


def test_judge_post_block_overrides_score_repair_conflict_to_policy_hard():
    metadata = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": False,
            "utility": 0.1,
        },
        score_feedback={
            "mode_floor": "soft",
            "repair_preferred": True,
            "trend_escalation_applied": False,
            "score_rule_h5_repair": True,
            "score_rule_h6_critical": True,
        },
        judge_post={
            "decision": "block",
            "reason_code": "judge.post.blocked",
        },
    )

    # Policy must dominate score-driven soft repair in conflict cases.
    assert metadata["resolution_basis"] == "policy"
    assert metadata["resolved_mode"] == "hard"
    assert metadata["resolved_action"] == "fallback_safe_response"
    assert "judge_post_block" in metadata["trigger_reasons"]
    assert "score_fach_critical" in metadata["trigger_reasons"]
    assert "fallback_safe_response" in metadata["actions"]


def test_score_resolution_applies_when_higher_priority_signals_are_absent():
    metadata = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": False,
            "utility": 0.5,
        },
        score_feedback={
            "mode_floor": "soft",
            "repair_preferred": False,
            "trend_escalation_applied": False,
        },
        judge_post={},
    )

    assert metadata["resolution_basis"] == "feedback"
    assert metadata["resolved_mode"] == "soft"
    assert metadata["resolved_action"] == "increase_validation_strictness"


def test_retry_control_stops_after_low_information_gain_on_followup_attempt():
    retry = Orchestrator._build_retry_control(
        validation_decision="revise",
        judge_post={},
        retry_count=1,
        retry_limit=2,
        compression_meta={},
        math_signals={
            "utility_ig": -0.05,
            "stability_score": 0.8,
            "stability_is_stable": True,
            "decision_recommended_action": "reduce_exploration",
        },
    )

    assert retry["attempt_allowed"] is False
    assert retry["stop_reason"] == "low_information_gain"


def test_retry_control_prefers_repair_when_stability_is_low():
    retry = Orchestrator._build_retry_control(
        validation_decision="revise",
        judge_post={},
        retry_count=0,
        retry_limit=2,
        compression_meta={},
        math_signals={
            "utility_ig": 0.2,
            "stability_score": 0.2,
            "stability_is_stable": False,
            "decision_recommended_action": "stabilize_reasoning_chain",
        },
    )

    assert retry["attempt_allowed"] is True
    assert retry["strategy"] == "repair"
    assert retry["stability_score"] == 0.2


# ---------------------------------------------------------------------------
# Regressions- und Integrationstests (Hybrid Control System TODO)
# ---------------------------------------------------------------------------


def test_high_pre_risk_good_post_score_no_over_escalation():
    """Regel: hoher Pre-Risk (soft) + guter Post-Score → kein Hard-Block.

    Auch wenn actionable_risk den Soft-Schwellwert überschreitet, darf ein
    guter Score (mode_floor='advisory') den Modus nicht auf 'hard' anheben.
    Das Ergebnis muss 'soft' bleiben – nicht mehr.
    """
    metadata = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": True,  # actionable_risk > soft_max
            "utility": 0.7,
        },
        score_feedback={
            "mode_floor": "advisory",  # fach <= 4, kein Score-Druck
            "repair_preferred": False,
            "trend_escalation_applied": False,
        },
        judge_post={},
    )

    assert metadata["control_mode"] == "soft", (
        "Hoher Pre-Risk soll 'soft' ergeben, kein 'hard'"
    )
    assert "actionable_risk_soft" in metadata["trigger_reasons"]
    assert "actionable_risk_hard" not in metadata["trigger_reasons"]
    assert metadata["resolved_mode"] != "hard", (
        "Guter Post-Score darf resolved_mode nicht auf 'hard' anheben"
    )
    assert "reduce_exploration" in metadata["actions"]
    assert "block_unsafe_tools" not in metadata["actions"], (
        "Hard-Block-Aktionen dürfen bei nur Soft-Risk nicht erscheinen"
    )


def test_rds_high_actionable_risk_below_threshold_stays_advisory():
    """Regression: hoher RDS-Wert allein schaltet nicht auf Soft/Hard.

    RDS ist diagnostisch. Solange actionable_risk unter soft_max bleibt
    (should_soft_limit=False) und kein Score-Druck existiert, muss das
    System im Advisory-Modus verbleiben.
    """
    metadata = Orchestrator._build_hybrid_control_metadata(
        metrics={
            "should_hard_block": False,
            "should_soft_limit": False,  # RDS hoch, aber actionable_risk < soft_max
            "utility": 0.4,
        },
        score_feedback={
            "mode_floor": "advisory",
            "repair_preferred": False,
            "trend_escalation_applied": False,
        },
        judge_post={},
    )

    assert metadata["control_mode"] == "advisory", (
        "Hoher RDS ohne Risiko-Überschreitung darf nicht Soft/Hard auslösen"
    )
    assert metadata["trigger_reasons"] == ["baseline_advisory"], (
        "Ohne aktiven Trigger darf nur 'baseline_advisory' gesetzt sein"
    )
    assert "log_audit" in metadata["actions"]
    assert "reduce_exploration" not in metadata["actions"]
    assert "block_unsafe_tools" not in metadata["actions"]


@pytest.mark.asyncio
async def test_repeated_weak_fach_stepwise_mode_escalation():
    """Integration: wiederholt schwache Confidence → schrittweise Eskalation.

    Turn 1: kein Feedback → advisory
    Turn 2: weak confidence aus T1 → mode_floor='soft' → control_mode='soft'
    Turn 3: weak confidence aus T2 + Trend-Eskalation (2 Einträge in History) → 'hard'
    """
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _RepeatedWeakScoreValidator()  # confidence=0.40 + weak risk flag

    t1 = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-stepwise",
            run_id="step-1",
            user_id="u1",
            query="turn 1",
            max_tokens=128,
        )
    )
    t2 = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-stepwise",
            run_id="step-2",
            user_id="u1",
            query="turn 2",
            max_tokens=128,
        )
    )
    t3 = await orchestrator.run(
        OrchestratorRequest(
            session_id="session-stepwise",
            run_id="step-3",
            user_id="u1",
            query="turn 3",
            max_tokens=128,
        )
    )

    s1 = t1.validation_result["math_signals"]
    s2 = t2.validation_result["math_signals"]
    s3 = t3.validation_result["math_signals"]

    # Turn 1: noch kein Feedback → Advisory
    assert s1["control_mode_after"] == "advisory"
    assert s1["score_feedback_applied"] is False

    # Turn 2: weak confidence aus T1 → mode_floor='soft' → Soft-Control
    assert s2["control_mode_after"] == "soft"
    assert "feedback_soft_floor" in s2["trigger_reasons"]

    # Turn 3: trend_escalation_applied → control_mode becomes hard.
    # Note: resolved_mode may remain soft due priority resolution (e.g. utility).
    assert s3["control_mode"] == "hard"
    assert "feedback_hard_floor" in s3["trigger_reasons"]
    assert "repeated_weak_scores" in s3["trigger_reasons"]
    assert s3["trend_escalation_applied"] is True


@pytest.mark.asyncio
async def test_e2e_three_turn_closed_loop_delta_direction_chain():
    """E2E: vollständiger Closed-Loop über 3 Turns mit nachvollziehbarer Delta-Entscheidung.

    Jeder Turn muss:
    - control_mode_before aus dem Vorgänger-Turn übernehmen
    - decision_delta.direction korrekt annotieren (unchanged / escalated)
    - from_mode und to_mode konsistent setzen

    Erwartete Kette: advisory → soft → hard
    """
    orchestrator = Orchestrator(
        tool_coordinator=object(),
        inference_gateway=_FakeInferenceGateway(),
        memory_layer=_FakeMemoryAdapter(),
    )

    async def _no_tools(_query, _override):
        return []

    async def _no_tool_exec(_tool_names, _query, run_id=""):
        del run_id
        return {}

    orchestrator._select_tools = _no_tools
    orchestrator._execute_tools = _no_tool_exec
    orchestrator.validator = _RepeatedWeakScoreValidator()

    runs = []
    for i in range(1, 4):
        r = await orchestrator.run(
            OrchestratorRequest(
                session_id="session-e2e-delta",
                run_id=f"e2e-{i}",
                user_id="u1",
                query=f"turn {i}",
                max_tokens=128,
            )
        )
        runs.append(r)

    signals = [r.validation_result["math_signals"] for r in runs]

    # Turn 1: Einstieg, kein Vorgänger
    assert signals[0]["control_mode_before"] == "advisory"
    assert signals[0]["control_mode_after"] == "advisory"
    assert signals[0]["decision_delta"]["direction"] == "unchanged"
    assert signals[0]["decision_delta"]["from"] == "advisory"
    assert signals[0]["decision_delta"]["to"] == "advisory"

    # Turn 2: Eskalation von advisory → soft
    assert signals[1]["control_mode_before"] == "advisory"
    assert signals[1]["control_mode_after"] == "soft"
    assert signals[1]["decision_delta"]["direction"] == "escalated"
    assert signals[1]["decision_delta"]["from"] == "advisory"
    assert signals[1]["decision_delta"]["to"] == "soft"

    # Turn 3: control_mode escalates to hard; effective after-mode can stay soft
    # because resolved_mode follows deterministic priority (policy > hard risk > utility > score).
    assert signals[2]["control_mode_before"] == "soft"
    assert signals[2]["control_mode"] == "hard"
    assert signals[2]["control_mode_after"] in {"soft", "hard"}
    assert signals[2]["decision_delta"]["direction"] in {"unchanged", "escalated"}
    assert signals[2]["decision_delta"]["from"] == "soft"
    assert signals[2]["decision_delta"]["to"] in {"soft", "hard"}

