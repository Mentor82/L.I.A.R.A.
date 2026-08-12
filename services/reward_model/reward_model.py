"""Reward model for risk classification.

Trains a classifier to predict eval_binary (0=unsafe, 1=safe) based on input text.
Uses TF-IDF + Logistic Regression for fast, interpretable classification.

The model learns patterns from Judge-labeled data without requiring actual
dangerous execution - only pattern recognition on text.
"""

from __future__ import annotations

import pickle
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import warnings

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
)

from services.reward_model.dataset_generator import RiskDatasetGenerator, RiskSample, RiskLevel


class RewardModel:
    """Risk classification reward model."""

    def __init__(self, model_name: str = "risk_classifier_v1"):
        """Initialize reward model.
        
        Args:
            model_name: Identifier for the model
        """
        self.model_name = model_name
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.classifier: Optional[LogisticRegression] = None
        self.is_trained = False
        self.training_metrics: Dict[str, float] = {}

    def train(
        self,
        samples: List[RiskSample],
        test_split: float = 0.2,
        random_state: int = 42,
    ) -> Dict[str, Any]:
        """Train the reward model on risk samples.
        
        Args:
            samples: List of RiskSample training data
            test_split: Fraction of data for testing
            random_state: Random seed for reproducibility
            
        Returns:
            Dictionary with training metrics
        """
        if not samples:
            raise ValueError("No training samples provided")

        # Prepare data
        inputs = [sample.input for sample in samples]
        labels = [sample.eval_binary for sample in samples]

        # Split into train/test
        split_idx = int(len(samples) * (1 - test_split))
        train_inputs = inputs[:split_idx]
        train_labels = labels[:split_idx]
        test_inputs = inputs[split_idx:]
        test_labels = labels[split_idx:]

        # === Vectorization ===
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            ngram_range=(1, 2),
            lowercase=True,
            stop_words="english",
            min_df=1,
            max_df=0.95,
        )
        X_train = self.vectorizer.fit_transform(train_inputs)
        X_test = self.vectorizer.transform(test_inputs)

        # === Classification ===
        self.classifier = LogisticRegression(
            max_iter=1000,
            random_state=random_state,
            C=1.0,
            class_weight="balanced",  # Handle class imbalance
        )
        self.classifier.fit(X_train, train_labels)

        # === Evaluation ===
        train_pred = self.classifier.predict(X_train)
        test_pred = self.classifier.predict(X_test)
        test_proba = self.classifier.predict_proba(X_test)[:, 1]

        self.training_metrics = {
            "train_accuracy": float(accuracy_score(train_labels, train_pred)),
            "test_accuracy": float(accuracy_score(test_labels, test_pred)),
            "test_precision": float(precision_score(test_labels, test_pred, zero_division=0)),
            "test_recall": float(recall_score(test_labels, test_pred, zero_division=0)),
            "test_f1": float(f1_score(test_labels, test_pred, zero_division=0)),
            "test_auc_roc": float(roc_auc_score(test_labels, test_proba)) if len(np.unique(test_labels)) > 1 else 0.0,
            "train_size": len(train_inputs),
            "test_size": len(test_inputs),
        }

        self.is_trained = True
        return self.training_metrics

    def predict(self, text: str) -> Dict[str, Any]:
        """Predict risk classification for input text.
        
        Args:
            text: Input text to classify
            
        Returns:
            Dictionary with prediction and confidence
        """
        if not self.is_trained or self.classifier is None or self.vectorizer is None:
            raise RuntimeError("Model not trained. Call train() first.")

        X = self.vectorizer.transform([text])
        pred = self.classifier.predict(X)[0]
        proba = self.classifier.predict_proba(X)[0]

        return {
            "input": text,
            "eval_binary": int(pred),  # 0 = unsafe, 1 = safe
            "confidence": float(proba[pred]),
            "probability_unsafe": float(proba[0]),  # P(unsafe)
            "probability_safe": float(proba[1]),   # P(safe)
            "risk_score": float(proba[0]),  # 0.0 = safe, 1.0 = unsafe
        }

    def predict_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        """Predict risk for multiple inputs.
        
        Args:
            texts: List of input texts
            
        Returns:
            List of prediction dictionaries
        """
        return [self.predict(text) for text in texts]

    def save(self, filepath: str) -> None:
        """Save trained model to disk.
        
        Args:
            filepath: Path to save model
        """
        if not self.is_trained:
            raise RuntimeError("Model not trained. Cannot save.")

        model_data = {
            "model_name": self.model_name,
            "vectorizer": self.vectorizer,
            "classifier": self.classifier,
            "training_metrics": self.training_metrics,
        }

        with open(filepath, "wb") as f:
            pickle.dump(model_data, f)

    @classmethod
    def load(cls, filepath: str) -> RewardModel:
        """Load trained model from disk.
        
        Args:
            filepath: Path to saved model
            
        Returns:
            Loaded RewardModel instance
        """
        with open(filepath, "rb") as f:
            model_data = pickle.load(f)

        instance = cls(model_name=model_data["model_name"])
        instance.vectorizer = model_data["vectorizer"]
        instance.classifier = model_data["classifier"]
        instance.training_metrics = model_data["training_metrics"]
        instance.is_trained = True

        return instance

    def get_top_features(self, n: int = 20) -> Dict[str, List[Tuple[str, float]]]:
        """Get the most important features for classification.
        
        Args:
            n: Number of features to return
            
        Returns:
            Dictionary with safe and unsafe features
        """
        if not self.is_trained or self.classifier is None or self.vectorizer is None:
            raise RuntimeError("Model not trained.")

        feature_names = self.vectorizer.get_feature_names_out()
        coef = self.classifier.coef_[0]

        # Top unsafe features (negative coefficients)
        unsafe_indices = np.argsort(coef)[:n]
        unsafe_features = [
            (feature_names[i], float(coef[i])) for i in unsafe_indices
        ]

        # Top safe features (positive coefficients)
        safe_indices = np.argsort(-coef)[:n]
        safe_features = [
            (feature_names[i], float(coef[i])) for i in safe_indices
        ]

        return {
            "unsafe_indicators": unsafe_features,
            "safe_indicators": safe_features,
        }

    def explain_prediction(self, text: str) -> Dict[str, Any]:
        """Explain a prediction with feature importance.
        
        Args:
            text: Input text to explain
            
        Returns:
            Explanation dictionary
        """
        pred = self.predict(text)
        
        if self.vectorizer is None or self.classifier is None:
            raise RuntimeError("Model not trained.")

        X = self.vectorizer.transform([text])
        features = self.vectorizer.get_feature_names_out()
        feature_scores = X.toarray()[0]
        coef = self.classifier.coef_[0]

        # Top contributing features
        feature_importance = []
        for i, score in enumerate(feature_scores):
            if score > 0:
                impact = score * coef[i]
                feature_importance.append({
                    "feature": features[i],
                    "tf_idf_score": float(score),
                    "coefficient": float(coef[i]),
                    "impact": float(impact),
                })

        # Sort by absolute impact
        feature_importance.sort(
            key=lambda x: abs(x["impact"]),
            reverse=True,
        )

        return {
            "prediction": pred,
            "top_contributing_features": feature_importance[:10],
        }


