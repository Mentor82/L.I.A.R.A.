from typing import Any, Dict, List


def format_tool_context(tool_outputs: Dict[str, Any]) -> str:
    """Create compact deterministic text for tool outputs in run context."""
    parts: List[str] = []
    for tool_name, output in sorted(tool_outputs.items()):
        if isinstance(output, dict) and output.get("summary_text"):
            text = str(output.get("summary_text", "")).replace("\n", " ").strip()
        else:
            text = str(output).replace("\n", " ").strip()
        parts.append(f"[{tool_name}] {text[:600]}")
    return "\n".join(parts)


def build_working_context_summary(query: str, tool_outputs: Dict[str, Any], response: str) -> str:
    tool_summary = format_tool_context(tool_outputs) if tool_outputs else "(none)"
    answer = (response or "").strip().replace("\n", " ")
    return (
        f"query: {query.strip()}\n"
        f"tools: {tool_summary}\n"
        f"assistant: {answer[:700]}"
    )
