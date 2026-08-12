"""Post-action judge adapter using reward model scoring.

Validates tool execution responses using learned risk classification
to ensure the actual output is safe and appropriate.

Provides standalone reward model scoring for response validation
without requiring integration with policy-based adapters.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from services.judge.contracts import (
    JudgeCheckResult,
    JudgeDecision,
)
from services.reward_model.scorer import RewardModelScorer


class RewardModelPostActionAdapter:
    """Post-action adapter using reward model for response validation."""

    def __init__(self, scorer: Optional[RewardModelScorer] = None):
        """Initialize adapter with optional scorer.
        
        Args:
            scorer: Optional pre-configured RewardModelScorer
        """
        self.scorer = scorer or RewardModelScorer()

    def evaluate_with_reward_score(
        self,
        action: str,
        input_data: Dict[str, Any],
        result: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> JudgeDecision:
        """Evaluate action result with reward model scoring.
        
        Args:
            action: Action name (e.g., "sys")
            input_data: Original action input
            result: Actual execution result
            context: Additional context
            
        Returns:
            Judge decision based on response safety
        """
        # Extract response text for scoring
        response_text = self._extract_response_text(result)
        
        # Score the response for safety
        reward_score = self.scorer.score_response(response_text, context)

        # Create judge decision from reward score
        return self._create_decision_from_score(reward_score, action, response_text)

    def _extract_response_text(self, result: Dict[str, Any]) -> str:
        """Extract response text for reward scoring.
        
        Args:
            result: Execution result
            
        Returns:
            Text representation of response
        """
        # Try common result fields
        if "output" in result:
            text = str(result["output"])
        elif "stdout" in result:
            text = str(result["stdout"])
        elif "response" in result:
            text = str(result["response"])
        elif "message" in result:
            text = str(result["message"])
        else:
            text = str(result)

        # Limit to first 500 chars for scoring
        return text[:500] if text else ""

    def _create_decision_from_score(
        self,
        reward_score: Dict[str, Any],
        action: str,
        response_text: str,
    ) -> JudgeDecision:
        """Create JudgeDecision from response safety score.
        
        Args:
            reward_score: Score from reward model
            action: Action name
            response_text: Response text
            
        Returns:
            Judge decision
        """
        eval_binary = reward_score.get("eval_binary", 1)
        confidence = reward_score.get("confidence", 0.5)
        risk_score = reward_score.get("risk_score", 0.5)
        model_available = reward_score.get("model_available", False)

        # Create check result
        check = JudgeCheckResult(
            check="reward_model_response_safety",
            status="pass" if eval_binary == 1 else "fail",
            severity="high" if eval_binary == 0 and risk_score > 0.7 else "low",
            reason_code=None,
            message=f"Response safety: {'safe' if eval_binary == 1 else 'unsafe'} (risk={risk_score:.2f})",
        )

        # Determine decision
        if not model_available:
            # No model available - default to allow with medium confidence
            decision = JudgeDecision.allow(
                confidence=0.5,
                checks=[check],
                issues=["Reward model not available; defaulting to allow"],
                constraints={},
            )
        elif eval_binary == 1:
            # Safe response
            decision = JudgeDecision.allow(
                confidence=confidence,
                checks=[check],
                issues=[],
                constraints={},
            )
        else:
            # Unsafe response
            decision = JudgeDecision.block(
                confidence=confidence,
                checks=[check],
                issues=["Reward model detects unsafe response content"],
                constraints={
                    "response_safety_issue": {
                        "severity": "high",
                        "reason": f"Unsafe response detected (risk={risk_score:.2f})",
                        "confidence": confidence,
                    }
                },
            )

        return decision

    def validate_response_safety(
        self,
        response: str,
        action: str,
        context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Validate a response for safety concerns.
        
        Args:
            response: Response text to validate
            action: Action that generated response
            context: Additional context
            
        Returns:
            Validation result
        """
        score = self.scorer.score_response(response, context)
        
        return {
            "action": action,
            "response_length": len(response),
            "eval_binary": score.get("eval_binary", 1),
            "risk_score": score.get("risk_score", 0.5),
            "confidence": score.get("confidence", 0.5),
            "safe": score.get("eval_binary", 1) == 1,
            "model_available": score.get("model_available", False),
            "explanation": self.scorer.get_explanation(response),
        }
