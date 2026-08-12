"""
Unit tests for Admin TUI threshold validation.
"""

import pytest
from frontend.admin_tui.validation import ThresholdValidator, ValidationResult


class TestThresholdValidator:
    """Tests for threshold value validation."""

    def test_valid_soft_max(self):
        result = ThresholdValidator.validate_soft_max("5.0")
        assert result.is_valid is True
        assert result.error_message is None

    def test_invalid_soft_max_not_number(self):
        result = ThresholdValidator.validate_soft_max("abc")
        assert result.is_valid is False
        assert "must be a number" in result.error_message

    def test_invalid_soft_max_out_of_range(self):
        result = ThresholdValidator.validate_soft_max("25.0")
        assert result.is_valid is False
        assert "between" in result.error_message

    def test_valid_hard_max(self):
        result = ThresholdValidator.validate_hard_max("8.0")
        assert result.is_valid is True

    def test_valid_rds_threshold(self):
        result = ThresholdValidator.validate_rds_threshold("3.0")
        assert result.is_valid is True

    def test_valid_utility_threshold(self):
        result = ThresholdValidator.validate_utility_threshold("0.0")
        assert result.is_valid is True

    def test_valid_score_threshold(self):
        result = ThresholdValidator.validate_score_threshold("3.0")
        assert result.is_valid is True

    def test_valid_escalation_count(self):
        result = ThresholdValidator.validate_escalation_count("2")
        assert result.is_valid is True

    def test_invalid_escalation_count_zero(self):
        result = ThresholdValidator.validate_escalation_count("0")
        assert result.is_valid is False

    def test_invalid_escalation_count_not_int(self):
        result = ThresholdValidator.validate_escalation_count("2.5")
        assert result.is_valid is False

    def test_cross_field_valid(self):
        result = ThresholdValidator.validate_cross_field("5.0", "8.0")
        assert result.is_valid is True

    def test_cross_field_invalid_equal(self):
        result = ThresholdValidator.validate_cross_field("5.0", "5.0")
        assert result.is_valid is False
        assert "strictly less" in result.error_message

    def test_cross_field_invalid_greater(self):
        result = ThresholdValidator.validate_cross_field("8.0", "5.0")
        assert result.is_valid is False

    def test_validation_result_bool(self):
        valid = ValidationResult(True)
        invalid = ValidationResult(False, "Error")

        assert bool(valid) is True
        assert bool(invalid) is False

    def test_boundary_values(self):
        # Test minimum valid soft_max
        result = ThresholdValidator.validate_soft_max("0.0")
        assert result.is_valid is True

        # Test maximum valid soft_max
        result = ThresholdValidator.validate_soft_max("20.0")
        assert result.is_valid is True

        # Test just outside range
        result = ThresholdValidator.validate_soft_max("20.1")
        assert result.is_valid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
