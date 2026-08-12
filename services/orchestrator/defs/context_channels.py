import logging
from typing import Any, Callable, Dict, List


def merge_context_channels(channels: Dict[str, str]) -> str:
    ordered = [
        channels.get("fact_context", ""),
        channels.get("memory_context", ""),
        channels.get("relation_context", ""),
        channels.get("working_context", ""),
    ]
    return "\n".join(part for part in ordered if part.strip())


async def load_conversation_history(
    *,
    session_id: str,
    current_query: str,
    limit: int,
    query_history_fn: Callable[[Any], Any],
    memory_history_query_request_cls: Any,
) -> tuple[str, int]:
    """Return a compact role-tagged transcript from recent session turns."""
    logger = logging.getLogger(__name__)

    try:
        history = await query_history_fn(
            memory_history_query_request_cls(
                session_id=session_id,
                limit=limit,
                include_tool_messages=False,
            )
        )
        logger.debug("[HISTORY] Loaded %s items for session %s", len(history.items or []), session_id)
    except Exception as exc:
        logger.error("[HISTORY] Failed to load history: %s", exc)
        return "", 0

    lines: List[str] = []
    normalized_query = current_query.strip().lower()
    for item in history.items:
        content = (item.content or "").strip().replace("\n", " ")
        if not content:
            continue

        # The API already appends the active user query before orchestration.
        # Skip that one so the model sees prior context clearly.
        if item.role == "user" and content.lower() == normalized_query:
            logger.debug("[HISTORY] Skipping current query: %s...", content[:60])
            continue

        lines.append(f"{item.role}: {content[:280]}")
        logger.debug("[HISTORY] Added line from %s: %s...", item.role, content[:60])

    result = "\n".join(lines[-limit:])
    logger.debug("[HISTORY] Returning %s history lines (%s chars)", len(lines), len(result))
    return result, len(lines)
