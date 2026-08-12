"""
Unit tests for services/orchestrator/reasoning_math_ext.py

Covers Phase 1 math primitives:
- bayes_update
- kalman_belief_update
- compute_signal_variance
- compute_belief_snapshot
"""
from __future__ import annotations

import math

import pytest

from services.orchestrator.reasoning_math_ext import (
    bayes_update,
    compute_belief_snapshot,
    compute_signal_variance,
    kalman_belief_update,
)


# ---------------------------------------------------------------------------
# bayes_update
# ---------------------------------------------------------------------------


class TestBayesUpdate:
    def test_prior_05_likelihood_05_posterior_05(self) -> None:
        """With equal prior and likelihood, posterior stays at 0.5."""
        result = bayes_update(
            {"prior": 0.5, "entropy": 0.0, "variance": 0.0},
            {"likelihood": 0.5, "signal": 0.5, "entropy": 0.0},
        )
        assert math.isclose(result["posterior"], 0.5, rel_tol=1e-6)

    def test_high_likelihood_raises_posterior(self) -> None:
        result = bayes_update(
            {"prior": 0.5, "entropy": 0.0, "variance": 0.0},
            {"likelihood": 0.9, "signal": 0.9, "entropy": 0.0},
        )
        assert result["posterior"] > 0.5

    def test_low_likelihood_lowers_posterior(self) -> None:
        result = bayes_update(
            {"prior": 0.5, "entropy": 0.0, "variance": 0.0},
            {"likelihood": 0.1, "signal": 0.1, "entropy": 0.0},
        )
        assert result["posterior"] < 0.5

    def test_posterior_bounded_01(self) -> None:
        result = bayes_update(
            {"prior": 0.99, "entropy": 0.0, "variance": 0.0},
            {"likelihood": 0.99, "signal": 1.0, "entropy": 0.0},
        )
        assert 0.0 <= result["posterior"] <= 1.0

    def test_audit_fields_present(self) -> None:
        result = bayes_update(
            {"prior": 0.6, "entropy": 0.1, "variance": 0.01},
            {"likelihood": 0.7, "signal": 0.8, "entropy": 0.1},
        )
        assert result["compute_backend"] == "python"
        assert result["compute_path"] == "primary"
        assert "prior" in result
        assert "likelihood" in result
        assert "marginal" in result
        assert "posterior" in result


# ---------------------------------------------------------------------------
# kalman_belief_update
# ---------------------------------------------------------------------------


class TestKalmanBeliefUpdate:
    def test_estimate_moves_toward_signal(self) -> None:
        belief = {"prior": 0.3, "entropy": 0.0, "variance": 0.1}
        observation = {"likelihood": 0.5, "signal": 0.9, "entropy": 0.0}
        result = kalman_belief_update(belief, observation, kalman_gain=0.5)
        # With K=0.5, estimate = 0.3 + 0.5*(0.9 - 0.3) = 0.6
        assert math.isclose(result["estimate"], 0.6, rel_tol=1e-6)

    def test_zero_kalman_gain_no_update(self) -> None:
        belief = {"prior": 0.4, "entropy": 0.0, "variance": 0.0}
        observation = {"likelihood": 0.5, "signal": 0.9, "entropy": 0.0}
        result = kalman_belief_update(belief, observation, kalman_gain=0.0)
        # K=0 → estimate = prior + 0 * (signal - prior) = prior
        assert math.isclose(result["estimate"], 0.4, rel_tol=1e-5)

    def test_full_kalman_gain_converges_to_signal(self) -> None:
        belief = {"prior": 0.2, "entropy": 0.0, "variance": 0.1}
        observation = {"likelihood": 0.5, "signal": 0.8, "entropy": 0.0}
        result = kalman_belief_update(belief, observation, kalman_gain=1.0)
        assert math.isclose(result["estimate"], 0.8, rel_tol=1e-6)

    def test_variance_decreases_with_update(self) -> None:
        belief = {"prior": 0.5, "entropy": 0.0, "variance": 0.5}
        observation = {"likelihood": 0.5, "signal": 0.5, "entropy": 0.0}
        result = kalman_belief_update(belief, observation, kalman_gain=0.3)
        # Joseph form: (1-K)^2 * P; so variance should decrease
        assert result["variance"] < 0.5

    def test_audit_fields_present(self) -> None:
        belief = {"prior": 0.5, "entropy": 0.0, "variance": 0.0}
        observation = {"likelihood": 0.5, "signal": 0.5, "entropy": 0.0}
        result = kalman_belief_update(belief, observation)
        assert result["compute_backend"] == "python"
        assert result["compute_path"] == "primary"
        for field in ("estimate", "residual", "variance", "kalman_gain", "prior", "signal"):
            assert field in result, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# compute_signal_variance
