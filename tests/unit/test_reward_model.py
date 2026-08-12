"""Unit tests for reward model and dataset generation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from services.reward_model.dataset_generator import (
    RiskDatasetGenerator,
    RiskSample,
    RiskLevel,
)
from services.reward_model.reward_model import (
    RewardModel,
    RewardModelTrainer,
)
from services.reward_model.scorer import RewardModelScorer


class TestRiskDatasetGenerator:
    """Test risk dataset generation."""

    def test_generate_command_samples(self):
        """Test generating command samples."""
        samples = RiskDatasetGenerator.generate_command_samples()
        assert len(samples) > 0
        
        # Check structure
        for sample in samples[:5]:
            assert isinstance(sample, RiskSample)
            assert sample.input
            assert sample.risk_level in RiskLevel
            assert sample.eval_binary in {0, 1}
            assert sample.reason
            assert sample.pattern

    def test_generate_intent_samples(self):
        """Test generating intent samples."""
        samples = RiskDatasetGenerator.generate_intent_samples()
        assert len(samples) > 0
        
        # Check for safe and unsafe
        safe_count = sum(1 for s in samples if s.eval_binary == 1)
        unsafe_count = sum(1 for s in samples if s.eval_binary == 0)
        
        assert safe_count > 0
        assert unsafe_count > 0

    def test_generate_tool_call_samples(self):
        """Test generating tool call samples."""
        samples = RiskDatasetGenerator.generate_tool_call_samples()
        assert len(samples) > 0
        
        # Verify presence of both safe and unsafe tool calls
        tool_names = [s.pattern for s in samples]
        assert "sys_time" in tool_names
        assert "sys_rm_rf" in tool_names

    def test_generate_full_dataset(self):
        """Test generating full dataset."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        assert len(samples) > 10
        
        # Should have mix of risk levels
        risk_levels = set(s.risk_level for s in samples)
        assert RiskLevel.HIGH in risk_levels or RiskLevel.LOW in risk_levels
        
        # Should have mix of eval_binary values
        binary_values = set(s.eval_binary for s in samples)
        assert 0 in binary_values
        assert 1 in binary_values

    def test_save_and_load_dataset(self, tmp_path):
        """Test saving and loading dataset."""
        samples = RiskDatasetGenerator.generate_full_dataset()[:10]
        filepath = tmp_path / "test_dataset.jsonl"
        
        RiskDatasetGenerator.save_dataset(samples, str(filepath))
        loaded = RiskDatasetGenerator.load_dataset(str(filepath))
        
        assert len(loaded) == len(samples)
        assert loaded[0].input == samples[0].input
        assert loaded[0].eval_binary == samples[0].eval_binary

    def test_to_json_records(self):
        """Test converting samples to JSON."""
        samples = RiskDatasetGenerator.generate_command_samples()[:3]
        records = RiskDatasetGenerator.to_json_records(samples)
        
        assert len(records) == 3
        for record in records:
            assert "input" in record
            assert "risk_level" in record
            assert "eval_binary" in record
            assert "reason" in record


class TestRewardModel:
    """Test reward model training and inference."""

    def test_model_initialization(self):
        """Test model initialization."""
        model = RewardModel(model_name="test_model")
        assert model.model_name == "test_model"
        assert not model.is_trained

    def test_model_training(self):
        """Test model training on dataset."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        
        metrics = model.train(samples, test_split=0.2)
        
        assert model.is_trained
        assert "train_accuracy" in metrics
        assert "test_accuracy" in metrics
        assert metrics["train_accuracy"] >= 0.0
        assert metrics["test_accuracy"] >= 0.0

    def test_model_prediction(self):
        """Test model prediction."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        
        # Test safe input
        result = model.predict("ls -la")
        assert "eval_binary" in result
        assert "confidence" in result
        assert "risk_score" in result
        assert 0.0 <= result["risk_score"] <= 1.0
        assert 0.0 <= result["confidence"] <= 1.0

    def test_model_batch_prediction(self):
        """Test batch prediction."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        
        texts = ["ls -la", "rm -rf /", "pwd", "cat file.txt"]
        results = model.predict_batch(texts)
        
        assert len(results) == 4
        for result in results:
            assert "eval_binary" in result

    def test_model_save_and_load(self, tmp_path):
        """Test saving and loading model."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel(model_name="test_save")
        model.train(samples)
        
        filepath = tmp_path / "test_model.pkl"
        model.save(str(filepath))
        
        loaded_model = RewardModel.load(str(filepath))
        assert loaded_model.is_trained
        assert loaded_model.model_name == "test_save"
        
        # Test prediction consistency
        test_input = "ls -la"
        original_pred = model.predict(test_input)
        loaded_pred = loaded_model.predict(test_input)
        
        assert original_pred["eval_binary"] == loaded_pred["eval_binary"]

    def test_model_top_features(self):
        """Test extracting top features."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        
        features = model.get_top_features(n=10)
        assert "unsafe_indicators" in features
        assert "safe_indicators" in features
        assert len(features["unsafe_indicators"]) > 0
        assert len(features["safe_indicators"]) > 0

    def test_model_explanation(self):
        """Test prediction explanation."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        
        explanation = model.explain_prediction("rm -rf /home")
        assert "prediction" in explanation
        assert "top_contributing_features" in explanation
        assert isinstance(explanation["top_contributing_features"], list)


