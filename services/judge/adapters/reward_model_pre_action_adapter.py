"""Pre-action judge adapter using reward model scoring.

Integrates learned risk classification into pre-action judge flow to augment
policy-based decisions with probabilistic risk scores.

Provides standalone reward model scoring without requiring integration
with policy-based adapters.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, List
from services.judge.contracts import (
    JudgeCheckResult,
    JudgeDecision,
)
from services.reward_model.scorer import RewardModelScorer


class RewardModelPreActionAdapter:
    """Pre-action adapter using reward model scoring."""

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
        context: Dict[str, Any] | None = None,
    ) -> JudgeDecision:
        """Evaluate action with reward model scoring.
        
        Args:
            action: Action name (e.g., "sys")
            input_data: Action input data
            context: Additional context
            
        Returns:
            Judge decision based on reward model score
        """
        # Extract action text for scoring
        action_text = self._extract_action_text(action, input_data)
        
        # Score the action with reward model
        reward_score = self.scorer.score_action(action, action_text, context)

        # Create judge decision from reward score
        return self._create_decision_from_score(reward_score, action, action_text)

    def _extract_action_text(
        self,
        action: str,
        input_data: Dict[str, Any],
    ) -> str:
        """Extract text representation for reward scoring.
        
        Args:
            action: Action name
            input_data: Input data
            
        Returns:
            Text representation
        """
        if action == "sys":
            # For sys actions, use the command
            if "command" in input_data:
                return str(input_data["command"])
            elif "cmd" in input_data:
                return str(input_data["cmd"])
        
        # Fallback: combine action and input
        return f"{action}: {str(input_data)[:200]}"

    def _create_decision_from_score(
        self,
        reward_score: Dict[str, Any],
        action: str,
        action_text: str,
    ) -> JudgeDecision:
        """Create JudgeDecision from reward score.
        
        Args:
            reward_score: Score from reward model
            action: Action name
            action_text: Action text
            
        Returns:
            Judge decision
        """
        eval_binary = reward_score.get("eval_binary", 1)
        confidence = reward_score.get("confidence", 0.5)
        risk_score = reward_score.get("risk_score", 0.5)
        model_available = reward_score.get("model_available", False)

        # Create check result
        check = JudgeCheckResult(
            check="reward_model_risk_score",
            status="pass" if eval_binary == 1 else "fail",
            severity="high" if eval_binary == 0 and risk_score > 0.7 else "low",
            reason_code=None,
            message=f"Reward model: {'safe' if eval_binary == 1 else 'unsafe'} (risk={risk_score:.2f})",
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
            # Safe prediction
            decision = JudgeDecision.allow(
                confidence=confidence,
                checks=[check],
                issues=[],
                constraints={},
            )
        else:
            # Unsafe prediction
            decision = JudgeDecision.block(
                confidence=confidence,
                checks=[check],
                issues=[f"Reward model predicts unsafe action: {action_text[:50]}"],
                constraints={
                    "reward_model_block": {
                        "severity": "high",
                        "reason": f"Unsafe prediction (risk={risk_score:.2f})",
                        "confidence": confidence,
                    }
                },
            )

        return decision

    def explain_decision(
        self,
        action: str,
        input_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Get detailed explanation for action evaluation.
        
        Args:
            action: Action name
            input_data: Action input
            
        Returns:
            Detailed explanation
        """
        action_text = self._extract_action_text(action, input_data)
        
        return {
            "action": action,
            "action_text": action_text,
            "reward_model_explanation": self.scorer.get_explanation(action_text),
        }

