from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "liara_law_regression_runner.py"
    spec = importlib.util.spec_from_file_location("liara_law_regression_runner", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_get_nested_reads_dotted_paths() -> None:
    module = _load_module()
    payload = {"summary": {"total": 3}}
    assert module._get_nested(payload, "summary.total") == 3
    assert module._get_nested(payload, "summary.missing") is None


def test_evaluate_chat_case_law_conflict_uses_explainability_fields() -> None:
    module = _load_module()
    outcome = {
        "http_status": 200,
        "response": {
            "metadata": {
                "validation": {
                    "explainability": {
                        "triggered_laws": ["utility_negative"],
                        "decision_path": ["check_policy", "check_risk", "apply_soft_control"],
                    },
                    "math_signals": {
                        "conflict_resolution": {
                            "winning_law": "utility_negative",
                            "strategy": "priority_then_weight",
                        }
                    },
                }
            }
        },
    }
    expected = {
        "triggered_laws_contains_any": ["utility_negative", "truth_first"],
        "decision_path_contains": ["check_policy", "check_risk"],
    }

    verdict = module._evaluate_chat_case("LAW-CONFLICT-001", expected, outcome)
    assert verdict["status"] == "passed"


def test_evaluate_chat_case_audit_002_accepts_list_payload() -> None:
    module = _load_module()
    verdict = module._evaluate_chat_case(
        "AUDIT-002",
        {"must_return_list_or_empty": True, "must_not_fail_if_no_events": True},
        {"http_status": 200, "response": {"count": 2, "items": [{"id": 1}, {"id": 2}]}},
    )
    assert verdict["status"] == "passed"


def test_evaluate_chat_case_threshold_003_checks_reason_and_jump() -> None:
    module = _load_module()
    verdict = module._evaluate_chat_case(
        "THRESHOLD-003",
        {"reason_contains_any": ["insufficient_samples", "disabled"], "no_large_threshold_jump": True},
        {
            "http_status": 200,
            "response": {
                "metadata": {
                    "validation": {
                        "threshold_adaptation": {
                            "applied": False,
                            "reason": "disabled",
                            "previous": {"soft_risk_max": 5.0, "hard_risk_max": 8.0},
                            "recommended": {"soft_risk_max": 5.2, "hard_risk_max": 8.4},
                        }
                    }
                }
            },
        },
    )
    assert verdict["status"] == "passed"


def test_has_large_threshold_jump_detects_excessive_delta() -> None:
    module = _load_module()
    assert module._has_large_threshold_jump(
        {
            "previous": {"soft_risk_max": 5.0},
            "recommended": {"soft_risk_max": 6.5},
        }
    ) is True


def test_evaluate_chat_case_law_conflict_002_accepts_refusal_decision() -> None:
    module = _load_module()
    verdict = module._evaluate_chat_case(
        "LAW-CONFLICT-002",
        {
            "triggered_laws_contains_any": ["truth_first"],
            "decision": "clarify_or_refuse",
            "must_not_claim_unverified_fact": True,
        },
        {
            "http_status": 200,
            "response": {
                "response": "Ich kann diese Aussage ohne belastbare Evidenz nicht bestaetigen.",
                "metadata": {
                    "validation": {
                        "decision": "refuse",
                        "explainability": {"triggered_laws": ["truth_first"]},
                    }
                },
            },
        },
    )
    assert verdict["status"] == "passed"


def test_evaluate_chat_case_law_conflict_004_requires_qualification() -> None:
    module = _load_module()
    verdict = module._evaluate_chat_case(
        "LAW-CONFLICT-004",
        {
            "triggered_laws_contains_any": ["truth_first"],
            "response_must_qualify": True,
        },
        {
            "http_status": 200,
            "response": {
                "response": "Das haengt vom Workload ab, pauschal stimmt die Aussage nicht immer.",
                "metadata": {
                    "validation": {
                        "explainability": {"triggered_laws": ["truth_first"]},
                    }
                },
            },
        },
    )
    assert verdict["status"] == "passed"


def test_evaluate_chat_case_law_conflict_005_blocks_backend_assumption() -> None:
    module = _load_module()
    verdict = module._evaluate_chat_case(
        "LAW-CONFLICT-005",
        {
            "triggered_laws_contains_any": ["evidence_required"],
            "must_not_assume_backend_health": True,
        },
        {
            "http_status": 200,
            "response": {
                "response": "Ohne Health-Checks kann ich nicht annehmen, dass alle Backends gesund sind.",
                "metadata": {
                    "validation": {
                        "explainability": {"triggered_laws": ["evidence_required"]},
                    }
                },
            },
        },
    )
    assert verdict["status"] == "passed"


def test_evaluate_stream_case_checks_order_and_chunk() -> None:
    module = _load_module()
    verdict = module._evaluate_stream_case(
        {"events_in_order_contains": ["progress", "chunk", "final", "done"], "at_least_one_chunk": True},
        {"http_status": 200, "event_names": ["progress", "progress", "chunk", "final", "done"]},
    )
    assert verdict["status"] == "passed"


def test_evaluate_determinism_case_detects_stability() -> None:
    module = _load_module()
    rows = []
    for _ in range(3):
        rows.append(
            {
                "outcome": {
                    "response": {
                        "metadata": {
                            "validation": {
                                "explainability": {
                                    "risk_score": 0.175,
                                    "decision_path": ["check_policy", "check_risk"],
                                },
                                "math_signals": {
                                    "conflict_resolution": {
                                        "winning_law": "utility_negative"
                                    }
                                },
                            }
                        }
                    }
                }
            }
        )

    verdict = module._evaluate_determinism_case(
        "DETERMINISM-001",
        {
            "winning_law_stable": True,
            "decision_path_stable": True,
            "risk_score_delta_max": 0.25,
        },
        rows,
    )
    assert verdict["status"] == "passed"


def test_filter_seed_supported_only_reduces_cases() -> None:
    module = _load_module()
    seed = [
        {"id": "LAW-CONFLICT-001"},
        {"id": "CHAOS-001"},
    ]
    filtered = module._filter_seed(seed, case_ids=[], supported_only=True)
    assert [item["id"] for item in filtered] == ["LAW-CONFLICT-001"]
