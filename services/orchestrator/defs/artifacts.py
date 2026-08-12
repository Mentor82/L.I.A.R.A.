from typing import Any, Dict, List


def extract_artifacts_from_tool_results(tool_results: Dict[str, Any]) -> List[Dict[str, Any]] | None:
    """Collect normalized artifact entries from tool outputs."""
    if not isinstance(tool_results, dict) or not tool_results:
        return None

    artifacts: List[Dict[str, Any]] = []
    for tool_name, output in tool_results.items():
        if not isinstance(output, dict):
            continue
        if (
            output.get("evidence") is False
            or str(output.get("status") or "").strip().lower() in {"failed", "error", "blocked", "denied"}
            or str(output.get("kind") or "").strip().lower() == "tool_execution_failure"
        ):
            continue

        if isinstance(output.get("artifacts"), list):
            for entry in output.get("artifacts") or []:
                if not isinstance(entry, dict) or not entry.get("kind"):
                    continue
                normalized = dict(entry)
                normalized.setdefault("source_tool", str(tool_name))
                normalized.setdefault("metadata", {})
                artifacts.append(normalized)
            continue

        if output.get("kind"):
            normalized = dict(output)
            normalized.setdefault("source_tool", str(tool_name))
            normalized.setdefault("metadata", {})
            artifacts.append(normalized)

    return artifacts or None
