"""Reward model scorer for judge integration.

Integrates learned risk classification into pre/post-action judge decisions.
Provides probabilistic risk scores that augment judge confidence.

Uses pre-trained reward model to score inputs/outputs and boost or reduce
judge confidence based on learned patterns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional
from services.judge.contracts import JudgeCheckResult

if TYPE_CHECKING:
    from services.reward_model.reward_model import RewardModel


class RewardModelScorer:
    """Scores inputs/outputs using learned reward model."""

    def __init__(self, model: Optional["RewardModel"] = None):
        """Initialize scorer with optional pre-trained model.
        
        Args:
            model: Optional pre-trained RewardModel instance
        """
        self.model = model
        self.is_ready = model is not None and model.is_trained

    def score_action(
        self,
        action: str,
        input_text: str,
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Score an action for safety risk.
        
        Args:
            action: Action name (e.g., "sys", "compute.run")
            input_text: Text representation of action input
            context: Additional context
            
        Returns:
            Score dictionary with eval_binary and confidence
        """
        if not self.is_ready:
            # If model not available, return neutral score
            return {
                "model_available": False,
                "eval_binary": 1,  # Assume safe if no model
                "risk_score": 0.5,
                "confidence": 0.5,
                "source": "no_model",
            }

        # Get prediction from model
        prediction = self.model.predict(input_text)

        return {
            "model_available": True,
            "eval_binary": prediction["eval_binary"],
            "risk_score": prediction["risk_score"],
            "confidence": prediction["confidence"],
            "probability_safe": prediction["probability_safe"],
            "probability_unsafe": prediction["probability_unsafe"],
            "source": "reward_model",
        }

    def score_response(
        self,
        response: str,
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Score a response/output for safety.
        
        Args:
            response: Response text to score
            context: Additional context
            
        Returns:
            Score dictionary
        """
        if not self.is_ready:
            return {
                "model_available": False,
                "eval_binary": 1,  # Assume safe
                "risk_score": 0.5,
                "confidence": 0.5,
                "source": "no_model",
            }

        prediction = self.model.predict(response)
        return {
            "model_available": True,
            "eval_binary": prediction["eval_binary"],
            "risk_score": prediction["risk_score"],
            "confidence": prediction["confidence"],
            "source": "reward_model",
        }

    def create_check_result(
        self,
        score: Dict[str, Any],
        check_name: str = "reward_model_score",
    ) -> JudgeCheckResult:
        """Convert score to JudgeCheckResult.
        
        Args:
            score: Score dictionary from score_action or score_response
            check_name: Name of the check
            
        Returns:
            JudgeCheckResult
        """
        risk_score = score.get("risk_score", 0.5)
        eval_binary = score.get("eval_binary", 1)
        confidence = score.get("confidence", 0.5)

        # Determine status and severity
        if eval_binary == 1:  # Safe
            status = "pass"
            severity = "low"
            reason_code = None
            message = f"Reward model predicts safe (risk_score={risk_score:.2f})"
        else:  # Unsafe
            status = "fail"
            severity = "high" if risk_score > 0.7 else "medium"
            reason_code = "reward_model.unsafe_prediction"
            message = f"Reward model predicts unsafe (risk_score={risk_score:.2f})"

        return JudgeCheckResult(
            check=check_name,
            status=status,
            severity=severity,
            reason_code=reason_code,
            message=message,
        )

    def boost_confidence(
        self,
        base_confidence: float,
        risk_score: float,
        boost_factor: float = 0.1,
    ) -> float:
        """Boost or reduce confidence based on risk score.
        
        Args:
            base_confidence: Base confidence from judge
            risk_score: Risk score from reward model (0.0=safe, 1.0=unsafe)
            boost_factor: How much to adjust confidence
            
        Returns:
            Adjusted confidence
        """
        # If risk_score is low (safe), boost confidence
        # If risk_score is high (unsafe), reduce confidence
        adjustment = (0.5 - risk_score) * boost_factor * 2
        adjusted = base_confidence + adjustment
        return max(0.0, min(1.0, adjusted))  # Clamp to [0, 1]

    def get_explanation(self, input_text: str) -> Dict[str, Any]:
        """Get explanation for a prediction.
        
        Args:
            input_text: Input to explain
            
        Returns:
            Explanation dictionary
        """
        if not self.is_ready:
            return {
                "available": False,
                "reason": "Model not available",
            }

        try:
            explanation = self.model.explain_prediction(input_text)
            return {
                "available": True,
                **explanation,
            }
        except Exception as e:
            return {
                "available": False,
                "reason": str(e),
            }