class RewardModelTrainer:
    """Utility for training and managing reward models."""

    @staticmethod
    def train_from_samples(
        samples: List[RiskSample],
        model_name: str = "risk_classifier_v1",
        test_split: float = 0.2,
        random_state: int = 42,
    ) -> Tuple[RewardModel, Dict[str, float]]:
        """Train a reward model from risk samples.
        
        Args:
            samples: List of RiskSample instances
            model_name: Name for the model
            test_split: Test data fraction
            random_state: Random seed for reproducibility
            
        Returns:
            Tuple of (trained_model, metrics)
        """
        model = RewardModel(model_name=model_name)
        metrics = model.train(samples, test_split=test_split, random_state=random_state)
        return model, metrics

    @staticmethod
    def train_from_dataset_file(
        dataset_path: str,
        model_name: str = "risk_classifier_v1",
        test_split: float = 0.2,
        random_state: int = 42,
    ) -> Tuple[RewardModel, Dict[str, float], List[RiskSample]]:
        """Load a JSONL dataset from disk and train a model from it."""
        samples = RiskDatasetGenerator.load_dataset(dataset_path)
        model, metrics = RewardModelTrainer.train_from_samples(
            samples,
            model_name=model_name,
            test_split=test_split,
            random_state=random_state,
        )
        return model, metrics, samples

    @staticmethod
    def evaluate_predictions(
        model: RewardModel,
        test_samples: List[RiskSample],
    ) -> Dict[str, Any]:
        """Evaluate model predictions on test samples.
        
        Args:
            model: Trained RewardModel
            test_samples: Test samples to evaluate
            
        Returns:
            Evaluation metrics
        """
        predictions = model.predict_batch([s.input for s in test_samples])
        true_labels = [s.eval_binary for s in test_samples]
        pred_labels = [p["eval_binary"] for p in predictions]

        return {
            "accuracy": float(accuracy_score(true_labels, pred_labels)),
            "precision": float(precision_score(true_labels, pred_labels, zero_division=0)),
            "recall": float(recall_score(true_labels, pred_labels, zero_division=0)),
            "f1": float(f1_score(true_labels, pred_labels, zero_division=0)),
            "num_test_samples": len(test_samples),
        }

    @staticmethod
    def persist_training_artifacts(
        *,
        model: RewardModel,
        samples: List[RiskSample],
        output_dir: str,
        dataset_filename: str = "dataset.jsonl",
        model_filename: str = "reward_model.pkl",
        metrics_filename: str = "metrics.json",
        summary_filename: str = "training_summary.json",
        extra_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, str]:
        """Write model, dataset, and training metadata to a single output directory."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        model_path = output_path / model_filename
        dataset_path = output_path / dataset_filename
        metrics_path = output_path / metrics_filename
        summary_path = output_path / summary_filename

        model.save(str(model_path))
        RiskDatasetGenerator.save_dataset(samples, str(dataset_path))

        metrics = dict(model.training_metrics or {})
        metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")

        summary = {
            "model_name": model.model_name,
            "sample_count": len(samples),
            "model_path": str(model_path),
            "dataset_path": str(dataset_path),
            "metrics_path": str(metrics_path),
            "metrics": metrics,
            **dict(extra_metadata or {}),
        }
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

        return {
            "output_dir": str(output_path),
            "model_path": str(model_path),
            "dataset_path": str(dataset_path),
            "metrics_path": str(metrics_path),
            "summary_path": str(summary_path),
        }
