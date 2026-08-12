"""Integration tests for reward model judge adapters."""

import pytest
from services.reward_model import (
    RiskDatasetGenerator,
    RewardModel,
    RewardModelScorer,
)
from services.judge.adapters.reward_model_pre_action_adapter import (
    RewardModelPreActionAdapter,
)
from services.judge.adapters.reward_model_post_action_adapter import (
    RewardModelPostActionAdapter,
)


class TestRewardModelPreActionAdapter:
    """Test pre-action adapter integration."""

    def test_adapter_initialization(self):
        """Test adapter initialization."""
        adapter = RewardModelPreActionAdapter()
        assert adapter.scorer is not None
        assert not adapter.scorer.is_ready  # No model loaded

    def test_adapter_with_trained_model(self):
        """Test adapter with trained model."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPreActionAdapter(scorer=scorer)
        assert adapter.scorer.is_ready

    def test_extract_action_text(self):
        """Test extracting action text."""
        adapter = RewardModelPreActionAdapter()

        # Test with sys command
        text = adapter._extract_action_text("sys", {"command": "ls -la"})
        assert "ls -la" in text

        # Test with fallback
        text = adapter._extract_action_text("unknown", {"key": "value"})
        assert "unknown" in text

    def test_evaluate_with_reward_score(self):
        """Test full evaluation with reward score."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPreActionAdapter(scorer=scorer)

        # Evaluate safe command
        decision = adapter.evaluate_with_reward_score(
            action="sys",
            input_data={"command": "ls -la"},
            context={},
        )

        assert decision is not None
        assert decision.decision in {"allow", "block", "revise"}
        assert 0.0 <= decision.confidence <= 1.0
        assert len(decision.checks) > 0

    def test_augmented_decision_with_model(self):
        """Test decision augmentation."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPreActionAdapter(scorer=scorer)

        # Test with safe action
        decision = adapter.evaluate_with_reward_score(
            action="sys",
            input_data={"command": "ls"},
            context={},
        )

        # Should be allow or high confidence
        assert decision.decision in {"allow", "warn"}
        assert len(decision.checks) > 0

    def test_explain_decision(self):
        """Test decision explanation."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPreActionAdapter(scorer=scorer)

        explanation = adapter.explain_decision(
            action="sys", input_data={"command": "pwd"}
        )

        assert "action" in explanation
        assert "action_text" in explanation
        assert "reward_model_explanation" in explanation


class TestRewardModelPostActionAdapter:
    """Test post-action adapter integration."""

    def test_adapter_initialization(self):
        """Test adapter initialization."""
        adapter = RewardModelPostActionAdapter()
        assert adapter.scorer is not None

    def test_extract_response_text(self):
        """Test extracting response text."""
        adapter = RewardModelPostActionAdapter()

        # Test with different result formats
        text1 = adapter._extract_response_text({"output": "test output"})
        assert "test output" in text1

        text2 = adapter._extract_response_text({"stdout": "command output"})
        assert "command output" in text2

        # Test truncation
        long_output = "x" * 1000
        text3 = adapter._extract_response_text({"output": long_output})
        assert len(text3) <= 500

    def test_evaluate_with_reward_score(self):
        """Test evaluating execution result."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPostActionAdapter(scorer=scorer)

        decision = adapter.evaluate_with_reward_score(
            action="sys",
            input_data={"command": "ls"},
            result={"output": "/home\n/var\n/usr"},
            context={},
        )

        assert decision is not None
        assert decision.decision in {"allow", "block", "revise"}
        assert len(decision.checks) > 0

    def test_validate_response_safety(self):
        """Test response safety validation."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPostActionAdapter(scorer=scorer)

        validation = adapter.validate_response_safety(
            response="Directory listing: file1 file2 file3",
            action="sys",
            context={},
        )

        assert "action" in validation
        assert "safe" in validation
        assert "eval_binary" in validation
        assert "risk_score" in validation
        assert validation["safe"] in {True, False}


class TestRewardModelJudgeIntegration:
    """End-to-end integration tests."""

    def test_full_action_evaluation_flow(self):
        """Test complete action evaluation with reward model."""
        # Setup
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        pre_adapter = RewardModelPreActionAdapter(scorer=scorer)
        post_adapter = RewardModelPostActionAdapter(scorer=scorer)

        # Simulate action flow: pre-check -> execution -> post-check
        test_cases = [
            {
                "action": "sys",
                "input": {"command": "ls -la /home"},
                "result": {"output": "user1\nuser2"},
            },
            {
                "action": "sys",
                "input": {"command": "pwd"},
                "result": {"output": "/home/user"},
            },
        ]

        for test_case in test_cases:
            # Pre-action check
            pre_decision = pre_adapter.evaluate_with_reward_score(
                action=test_case["action"],
                input_data=test_case["input"],
                context={},
            )
            assert pre_decision.decision in {"allow", "block", "revise"}

            # Post-action check (only if pre-decision allows)
            if pre_decision.decision == "allow":
                post_decision = post_adapter.evaluate_with_reward_score(
                    action=test_case["action"],
                    input_data=test_case["input"],
                    result=test_case["result"],
                    context={},
                )
                assert post_decision.decision in {"allow", "block", "revise"}

    def test_reward_model_confidence_impact(self):
        """Test reward model's impact on confidence scores."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)

        adapter = RewardModelPreActionAdapter(scorer=scorer)

        # Safe command
        safe_decision = adapter.evaluate_with_reward_score(
            action="sys",
            input_data={"command": "ls"},
            context={},
        )

        # Unsafe command
        unsafe_decision = adapter.evaluate_with_reward_score(
            action="sys",
            input_data={"command": "rm -rf /"},
            context={},
        )

        # Confidence should reflect risk level
        # Safe decision should have higher confidence and be allow
        assert safe_decision.confidence >= unsafe_decision.confidence or safe_decision.decision == "allow"
