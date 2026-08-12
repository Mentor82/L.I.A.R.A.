from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "apply_reasoning_thresholds_from_audit.py"
    spec = importlib.util.spec_from_file_location("apply_reasoning_thresholds_from_audit", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_env_suggestion_from_threshold_evaluation() -> None:
    module = _load_module()
    payload = {
        "threshold_evaluation": {
            "env_suggestion": {
                "REASONING_SOFT_RISK_MAX": 1.25,
                "REASONING_HARD_RISK_MAX": 1.75,
            }
        }
    }
    out = module._extract_env_suggestion(payload)
    assert out is not None
    assert out["REASONING_SOFT_RISK_MAX"] == 1.25
    assert out["REASONING_HARD_RISK_MAX"] == 1.75


def test_extract_env_suggestion_fallbacks_to_advisory_thresholds() -> None:
    module = _load_module()
    payload = {
        "advisory_thresholds": {
            "recommended": {
                "soft_risk_max": 2.1,
                "hard_risk_max": 2.8,
                "mode": "advisory_only",
            }
        }
    }
    out = module._extract_env_suggestion(payload)
    assert out is not None
    assert out["REASONING_SOFT_RISK_MAX"] == 2.1
    assert out["REASONING_HARD_RISK_MAX"] == 2.8


def test_patch_env_content_replaces_existing_and_appends_missing() -> None:
    module = _load_module()
    before = "DEBUG=true\nREASONING_SOFT_RISK_MAX=5.000000\n"
    updates = {
        "REASONING_SOFT_RISK_MAX": 1.234567,
        "REASONING_HARD_RISK_MAX": 2.345678,
    }
    after = module._patch_env_content(before, updates)

    assert "REASONING_SOFT_RISK_MAX=1.234567" in after
    assert "REASONING_HARD_RISK_MAX=2.345678" in after
    assert after.endswith("\n")