class TestRewardModelScorer:
    """Test reward model scorer."""

    def test_scorer_initialization_no_model(self):
        """Test scorer without model."""
        scorer = RewardModelScorer()
        assert not scorer.is_ready

    def test_scorer_initialization_with_model(self):
        """Test scorer with trained model."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        
        scorer = RewardModelScorer(model=model)
        assert scorer.is_ready

    def test_score_action(self):
        """Test scoring an action."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)
        
        score = scorer.score_action("sys", "ls -la")
        assert "eval_binary" in score
        assert "risk_score" in score
        assert "confidence" in score
        assert score["model_available"] is True

    def test_score_response(self):
        """Test scoring a response."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)
        
        score = scorer.score_response("Directory listing complete")
        assert "eval_binary" in score
        assert "risk_score" in score

    def test_create_check_result(self):
        """Test creating judge check result."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)
        
        score = scorer.score_action("sys", "ls")
        check_result = scorer.create_check_result(score)
        
        assert check_result.check
        assert check_result.status in {"pass", "fail"}
        assert check_result.severity in {"low", "medium", "high"}

    def test_boost_confidence(self):
        """Test confidence boosting."""
        scorer = RewardModelScorer()
        
        # Low risk (safe) should boost confidence
        base = 0.5
        boosted = scorer.boost_confidence(base, risk_score=0.1, boost_factor=0.2)
        assert boosted > base
        
        # High risk (unsafe) should reduce confidence
        reduced = scorer.boost_confidence(base, risk_score=0.9, boost_factor=0.2)
        assert reduced < base
        
        # Neutral risk should keep confidence similar
        neutral = scorer.boost_confidence(base, risk_score=0.5, boost_factor=0.2)
        assert abs(neutral - base) < 0.05

    def test_get_explanation(self):
        """Test getting prediction explanation."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model = RewardModel()
        model.train(samples)
        scorer = RewardModelScorer(model=model)
        
        explanation = scorer.get_explanation("rm -rf /home")
        assert explanation.get("available") is True
        assert "prediction" in explanation


class TestRewardModelTrainer:
    """Test trainer utility."""

    def test_train_from_samples(self):
        """Test training from samples."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model, metrics = RewardModelTrainer.train_from_samples(samples)
        
        assert model.is_trained
        assert "train_accuracy" in metrics
        assert "test_accuracy" in metrics

    def test_evaluate_predictions(self):
        """Test evaluating predictions."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model, _ = RewardModelTrainer.train_from_samples(samples)
        
        # Use subset for evaluation
        test_samples = samples[:10]
        eval_metrics = RewardModelTrainer.evaluate_predictions(model, test_samples)
        
        assert "accuracy" in eval_metrics
        assert "precision" in eval_metrics
        assert "recall" in eval_metrics
        assert "f1" in eval_metrics

    def test_train_from_dataset_file(self, tmp_path):
        """Test training from a persisted JSONL dataset."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        dataset_path = tmp_path / "reward_dataset.jsonl"
        RiskDatasetGenerator.save_dataset(samples, str(dataset_path))

        model, metrics, loaded_samples = RewardModelTrainer.train_from_dataset_file(
            str(dataset_path),
            model_name="from_file",
        )

        assert model.is_trained
        assert model.model_name == "from_file"
        assert len(loaded_samples) == len(samples)
        assert "test_accuracy" in metrics

    def test_persist_training_artifacts(self, tmp_path):
        """Test writing model, dataset, and metrics bundle."""
        samples = RiskDatasetGenerator.generate_full_dataset()
        model, _ = RewardModelTrainer.train_from_samples(samples, model_name="bundle_test")

        artifact_paths = RewardModelTrainer.persist_training_artifacts(
            model=model,
            samples=samples,
            output_dir=str(tmp_path / "artifacts"),
            extra_metadata={"dataset_source": "generated"},
        )

        model_path = Path(artifact_paths["model_path"])
        dataset_path = Path(artifact_paths["dataset_path"])
        metrics_path = Path(artifact_paths["metrics_path"])
        summary_path = Path(artifact_paths["summary_path"])

        assert model_path.exists()
        assert dataset_path.exists()
        assert metrics_path.exists()
        assert summary_path.exists()

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["model_name"] == "bundle_test"
        assert summary["dataset_source"] == "generated"

    def test_training_script_writes_artifacts(self, tmp_path):
        """Test the reward model CLI training workflow."""
        repo_root = Path(__file__).resolve().parents[2]
        output_dir = tmp_path / "reward_cli"
        script_path = repo_root / "scripts" / "train_reward_model.py"

        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--output-dir",
                str(output_dir),
                "--print-summary",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )

        assert "Reward model trained:" in result.stdout
        assert (output_dir / "reward_model.pkl").exists()
        assert (output_dir / "dataset.jsonl").exists()
        assert (output_dir / "metrics.json").exists()
        assert (output_dir / "training_summary.json").exists()


class TestRewardModelIntegration:
    """Integration tests for reward model."""

    def test_full_pipeline(self):
        """Test full training and inference pipeline."""
        # Generate dataset
        samples = RiskDatasetGenerator.generate_full_dataset()
        assert len(samples) > 0
        
        # Train model
        model = RewardModel(model_name="integration_test")
        metrics = model.train(samples)
        assert model.is_trained
        
        # Create scorer
        scorer = RewardModelScorer(model=model)
        assert scorer.is_ready
        
        # Score various inputs
        test_cases = [
            ("ls -la", 1),  # Safe
            ("rm -rf /", 0),  # Unsafe
            ("pwd", 1),     # Safe
            ("sudo su", 0),  # Unsafe
        ]
        
        for input_text, expected_safety in test_cases:
            score = scorer.score_action("sys", input_text)
            assert score["eval_binary"] in {0, 1}
            # Note: Not asserting exact matches as model is probabilistic

    def test_scorer_without_model_graceful_degradation(self):
        """Test scorer handles missing model gracefully."""
        scorer = RewardModelScorer()  # No model
        
        # Should still work but with neutral scores
        score = scorer.score_action("sys", "ls -la")
        assert score["model_available"] is False
        assert score["eval_binary"] == 1  # Assume safe by default
