"""Pure formatting tests for the Textual admin console."""

from services.tui.admin_console import assurance_style, dreaming_proposal_row


def test_assurance_style_distinguishes_gate_states() -> None:
    assert "green" in assurance_style("passed")
    assert "red" in assurance_style("failed")
    assert "attention" in assurance_style("attention")
    assert "pending*" in assurance_style("pending", required=True)


def test_dreaming_proposal_row_exposes_validator_and_artifact() -> None:
    row = dreaming_proposal_row(
        {
            "proposal_id": "proposal-1234567890",
            "session_id": "session-1234567890",
            "decision": "pending",
            "assurance": {
                "required": True,
                "verdict": "passed",
                "validator_job_id": "validator-job-1234567890",
                "findings_count": 2,
                "artifacts": [{"path": "artifacts/validator_jobs/job/report.json"}],
            },
            "quality_signals": {
                "available": True,
                "complexity": {"level": "moderate", "score": 0.41},
                "coverage": {"source_coverage_ratio": 1.0, "relation_coverage_ratio": 0.5},
            },
        }
    )

    assert row[0].startswith("proposal-") and len(row[0]) == 18
    assert row[2] == "pending"
    assert "passed" in row[3]
    assert row[4].startswith("validator-job-") and len(row[4]) == 18
    assert row[5] == "2"
    assert row[6] == "moderate"
    assert row[7] == "100%"
    assert row[8] == "50%"
    assert row[9].endswith("report.json")
