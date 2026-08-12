"""Unit tests for the validator output parser and finding normalization."""

from __future__ import annotations

import json
import pytest

from services.contracts.validator_jobs import ValidatorFinding
from services.validator.parser import parse_validator_findings


def test_parse_validator_findings_empty_inputs():
    assert parse_validator_findings(None) == []
    assert parse_validator_findings("") == []
    assert parse_validator_findings([]) == []
    assert parse_validator_findings({}) == []


def test_parse_validator_findings_from_finding_objects():
    finding = ValidatorFinding(
        severity="error",
        message="Type mismatch",
        file_path="src/main.py",
        line=42,
        rule="TS2322",
    )
    result = parse_validator_findings([finding])
    assert len(result) == 1
    assert result[0].severity == "error"
    assert result[0].message == "Type mismatch"
    assert result[0].file_path == "src/main.py"
    assert result[0].line == 42
    assert result[0].rule == "TS2322"


def test_parse_validator_findings_from_dict_list():
    raw_dicts = [
        {
            "severity": "warning",
            "message": "Unused import 'os'",
            "file": "services/api/app.py",
            "line": 15,
            "code": "F401",
        },
        {
            "level": "error",
            "msg": "Syntax error near token",
            "path": "workers/worker.py",
            "lineno": 100,
            "rule_id": "E999",
        },
    ]
    result = parse_validator_findings(raw_dicts)
    assert len(result) == 2

    assert result[0].severity == "warning"
    assert result[0].message == "Unused import 'os'"
    assert result[0].file_path == "services/api/app.py"
    assert result[0].line == 15
    assert result[0].rule == "F401"

    assert result[1].severity == "error"
    assert result[1].message == "Syntax error near token"
    assert result[1].file_path == "workers/worker.py"
    assert result[1].line == 100
    assert result[1].rule == "E999"


def test_parse_validator_findings_from_json_string():
    json_payload = json.dumps(
        [
            {
                "severity": "error",
                "message": "Undefined variable 'x'",
                "file_path": "script.py",
                "line": 5,
                "rule": "F821",
            }
        ]
    )
    result = parse_validator_findings(json_payload)
    assert len(result) == 1
    assert result[0].severity == "error"
    assert result[0].message == "Undefined variable 'x'"
    assert result[0].file_path == "script.py"
    assert result[0].line == 5
    assert result[0].rule == "F821"


def test_parse_validator_findings_from_linter_stdout():
    stdout_lines = (
        "services/orchestrator/orchestrator.py:123:45: E501: line too long (120 > 88)\n"
        "tests/unit/test_api.py:10: error: Item has no attribute 'get'\n"
        "invalid random text line without file patterns"
    )
    result = parse_validator_findings(stdout_lines)
    assert len(result) == 2

    assert result[0].file_path == "services/orchestrator/orchestrator.py"
    assert result[0].line == 123
    assert result[0].rule == "E501"
    assert "line too long" in result[0].message

    assert result[1].file_path == "tests/unit/test_api.py"
    assert result[1].line == 10
    assert result[1].severity == "error"
    assert "Item has no attribute" in result[1].message