# ---------------------------------------------------------------------------


class TestComputeSignalVariance:
    def test_constant_signal_zero_variance(self) -> None:
        result = compute_signal_variance([0.5, 0.5, 0.5, 0.5])
        assert math.isclose(result["variance"], 0.0, abs_tol=1e-10)
        assert math.isclose(result["std"], 0.0, abs_tol=1e-10)

    def test_wide_spread_low_confidence(self) -> None:
        # [0,1,0,1]: variance ≈ 0.333, confidence = 1/(1+0.333) ≈ 0.75
        # Wide spread still yields lower confidence than a tight spread.
        tight = compute_signal_variance([0.5, 0.51, 0.49, 0.50])
        wide = compute_signal_variance([0.0, 1.0, 0.0, 1.0])
        assert wide["confidence"] < tight["confidence"]

    def test_tight_spread_high_confidence(self) -> None:
        result = compute_signal_variance([0.5, 0.51, 0.49, 0.50])
        assert result["confidence"] > 0.9

    def test_empty_list_returns_defaults(self) -> None:
        result = compute_signal_variance([])
        assert result["n"] == 0
        assert result["confidence"] == 1.0  # no uncertainty = max confidence

    def test_single_value_returns_defaults(self) -> None:
        result = compute_signal_variance([0.8])
        assert result["n"] == 1
        assert result["mean"] == 0.8

    def test_audit_fields_present(self) -> None:
        result = compute_signal_variance([0.3, 0.5, 0.7])
        assert result["compute_backend"] == "python"
        assert result["compute_path"] == "primary"
        for field in ("mean", "variance", "std", "confidence", "n"):
            assert field in result


# ---------------------------------------------------------------------------
# compute_belief_snapshot
# ---------------------------------------------------------------------------


class TestComputeBeliefSnapshot:
    def _standard_belief(self) -> dict:
        return {"prior": 0.5, "entropy": 0.1, "variance": 0.0}

    def _standard_observation(self) -> dict:
        return {"likelihood": 0.7, "signal": 0.75, "entropy": 0.1}

    def test_all_prefixed_fields_present(self) -> None:
        result = compute_belief_snapshot(
            self._standard_belief(),
            self._standard_observation(),
            signal_window=[0.4, 0.5, 0.6],
        )
        expected_prefixes = (
            "belief_posterior",
            "belief_estimate",
            "belief_residual",
            "belief_variance",
            "signal_confidence",
            "signal_mean",
            "signal_variance",
        )
        for key in expected_prefixes:
            assert key in result, f"Missing key: {key}"

    def test_compute_backend_python(self) -> None:
        result = compute_belief_snapshot(
            self._standard_belief(),
            self._standard_observation(),
        )
        assert result.get("belief_compute_backend") == "python"
        assert result.get("belief_compute_path") == "primary"

    def test_empty_signal_window_still_returns(self) -> None:
        result = compute_belief_snapshot(
            self._standard_belief(),
            self._standard_observation(),
            signal_window=[],
        )
        assert isinstance(result, dict)
        assert "belief_posterior" in result

    def test_posterior_greater_than_prior_with_high_likelihood(self) -> None:
        result = compute_belief_snapshot(
            {"prior": 0.3, "entropy": 0.0, "variance": 0.0},
            {"likelihood": 0.95, "signal": 0.9, "entropy": 0.0},
        )
        assert result["belief_posterior"] > 0.3
