"""Unit tests for the orientation/plot_chart/wsl_session pre-action judge
adapters (Issue #13) and the JudgeEngine multi-tool combined-action dispatch
fix that lets them actually be reached for multi-tool turns."""

from __future__ import annotations

from services.judge.adapters.pre_action_orientation import evaluate_pre_action_orientation
from services.judge.adapters.pre_action_plot_chart import evaluate_pre_action_plot_chart
from services.judge.adapters.pre_action_wsl_session import evaluate_pre_action_wsl_session
from services.judge.contracts import JudgeContext, JudgeDecisionType, JudgeStage
from services.judge.engine import JudgeEngine


def _ctx(action: str, payload: dict | None = None, stage: JudgeStage = JudgeStage.PRE_ACTION) -> JudgeContext:
    return JudgeContext(
        request_id="req-1",
        stage=stage,
        actor="orchestrator",
        intent="tool_dispatch",
        action=action,
        input=payload or {},
    )


class TestOrientationPreAction:
    def test_allows_with_no_parameters(self):
        result = evaluate_pre_action_orientation(_ctx("orientation"))
        assert result.decision == JudgeDecisionType.ALLOW

    def test_blocks_for_wrong_action(self):
        result = evaluate_pre_action_orientation(_ctx("plot_chart"))
        assert result.decision == JudgeDecisionType.BLOCK
        assert result.checks[0].reason_code == "judge.profile.not_found"

    def test_blocks_for_wrong_stage(self):
        result = evaluate_pre_action_orientation(_ctx("orientation", stage=JudgeStage.POST_RESULT))
        assert result.decision == JudgeDecisionType.BLOCK


class TestPlotChartPreAction:
    def test_allows_default_chart_type(self):
        result = evaluate_pre_action_plot_chart(_ctx("plot_chart"))
        assert result.decision == JudgeDecisionType.ALLOW

    def test_allows_bar_chart_type(self):
        result = evaluate_pre_action_plot_chart(_ctx("plot_chart", {"chart_type": "bar"}))
        assert result.decision == JudgeDecisionType.ALLOW

    def test_warns_on_unrecognized_chart_type(self):
        result = evaluate_pre_action_plot_chart(_ctx("plot_chart", {"chart_type": "pie"}))
        assert result.decision == JudgeDecisionType.WARN

    def test_blocks_for_wrong_action(self):
        result = evaluate_pre_action_plot_chart(_ctx("orientation"))
        assert result.decision == JudgeDecisionType.BLOCK
        assert result.checks[0].reason_code == "judge.profile.not_found"


class TestWslSessionPreAction:
    def test_allows_known_lifecycle_action(self):
        result = evaluate_pre_action_wsl_session(_ctx("wsl_session", {"action": "status", "session_id": "s1"}))
        assert result.decision == JudgeDecisionType.ALLOW

    def test_revises_when_action_missing(self):
        result = evaluate_pre_action_wsl_session(_ctx("wsl_session", {}))
        assert result.decision == JudgeDecisionType.REVISE

    def test_blocks_unknown_lifecycle_action(self):
        result = evaluate_pre_action_wsl_session(_ctx("wsl_session", {"action": "nuke"}))
        assert result.decision == JudgeDecisionType.BLOCK
        assert result.checks[0].reason_code == "judge.wsl_session.unknown_action"

    def test_blocks_for_wrong_action(self):
        result = evaluate_pre_action_wsl_session(_ctx("plot_chart"))
        assert result.decision == JudgeDecisionType.BLOCK
        assert result.checks[0].reason_code == "judge.profile.not_found"


class TestJudgeEngineMultiToolDispatch:
    """Issue #13: previously context.action (a comma-joined tool-name list
    for multi-tool turns) never matched any single-action profile, so any
    multi-tool turn was unconditionally blocked regardless of which real
    tools were involved. The engine now splits and evaluates per tool."""

    def test_single_new_tool_reaches_its_profile_through_the_engine(self):
        engine = JudgeEngine()
        result = engine.evaluate_pre_action(_ctx("orientation"))
        assert result.decision == JudgeDecisionType.ALLOW

    def test_two_profiled_tools_in_one_turn_both_allow(self):
        engine = JudgeEngine()
        result = engine.evaluate_pre_action(_ctx("orientation,plot_chart"))
        assert result.decision == JudgeDecisionType.ALLOW

    def test_most_restrictive_sub_decision_wins(self):
        """wsl_session with an unknown lifecycle action must block the whole
        turn even though orientation alone would allow."""
        engine = JudgeEngine()
        result = engine.evaluate_pre_action(_ctx("orientation,wsl_session", {"action": "nuke"}))
        assert result.decision == JudgeDecisionType.BLOCK

    def test_unrecognized_tool_name_still_fails_closed(self):
        engine = JudgeEngine()
        result = engine.evaluate_pre_action(_ctx("some_future_tool"))
        assert result.decision == JudgeDecisionType.BLOCK
        assert any(c.reason_code == "judge.profile.not_found" for c in result.checks)

    def test_unrecognized_tool_combined_with_known_tool_still_blocks(self):
        engine = JudgeEngine()
        result = engine.evaluate_pre_action(_ctx("orientation,some_future_tool"))
        assert result.decision == JudgeDecisionType.BLOCK
