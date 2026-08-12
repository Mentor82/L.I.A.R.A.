from services.orchestrator.defs.embedding_query import (
    build_embedding_query,
    compact_embedding_text,
    infer_active_topic,
    rewrite_retrieval_query,
    summarize_history_for_embedding,
)


def _call_build_embedding_query(
    *,
    current_user_input: str,
    conversation_history: str,
    route: str,
    fact_key: str | None = None,
    force_context: bool = False,
    gap_action: str | None = None,
):
    return build_embedding_query(
        current_user_input=current_user_input,
        conversation_history=conversation_history,
        route=route,
        fact_key=fact_key,
        force_context=force_context,
        gap_action=gap_action,
        rewrite_retrieval_query=rewrite_retrieval_query,
        infer_active_topic_fn=infer_active_topic,
        summarize_history_for_embedding_fn=lambda history: summarize_history_for_embedding(
            history, compact_embedding_text=compact_embedding_text
        ),
        compact_embedding_text_fn=compact_embedding_text,
    )


def test_build_embedding_query_enriches_short_input_with_context_hints():
    query, metrics = _call_build_embedding_query(
        current_user_input="mach das kuerzer",
        conversation_history=(
            "USER: Wir arbeiten am Liara User Override System.\n"
            "ASSISTANT: Ziel ist ein kompakter Antwortmodus ohne Erklaerung."
        ),
        route="SEMANTIC_MEMORY",
        fact_key=None,
        force_context=False,
        gap_action=None,
    )

    lowered = query.lower()
    assert "mach das kuerzer" in lowered
    assert "semantic memory" in lowered
    assert "liara user override system" in lowered
    assert metrics["topic_used"] is True
    assert metrics["history_used"] is True


def test_build_embedding_query_includes_runtime_constraints():
    query, metrics = _call_build_embedding_query(
        current_user_input="zeige mir den letzten stand",
        conversation_history="",
        route="FACT_LOOKUP",
        fact_key="profile.name",
        force_context=True,
        gap_action="RETRY_WITH_BROADER_CONTEXT",
    )

    lowered = query.lower()
    assert "force_context" in lowered
    assert "gap_action:retry_with_broader_context" in lowered
    assert "fact_key:profile.name" in lowered
    assert "fact lookup" in lowered
    assert "profile.name" in lowered
    assert "force_context" in metrics["constraints"]

