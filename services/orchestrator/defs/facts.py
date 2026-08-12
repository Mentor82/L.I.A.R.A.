from typing import Any

from services.contracts import MemoryFactQueryRequest


async def check_fact_shortcut(
    orchestrator: Any,
    *,
    query: str,
    session_id: str,
    user_id: str,
    fact_shortcut_specs: list[tuple[str, Any, str, str]],
) -> tuple[str, str, str, str, Any] | None:
    librarian = orchestrator.librarian.route(
        query=query,
        gap_action=None,
        force_context=False,
        conversation_history="",
        session_id=session_id,
        user_id=user_id,
    )
    if librarian.route != "FACT_LOOKUP" or not librarian.fact_key or not librarian.fact_namespaces:
        return None

    template_map = {key: (de_tmpl, en_tmpl) for key, _query_re, de_tmpl, en_tmpl in fact_shortcut_specs}
    templates = template_map.get(librarian.fact_key)
    if not templates:
        return None

    for namespace in librarian.fact_namespaces:
        try:
            response = await orchestrator.memory_service.query_facts(
                MemoryFactQueryRequest(namespace=namespace, key=librarian.fact_key, limit=1)
            )
        except Exception:
            continue
        if response.items:
            value = str(response.items[0].value).strip()
            if value:
                return (value, librarian.fact_key, templates[0], templates[1], librarian)
    return None
