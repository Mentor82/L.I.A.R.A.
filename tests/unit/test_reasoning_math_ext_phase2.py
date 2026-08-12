"""
Unit tests for Phase 2 functions in services/orchestrator/reasoning_math_ext.py

Covers:
- information_gain
- confidence_weighted_utility
- temporal_discount
- compute_utility_snapshot
"""
from __future__ import annotations

import math

import pytest

from services.orchestrator.reasoning_math_ext import (
    confidence_weighted_utility,
    compute_utility_snapshot,
    information_gain,
    temporal_discount,
)


# ---------------------------------------------------------------------------
# information_gain
# ---------------------------------------------------------------------------


class TestInformationGain:
    def test_gain_when_entropy_decreases(self) -> None:
        result = information_gain(0.8, 0.4)
        assert math.isclose(result["ig"], 0.4, rel_tol=1e-5)
        assert result["direction"] == "gain"

    def test_loss_when_entropy_increases(self) -> None:
        result = information_gain(0.3, 0.7)
        assert math.isclose(result["ig"], -0.4, rel_tol=1e-5)
        assert result["direction"] == "loss"

    def test_neutral_when_equal(self) -> None:
        result = information_gain(0.5, 0.5)
        assert math.isclose(result["ig"], 0.0, abs_tol=1e-6)
        assert result["direction"] == "neutral"

    def test_clamps_entropy_to_01(self) -> None:
        result = information_gain(-0.5, 1.5)
        # clamped: entropy_before=0.0, entropy_after=1.0 → ig = -1.0
        assert math.isclose(result["ig"], -1.0, rel_tol=1e-5)
        assert result["direction"] == "loss"

    def test_audit_fields_present(self) -> None:
        result = information_gain(0.6, 0.3)
        assert result["compute_backend"] == "python"
        assert result["compute_path"] == "primary"
        for field in ("ig", "entropy_before", "entropy_after", "direction"):
            assert field in result


# ---------------------------------------------------------------------------
# confidence_weighted_utility
# ---------------------------------------------------------------------------


class TestConfidenceWeightedUtility:
    def test_zero_entropy_passes_utility_through(self) -> None:
        result = confidence_weighted_utility(7.0, 0.0)
        assert math.isclose(result["weighted_utility"], 7.0, rel_tol=1e-5)
        assert math.isclose(result["discount_factor"], 1.0, rel_tol=1e-5)

    def test_full_entropy_zeros_out_utility(self) -> None:
        result = confidence_weighted_utility(7.0, 1.0)
        assert math.isclose(result["weighted_utility"], 0.0, abs_tol=1e-6)
        assert math.isclose(result["discount_factor"], 0.0, abs_tol=1e-6)

    def test_half_entropy_halves_utility(self) -> None:
        result = confidence_weighted_utility(10.0, 0.5)
        assert math.isclose(result["weighted_utility"], 5.0, rel_tol=1e-5)

    def test_negative_utility_stays_negative(self) -> None:
        result = confidence_weighted_utility(-4.0, 0.5)
        assert result["weighted_utility"] < 0

    def test_audit_fields_present(self) -> None:
        result = confidence_weighted_utility(3.0, 0.4)
        assert result["compute_backend"] == "python"
        assert result["compute_path"] == "primary"
        for field in ("weighted_utility", "utility", "entropy", "discount_factor"):
            assert field in result


# ---------------------------------------------------------------------------
# temporal_discount
# ---------------------------------------------------------------------------


class TestTemporalDiscount:
    def test_step_0_no_discount(self) -> None:
        result = temporal_discount(5.0, 0, gamma=0.9)
        assert math.isclose(result["discounted_value"], 5.0, rel_tol=1e-5)
        assert math.isclose(result["discount_weight"], 1.0, rel_tol=1e-5)

    def test_step_1_applies_gamma(self) -> None:
        result = temporal_discount(10.0, 1, gamma=0.9)
        assert math.isclose(result["discounted_value"], 9.0, rel_tol=1e-5)

    def test_step_10_strong_discount(self) -> None:
        result = temporal_discount(1.0, 10, gamma=0.9)
        # 0.9^10 ≈ 0.3487
        assert math.isclose(result["discounted_value"], 0.9**10, rel_tol=1e-4)

    def test_gamma_1_no_decay(self) -> None:
        result = temporal_discount(8.0, 5, gamma=1.0)
        assert math.isclose(result["discounted_value"], 8.0, rel_tol=1e-5)

    def test_negative_step_clamped_to_0(self) -> None:
        result = temporal_discount(4.0, -3, gamma=0.8)
        assert result["step"] == 0
        assert math.isclose(result["discounted_value"], 4.0, rel_tol=1e-5)

    def test_audit_fields_present(self) -> None:
        result = temporal_discount(1.0, 2)
        assert result["compute_backend"] == "python"
        assert result["compute_path"] == "primary"
        for field in ("discounted_value", "value", "step", "gamma", "discount_weight"):
            assert field in result


# ---------------------------------------------------------------------------
# compute_utility_snapshot
# ---------------------------------------------------------------------------


class TestComputeUtilitySnapshot:
    def test_all_prefixed_fields_present(self) -> None:
        result = compute_utility_snapshot(
            utility=5.0,
            entropy_before=0.6,
            entropy_after=0.3,
            step=2,
        )
        expected = (
            "utility_ig",
            "utility_entropy_before",
            "utility_entropy_after",
            "utility_ig_direction",
            "utility_weighted",
            "utility_raw",
            "utility_discount_factor",
            "utility_discounted",
            "utility_step",
            "utility_gamma",
            "utility_discount_weight",
        )
        for key in expected:
            assert key in result, f"Missing key: {key}"

    def test_audit_fields_python(self) -> None:
        result = compute_utility_snapshot(3.0, 0.4, 0.2, 0)
        assert result["utility_compute_backend"] == "python"
        assert result["utility_compute_path"] == "primary"

    def test_ig_direction_gain(self) -> None:
        result = compute_utility_snapshot(5.0, entropy_before=0.8, entropy_after=0.2, step=0)
        assert result["utility_ig_direction"] == "gain"
        assert result["utility_ig"] > 0.0

    def test_temporal_discount_at_step_0_no_decay(self) -> None:
        result = compute_utility_snapshot(10.0, 0.0, 0.0, step=0, gamma=0.9)
        # entropy=0 → discount_factor=1.0 → weighted=10.0; step=0 → discounted=10.0
        assert math.isclose(result["utility_discounted"], 10.0, rel_tol=1e-5)

    def test_high_entropy_reduces_utility(self) -> None:
        low_entropy = compute_utility_snapshot(10.0, 0.0, 0.1, step=0)
        high_entropy = compute_utility_snapshot(10.0, 0.0, 0.9, step=0)
        assert low_entropy["utility_weighted"] > high_entropy["utility_weighted"]
