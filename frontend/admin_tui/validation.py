"""
Helper utilities for Admin TUI form validation.
"""

from typing import Tuple, Optional
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of validation."""

    is_valid: bool
    error_message: Optional[str] = None

    def __bool__(self) -> bool:
        return self.is_valid


class ThresholdValidator:
    """Validates threshold values and constraints."""

    # Valid ranges for thresholds
    SOFT_MAX_MIN = 0.0
    SOFT_MAX_MAX = 20.0
    HARD_MAX_MIN = 0.0
    HARD_MAX_MAX = 20.0
    RDS_THRESHOLD_MIN = 0.0
    RDS_THRESHOLD_MAX = 20.0
    UTILITY_THRESHOLD_MIN = -10.0
    UTILITY_THRESHOLD_MAX = 10.0
    SCORE_THRESHOLD_MIN = 0.0
    SCORE_THRESHOLD_MAX = 10.0
    ESCALATION_COUNT_MIN = 1
    ESCALATION_COUNT_MAX = 100

    @staticmethod
    def validate_soft_max(value: str) -> ValidationResult:
        """Validate soft_max field."""
        try:
            v = float(value)
        except ValueError:
            return ValidationResult(False, "soft_max must be a number")

        if v < ThresholdValidator.SOFT_MAX_MIN or v > ThresholdValidator.SOFT_MAX_MAX:
            return ValidationResult(
                False,
                f"soft_max must be between {ThresholdValidator.SOFT_MAX_MIN} and {ThresholdValidator.SOFT_MAX_MAX}",
            )

        return ValidationResult(True)

    @staticmethod
    def validate_hard_max(value: str) -> ValidationResult:
        """Validate hard_max field."""
        try:
            v = float(value)
        except ValueError:
            return ValidationResult(False, "hard_max must be a number")

        if v < ThresholdValidator.HARD_MAX_MIN or v > ThresholdValidator.HARD_MAX_MAX:
            return ValidationResult(
                False,
                f"hard_max must be between {ThresholdValidator.HARD_MAX_MIN} and {ThresholdValidator.HARD_MAX_MAX}",
            )

        return ValidationResult(True)

    @staticmethod
    def validate_rds_threshold(value: str) -> ValidationResult:
        """Validate RDS observe threshold."""
        try:
            v = float(value)
        except ValueError:
            return ValidationResult(False, "rds_observe_threshold must be a number")

        if (
            v < ThresholdValidator.RDS_THRESHOLD_MIN
            or v > ThresholdValidator.RDS_THRESHOLD_MAX
        ):
            return ValidationResult(False, "rds_observe_threshold must be between 0 and 20")

        return ValidationResult(True)

    @staticmethod
    def validate_utility_threshold(value: str) -> ValidationResult:
        """Validate utility negative threshold."""
        try:
            v = float(value)
        except ValueError:
            return ValidationResult(False, "utility_negative_threshold must be a number")

        if (
            v < ThresholdValidator.UTILITY_THRESHOLD_MIN
            or v > ThresholdValidator.UTILITY_THRESHOLD_MAX
        ):
            return ValidationResult(False, "utility_negative_threshold must be between -10 and 10")

        return ValidationResult(True)

    @staticmethod
    def validate_score_threshold(value: str) -> ValidationResult:
        """Validate score weak threshold."""
        try:
            v = float(value)
        except ValueError:
            return ValidationResult(False, "score_weak_threshold must be a number")

        if (
            v < ThresholdValidator.SCORE_THRESHOLD_MIN
            or v > ThresholdValidator.SCORE_THRESHOLD_MAX
        ):
            return ValidationResult(False, "score_weak_threshold must be between 0 and 10")

        return ValidationResult(True)

    @staticmethod
    def validate_escalation_count(value: str) -> ValidationResult:
        """Validate weak score escalation count."""
        try:
            v = int(value)
        except ValueError:
            return ValidationResult(False, "weak_score_escalation_count must be an integer")

        if (
            v < ThresholdValidator.ESCALATION_COUNT_MIN
            or v > ThresholdValidator.ESCALATION_COUNT_MAX
        ):
            return ValidationResult(
                False,
                f"weak_score_escalation_count must be between {ThresholdValidator.ESCALATION_COUNT_MIN} and {ThresholdValidator.ESCALATION_COUNT_MAX}",
            )

        return ValidationResult(True)

    @staticmethod
    def validate_cross_field(soft_max: str, hard_max: str) -> ValidationResult:
        """Validate relationship between soft_max and hard_max."""
        try:
            soft = float(soft_max)
            hard = float(hard_max)
        except ValueError:
            return ValidationResult(False, "Both values must be numeric")

        if soft >= hard:
            return ValidationResult(False, "soft_max must be strictly less than hard_max")

        return ValidationResult(True)
