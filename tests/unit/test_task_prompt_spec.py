from __future__ import annotations

import pytest

from services.orchestrator.task_prompt_spec import parse_task_prompt


def test_parse_task_prompt_returns_none_for_regular_query():
    assert parse_task_prompt("Was ist Python?") is None


def test_parse_task_prompt_parses_valid_yaml_block():
    query = """#task
type: tool
goal: "Durchschnittliche Antwortzeit berechnen"
steps:
  - tool run_stats limit=10
  - validate
  - answer
  - rds
output:
  - result
  - rds
"""

    spec = parse_task_prompt(query)
    assert spec is not None
    assert spec.task_type == "tool"
    assert spec.goal == "Durchschnittliche Antwortzeit berechnen"
    assert spec.explicit_tools() == ["run_stats"]

    execution_query = spec.to_execution_query()
    assert "[TASK_PIPELINE]" in execution_query
    assert "[TASK_OUTPUT_REQUIREMENTS]" in execution_query
    assert "Include a numeric rds field" in execution_query


def test_parse_task_prompt_rejects_invalid_type():
    query = """#task
type: unknown
goal: "x"
steps:
  - think
output:
  - answer
"""

    with pytest.raises(ValueError, match="unsupported task type"):
        parse_task_prompt(query)


def test_parse_task_prompt_requires_non_empty_steps():
    query = """#task
type: reasoning
goal: "x"
steps: []
output:
  - answer
"""

    with pytest.raises(ValueError, match="steps must not be empty"):
        parse_task_prompt(query)