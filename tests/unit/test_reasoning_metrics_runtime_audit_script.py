from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "reasoning_metrics_runtime_audit.py"
    spec = importlib.util.spec_from_file_location("reasoning_metrics_runtime_audit", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_advisory_thresholds_insufficient_data() -> None:
    module = _load_module()
    result = module._build_advisory_thresholds([0.1, 0.2], min_samples=5)
    assert result["status"] == "insufficient_data"
    assert result["recommended"] is None


def test_build_advisory_thresholds_returns_recommended_values() -> None:
    module = _load_module()
    values = [0.1, 0.2, 0.35, 0.4, 0.55, 0.65, 0.8, 0.95, 1.1, 1.3]
    result = module._build_advisory_thresholds(values, min_samples=5)

    assert result["status"] == "ok"
    assert result["recommended"] is not None
    assert result["recommended"]["mode"] == "advisory_only"
    assert result["recommended"]["hard_risk_max"] > result["recommended"]["soft_risk_max"]
    assert result["quantile"]["hard_risk_max"] > result["quantile"]["soft_risk_max"]
    assert result["mad"]["hard_risk_max"] > result["mad"]["soft_risk_max"]


def test_build_limit_impact_rates() -> None:
    module = _load_module()
    impact = module._build_limit_impact([0.1, 0.6, 1.1, 1.4], soft_risk_max=0.5, hard_risk_max=1.0)
    assert impact["sample_count"] == 4
    assert impact["soft_limit_count"] == 3
    assert impact["hard_block_count"] == 2
    assert impact["soft_limit_rate"] == 0.75
    assert impact["hard_block_rate"] == 0.5


def test_build_threshold_evaluation_includes_env_suggestion() -> None:
    module = _load_module()
    advisory = {
        "status": "ok",
        "recommended": {
            "soft_risk_max": 0.8,
            "hard_risk_max": 1.2,
            "mode": "advisory_only",
        },
    }
    evaluation = module._build_threshold_evaluation([0.2, 0.9, 1.3], advisory)

    assert evaluation["mode"] == "advisory_only"
    assert evaluation["current"]["sample_count"] == 3
    assert evaluation["recommended"] is not None
    assert evaluation["env_suggestion"]["REASONING_SOFT_RISK_MAX"] == 0.8
    assert evaluation["env_suggestion"]["REASONING_HARD_RISK_MAX"] == 1.2
