"""Integration tests for Orchestrator Judge flow (Pre-Action and Post-Result).

Tests that the orchestrator correctly:
1. Evaluates pre-action judge decisions before tool dispatch
2. Blocks tool dispatch on judge block decisions  
3. Evaluates post-result judge decisions after LLM response
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.judge import JudgeEngine, JudgeContext, JudgeStage, JudgeDecision, JudgeDecisionType


class TestOrchestratorJudgeContextCreation:
    """Test JudgeContext creation helpers (unit-level)."""

    def test_judge_context_creation_for_pre_action(self):
        """Verify JudgeContext is correctly created for pre-action evaluation."""
        # Import locally to avoid full orchestrator initialization
        from services.orchestrator.orchestrator import Orchestrator
        
        # Create a simple test instance to call helper method
        orch = object.__new__(Orchestrator)
        orch._active_session_id = "session_123"
        orch._active_user_id = "user_456"
        
        context = orch._create_judge_context_for_pre_action(
            run_id="run_789",
            tool_names=["sys", "compute.run"],
            query="Calculate turbine power with 1500 RPM and 200 Nm torque",
        )
        
        assert context.request_id == "run_789"
        assert context.stage == JudgeStage.PRE_ACTION
        assert context.actor == "orchestrator"
        assert context.intent == "tool_dispatch"
        assert "sys" in context.action and "compute.run" in context.action
        assert context.input["tools"] == ["sys", "compute.run"]
        assert context.metadata["session_id"] == "session_123"
        assert context.metadata["user_id"] == "user_456"

    def test_judge_context_creation_for_post_result(self):
        """Verify JudgeContext is correctly created for post-result evaluation."""
        from services.orchestrator.orchestrator import Orchestrator
        
        orch = object.__new__(Orchestrator)
        orch._active_session_id = "session_123"
        orch._active_user_id = "user_456"
        
        response_text = "The turbine produces 31.4159 kW at 1500 RPM with 200 Nm torque."
        
        context = orch._create_judge_context_for_post_result(
            run_id="run_789",
            query="What is the turbine power?",
            response_content=response_text,
            tools_used=["compute.run"],
            tool_outputs={"compute.run": {"power_kw": 31.4159}},
        )
        
        assert context.request_id == "run_789"
        assert context.stage == JudgeStage.POST_RESULT
        assert context.actor == "orchestrator"
        assert context.intent == "response_validation"
        assert context.action == "validate_response"
        assert context.input["original_query"] == "What is the turbine power?"
        assert context.input["response"] == response_text
        assert context.input["tools_used"] == ["compute.run"]
        assert context.input["tool_outputs"] == {"compute.run": {"power_kw": 31.4159}}
        assert context.metadata["response_length"] == len(response_text)


class TestJudgeEngine:
    """Test JudgeEngine decision-making (engine level)."""

    def test_pre_action_allow_for_sys_tool(self):
        """Verify JudgeEngine.evaluate_pre_action handles sys tool."""
        engine = JudgeEngine()
        
        context = JudgeContext(
            request_id="run_test",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="tool_dispatch",
            action="sys",
            input={
                "tools": ["sys"],
                "query": "ls",
                "command": "ls",  # sys adapter expects command field
                "args": ["-la"],  # Structured mode with args
            },
            metadata={"source": "orchestrator"},
        )
        
        decision = engine.evaluate_pre_action(context)
        # Should ALLOW or WARN with structured args
        assert decision.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}

    def test_pre_action_allow_for_compute_run(self):
        """Verify JudgeEngine.evaluate_pre_action returns ALLOW for compute.run with valid input."""
        engine = JudgeEngine()
        
        context = JudgeContext(
            request_id="run_test",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="tool_dispatch",
            action="compute.run",
            input={
                "tools": ["compute.run"],
                "query": "Calculate power",
                "model": "turbine_power",
                "inputs": {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0},
            },
            metadata={"source": "orchestrator"},
        )
        
        decision = engine.evaluate_pre_action(context)
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.confidence >= 0.8

    def test_pre_action_block_for_unknown_profile(self):
        """Verify JudgeEngine.evaluate_pre_action returns BLOCK for unknown profile (default deny)."""
        engine = JudgeEngine()
        
        context = JudgeContext(
            request_id="run_test",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="tool_dispatch",
            action="unknown_tool",  # No profile defined
            input={"tools": ["unknown_tool"]},
            metadata={"source": "orchestrator"},
        )
        
        decision = engine.evaluate_pre_action(context)
        assert decision.decision == JudgeDecisionType.BLOCK
        assert len(decision.issues) > 0

    def test_post_result_allow_for_valid_response(self):
        """Verify JudgeEngine.evaluate_post_result returns ALLOW for valid response."""
        engine = JudgeEngine()
        
        context = JudgeContext(
            request_id="run_test",
            stage=JudgeStage.POST_RESULT,
            actor="orchestrator",
            intent="response_validation",
            action="validate_response",
            input={
                "query": "Calculate turbine power",
                "response": "The turbine produces 31.4159 kW at 1500 RPM with 200 Nm torque.",
                "tools_used": ["compute.run"],
            },
            metadata={"source": "orchestrator"},
        )
        
        decision = engine.evaluate_post_result(context)
        # Decision depends on ResponseValidator - should be ALLOW or WARN at worst
        assert decision.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}

    def test_post_result_receives_successful_tool_evidence(self):
        engine = JudgeEngine()
        context = JudgeContext(
            request_id="run_tool_evidence",
            stage=JudgeStage.POST_RESULT,
            actor="orchestrator",
            intent="response_validation",
            action="validate_response",
            input={
                "original_query": "Rufe die Kartendaten ab",
                "response": "Der Tool-Aufruf ergab Clay Revenant und Lehm-Wiedergänger.",
                "tools_used": ["sys"],
                "tool_outputs": {
                    "sys": {
                        "source": "sys",
                        "kind": "url_fetch",
                        "content": '{"name":"Clay Revenant","printed_name":"Lehm-Wiedergänger"}',
                    }
                },
            },
            metadata={"source": "orchestrator"},
        )

        decision = engine.evaluate_post_result(context)

        assert decision.decision == JudgeDecisionType.ALLOW
        assert not any("no successful tool result" in issue for issue in decision.issues)


class TestJudgeDecisionTypes:
    """Test JudgeDecision helper factory methods."""

    def test_allow_decision_factory(self):
        """Test JudgeDecision.allow() factory method."""
        decision = JudgeDecision.allow()
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.passed is True
        # Default confidence for ALLOW is 1.0 (100% certainty)
        assert decision.confidence == 1.0

    def test_warn_decision_factory(self):
        """Test JudgeDecision.warn() factory method."""
        decision = JudgeDecision.warn(
            confidence=0.7,
            issues=["Command uses legacy shell mode"],
        )
        assert decision.decision == JudgeDecisionType.WARN
        assert decision.passed is True  # Warn allows execution
        assert decision.confidence == 0.7
        assert "Command uses legacy shell mode" in decision.issues

    def test_revise_decision_factory(self):
        """Test JudgeDecision.revise() factory method."""
        decision = JudgeDecision.revise(
            confidence=0.5,
            issues=["Response needs grounding"],
        )
        assert decision.decision == JudgeDecisionType.REVISE
        assert decision.passed is False  # Revise blocks execution
        assert decision.confidence == 0.5

    def test_block_decision_factory(self):
        """Test JudgeDecision.block() factory method."""
        decision = JudgeDecision.block(
            confidence=1.0,
            issues=["Tool not in allowlist"],
        )
        assert decision.decision == JudgeDecisionType.BLOCK
        assert decision.passed is False
        assert decision.confidence == 1.0


class TestIntegrationScenarios:
    """Integration scenarios testing Judge decisions with tool contexts."""

    def test_sys_tool_pre_action_scenario(self):
        """Scenario: sys tool dispatch with valid command."""
        engine = JudgeEngine()
        
        # Create pre-action context for sys tool with structured args
        pre_context = JudgeContext(
            request_id="run_scenario_1",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="tool_dispatch",
            action="sys",
            input={
                "tools": ["sys"],
                "query": "List files in workspace",
                "command": "ls",  # Use a simpler command
                "args": ["/home/liara/workspace"],  # Allowed search path
            },
            metadata={"source": "orchestrator", "risk_hint": "low"},
        )
        
        decision = engine.evaluate_pre_action(pre_context)
        # Should ALLOW or WARN with structured args
        assert decision.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}
        assert decision.confidence >= 0.7

    def test_compute_run_pre_action_scenario(self):
        """Scenario: compute.run dispatch with valid inputs."""
        engine = JudgeEngine()
        
        pre_context = JudgeContext(
            request_id="run_scenario_2",
            stage=JudgeStage.PRE_ACTION,
            actor="orchestrator",
            intent="tool_dispatch",
            action="compute.run",
            input={
                "tools": ["compute.run"],
                "query": "Calculate turbine power",
                "model": "turbine_power",
                "inputs": {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0},
            },
            metadata={"source": "orchestrator", "risk_hint": "low"},
        )
        
        decision = engine.evaluate_pre_action(pre_context)
        assert decision.decision == JudgeDecisionType.ALLOW
        assert decision.confidence >= 0.8

    def test_post_result_scenario(self):
        """Scenario: Validate LLM response after tool execution."""
        engine = JudgeEngine()
        
        post_context = JudgeContext(
            request_id="run_scenario_3",
            stage=JudgeStage.POST_RESULT,
            actor="orchestrator",
            intent="response_validation",
            action="validate_response",
            input={
                "query": "What is the power of a turbine at 1500 RPM with 200 Nm torque?",
                "response": "Based on the simulation, the power is 31.4159 kW.",
                "tools_used": ["compute.run"],
            },
            metadata={"source": "orchestrator", "response_length": 50},
        )
        
        decision = engine.evaluate_post_result(post_context)
        assert decision.decision in {JudgeDecisionType.ALLOW, JudgeDecisionType.WARN}
        assert len(decision.checks) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
