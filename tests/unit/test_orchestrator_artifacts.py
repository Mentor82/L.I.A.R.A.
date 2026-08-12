from services.orchestrator.defs.artifacts import extract_artifacts_from_tool_results


def test_failure_envelope_is_not_projected_as_artifact() -> None:
    result = extract_artifacts_from_tool_results(
        {
            "sys": {
                "kind": "tool_execution_failure",
                "status": "failed",
                "evidence": False,
                "error": "network blocked",
            }
        }
    )

    assert result is None


def test_successful_artifact_remains_projected() -> None:
    result = extract_artifacts_from_tool_results(
        {
            "plot_chart": {
                "kind": "image",
                "url": "/artifacts/chart.png",
            }
        }
    )

    assert result == [
        {
            "kind": "image",
            "url": "/artifacts/chart.png",
            "source_tool": "plot_chart",
            "metadata": {},
        }
    ]
