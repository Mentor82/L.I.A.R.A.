"""Parser utilities for extracting structured ValidatorFinding objects from raw tool outputs."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from services.contracts.validator_jobs import ValidatorFinding

# Regex patterns for common linter and compiler outputs:
# e.g., "services/orchestrator/orchestrator.py:123:45: E501 line too long"
# e.g., "main.py:12: error: Item has no attribute"
# e.g., "src/app.py:42: F401 'os' imported but unused"
LINTER_LINE_PATTERN = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:(?P<sev>error|warning|info):)?\s*(?:(?P<rule>[A-Z]\d{3,4}|[A-Z0-9_-]+):)?\s*(?P<msg>.+)$",
    re.IGNORECASE,
)


def parse_validator_findings(raw_output: Any) -> List[ValidatorFinding]:
    """Parse raw validator execution outputs, linter JSONs, or log strings into normalized ValidatorFinding instances."""
    if not raw_output:
        return []

    # 1. If already a list of ValidatorFinding instances or dicts
    if isinstance(raw_output, list):
        findings: List[ValidatorFinding] = []
        for item in raw_output:
            if isinstance(item, ValidatorFinding):
                findings.append(item)
            elif isinstance(item, dict):
                findings.append(_parse_dict_finding(item))
            elif isinstance(item, str):
                parsed = _parse_string_line(item)
                if parsed:
                    findings.append(parsed)
        return findings

    # 2. If single dict
    if isinstance(raw_output, dict):
        # Check if dict contains a 'findings' or 'issues' key
        if "findings" in raw_output and isinstance(raw_output["findings"], list):
            return parse_validator_findings(raw_output["findings"])
        if "issues" in raw_output and isinstance(raw_output["issues"], list):
            return parse_validator_findings(raw_output["issues"])
        return [_parse_dict_finding(raw_output)]

    # 3. If string (JSON or raw stdout log lines)
    if isinstance(raw_output, str):
        cleaned = raw_output.strip()
        if not cleaned:
            return []

        # Try parsing JSON first
        if (cleaned.startswith("[") and cleaned.endswith("]")) or (
            cleaned.startswith("{") and cleaned.endswith("}")
        ):
            try:
                data = json.loads(cleaned)
                return parse_validator_findings(data)
            except Exception:
                pass

        # Otherwise parse line by line
        lines = cleaned.splitlines()
        findings = []
        for line in lines:
            parsed_line = _parse_string_line(line)
            if parsed_line:
                findings.append(parsed_line)

        # Fallback if no specific linter pattern matched
        if not findings and lines:
            findings.append(
                ValidatorFinding(
                    severity="warning",
                    message=lines[0][:256],
                )
            )
        return findings

    return []


def _parse_dict_finding(item: dict) -> ValidatorFinding:
    """Extract fields from a dictionary representation of a finding."""
    # Standard fields
    severity = item.get("severity") or item.get("level") or "info"
    if severity not in {"info", "warning", "error"}:
        severity = "warning" if "warn" in str(severity).lower() else ("error" if "err" in str(severity).lower() else "info")

    message = str(item.get("message") or item.get("msg") or item.get("detail") or "Validation issue detected")
    file_path = item.get("file_path") or item.get("file") or item.get("filename") or item.get("path")
    line = item.get("line") or item.get("line_number") or item.get("lineno")
    if line is not None:
        try:
            line = int(line)
        except (ValueError, TypeError):
            line = None

    rule = item.get("rule") or item.get("code") or item.get("rule_id") or item.get("check")
    patch_hint = item.get("patch_hint") or item.get("fix") or item.get("hint")
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

    return ValidatorFinding(
        severity=severity,
        message=message,
        file_path=str(file_path) if file_path else None,
        line=line,
        rule=str(rule) if rule else None,
        patch_hint=str(patch_hint) if patch_hint else None,
        metadata=metadata,
    )


def _parse_string_line(line: str) -> Optional[ValidatorFinding]:
    """Match a single text line against common linter formats."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("=="):
        return None

    match = LINTER_LINE_PATTERN.match(stripped)
    if match:
        groups = match.groupdict()
        file_p = groups.get("file")
        line_num = groups.get("line")
        sev_str = (groups.get("sev") or "warning").lower()
        rule_str = groups.get("rule")
        msg_str = groups.get("msg") or stripped

        severity = "error" if "err" in sev_str else ("warning" if "warn" in sev_str else "info")
        line_val = int(line_num) if line_num and line_num.isdigit() else None

        return ValidatorFinding(
            severity=severity,
            message=msg_str,
            file_path=file_p,
            line=line_val,
            rule=rule_str,
        )

    return None
