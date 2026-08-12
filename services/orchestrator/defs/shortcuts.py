import re
from typing import Any

from services.tools.registry import get_tool_registry


def check_intro_shortcut(
    query: str,
    *,
    intro_facts: list[tuple[Any, str, str, str]],
) -> tuple[str, str, str, str] | None:
    """Detect a pure self-introduction statement and return normalized tuple."""
    if not query:
        return None
    if "?" in query or query.count(".") > 1 or query.count("!") > 1:
        return None

    for fact_re, de_tmpl, en_tmpl, stop_reason in intro_facts:
        match = fact_re.match(query.strip())
        if match:
            value = match.group(1).strip(" .,!?:;\"'")
            if value:
                return (value, stop_reason, de_tmpl, en_tmpl)
    return None


def check_recall_shortcuts(
    query: str,
    conversation_history: str,
    *,
    recall_shortcuts: list[tuple[Any, Any, str, str, str]],
) -> tuple[str, str, str, str] | None:
    """Resolve known recall patterns from existing conversation history."""
    if not query or not conversation_history:
        return None

    for query_re, fact_re, de_tmpl, en_tmpl, stop_reason in recall_shortcuts:
        if not query_re.search(query):
            continue
        matches = list(fact_re.finditer(conversation_history))
        if matches:
            value = matches[-1].group(1).strip(" .,!?:;\"'")
            if value:
                return (value, stop_reason, de_tmpl, en_tmpl)
    return None


def check_tool_inventory_shortcut(
    query: str,
    *,
    tools_capability_query_re: Any,
) -> tuple[str, str] | None:
    """Return a deterministic tool inventory answer for capability questions."""
    if not query or not tools_capability_query_re.search(query):
        return None

    is_german = bool(re.search(r"\b(welche|was|kannst|tools?|nutzen|verwenden)\b", query, re.IGNORECASE))

    try:
        registry = get_tool_registry()
        tool_names = sorted(registry.list_tools())
        tools_text = ", ".join(tool_names) if tool_names else "(none)"
    except Exception:
        tool_names = []
        tools_text = "(none)"

    if is_german:
        if tool_names:
            response = (
                "Ich kann aktuell diese LIARA-Tools nutzen: "
                f"{tools_text}. "
                "Wenn du willst, kann ich eines davon direkt ausführen."
            )
        else:
            response = (
                "Aktuell ist keine Tool-Liste verfügbar. "
                "Bitte prüfe den Tool-Registry-Status (/tools)."
            )
    else:
        if tool_names:
            response = (
                "I can currently use these LIARA tools: "
                f"{tools_text}. "
                "If you want, I can execute one of them directly."
            )
        else:
            response = (
                "No tool inventory is currently available. "
                "Please check the tool registry endpoint (/tools)."
            )

    return response, "tool_inventory_shortcut"
