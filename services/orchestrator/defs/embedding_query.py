import os
import re
from typing import Any, Callable, Dict, List


def infer_active_topic(*, route: str, fact_key: str | None) -> str:
    route_map = {
        "FACT_LOOKUP": "fact lookup",
        "SESSION_RECALL": "session recall",
        "SEMANTIC_MEMORY": "semantic memory",
        "RUN_CONTEXT": "run context",
        "RELATION_LOOKUP": "relation lookup",
    }
    route_topic = route_map.get((route or "").strip().upper(), "")
    if fact_key:
        return f"{route_topic} {fact_key}".strip()
    return route_topic


def summarize_history_for_embedding(
    conversation_history: str,
    *,
    compact_embedding_text: Callable[..., tuple[str, Dict[str, Any]]],
) -> str:
    text = (conversation_history or "").strip()
    if not text:
        return ""

    role_prefix_re = re.compile(r"^(system|assistant|user|tool)\s*:\s*", re.IGNORECASE)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = [role_prefix_re.sub("", line) for line in lines]
    tail = cleaned[-3:]
    merged = " ; ".join(item for item in tail if item)
    compact, _ = compact_embedding_text(merged, max_chars=260, max_tokens=48)
    return compact


def build_session_summary_embedding(
    *,
    conversation_history: str,
    current_user_input: str,
    active_topic: str,
    summarize_history_for_embedding_fn: Callable[[str], str],
    compact_embedding_text_fn: Callable[..., tuple[str, Dict[str, Any]]],
) -> str:
    """Build an explicit, tagged session summary string for embedding identity."""
    history_summary = summarize_history_for_embedding_fn(conversation_history)
    current_focus, _ = compact_embedding_text_fn(current_user_input, max_chars=180, max_tokens=36)
    topic = (active_topic or "general").strip() or "general"
    payload = (
        f"SESSION_SUMMARY topic={topic} "
        f"history={history_summary or 'none'} "
        f"current_focus={current_focus}"
    )
    compact, _ = compact_embedding_text_fn(payload, max_chars=320, max_tokens=64)
    return compact


def compact_embedding_text(
    text: str,
    *,
    max_chars: int | None = None,
    max_tokens: int | None = None,
) -> tuple[str, Dict[str, Any]]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())

    if max_chars is None:
        max_chars_raw = os.getenv("EMBEDDING_QUERY_MAX_CHARS", "800")
        try:
            max_chars = max(96, int(max_chars_raw))
        except ValueError:
            max_chars = 800

    if max_tokens is None:
        max_tokens_raw = os.getenv("EMBEDDING_QUERY_MAX_TOKENS", "128")
        try:
            max_tokens = max(12, int(max_tokens_raw))
        except ValueError:
            max_tokens = 128

    truncated = False
    compact = normalized
    if len(compact) > max_chars:
        compact = compact[:max_chars].rstrip()
        truncated = True

    terms = re.findall(r"\S+", compact)
    if len(terms) > max_tokens:
        compact = " ".join(terms[:max_tokens])
        truncated = True

    if not compact:
        compact = "query"

    return compact, {
        "token_length": len(re.findall(r"\S+", compact)),
        "truncated": bool(truncated),
    }


def rewrite_retrieval_query(query: str) -> tuple[str, Dict[str, Any]]:
    """Compress retrieval query before embedding to avoid sending full chat transcripts."""
    original = (query or "").strip()
    normalized = re.sub(r"\s+", " ", original)

    # Drop common chat-role prefixes that indicate transcript dumps.
    role_prefix_re = re.compile(r"^(system|assistant|user|tool)\s*:\s*", re.IGNORECASE)
    lines = [line.strip() for line in original.splitlines() if line.strip()]
    if lines:
        cleaned_lines = [role_prefix_re.sub("", line) for line in lines]
        normalized = re.sub(r"\s+", " ", cleaned_lines[-1]).strip() or normalized

    max_chars_raw = os.getenv("EMBEDDING_RETRIEVAL_QUERY_MAX_CHARS", "600")
    try:
        max_chars = max(64, int(max_chars_raw))
    except ValueError:
        max_chars = 600

    max_tokens_raw = os.getenv("EMBEDDING_RETRIEVAL_QUERY_MAX_TOKENS", "96")
    try:
        max_tokens = max(8, int(max_tokens_raw))
    except ValueError:
        max_tokens = 96

    truncated = False
    rewritten = normalized

    if len(rewritten) > max_chars:
        rewritten = rewritten[:max_chars].rstrip()
        truncated = True

    terms = re.findall(r"\S+", rewritten)
    if len(terms) > max_tokens:
        rewritten = " ".join(terms[:max_tokens])
        truncated = True

    # Last safety net: always send a compact non-empty query.
    if not rewritten:
        rewritten = (original[:max_chars] or "query").strip()
        truncated = True

    token_length = len(re.findall(r"\S+", rewritten))
    metrics = {
        "original_query_length": len(original),
        "rewrite_length": len(rewritten),
        "token_length": token_length,
        "truncation_flag": bool(truncated),
    }
    return rewritten, metrics


def build_embedding_query(
    *,
    current_user_input: str,
    conversation_history: str,
    route: str,
    fact_key: str | None,
    force_context: bool,
    gap_action: str | None,
    rewrite_retrieval_query: Callable[[str], tuple[str, Dict[str, Any]]],
    infer_active_topic_fn: Callable[..., str],
    summarize_history_for_embedding_fn: Callable[[str], str],
    compact_embedding_text_fn: Callable[..., tuple[str, Dict[str, Any]]],
) -> tuple[str, Dict[str, Any]]:
    """Build a context-enriched embedding query instead of embedding raw input only."""
    compact_input, _ = rewrite_retrieval_query(current_user_input)
    active_topic = infer_active_topic_fn(route=route, fact_key=fact_key)
    session_summary = build_session_summary_embedding(
        conversation_history=conversation_history,
        current_user_input=current_user_input,
        active_topic=active_topic,
        summarize_history_for_embedding_fn=summarize_history_for_embedding_fn,
        compact_embedding_text_fn=compact_embedding_text_fn,
    )
    current_goal = compact_input

    constraints: List[str] = []
    if force_context:
        constraints.append("force_context")
    if gap_action and gap_action.upper() != "NONE":
        constraints.append(f"gap_action:{gap_action.lower()}")
    if fact_key:
        constraints.append(f"fact_key:{fact_key}")

    parts = [
        compact_input,
        active_topic,
        session_summary,
        current_goal,
        " ".join(constraints),
    ]
    raw_embedding_query = " ".join(part.strip() for part in parts if part and part.strip())
    embedding_query, compact_meta = compact_embedding_text_fn(raw_embedding_query)

    metrics = {
        "input_chars": len((current_user_input or "").strip()),
        "embedding_chars": len(embedding_query),
        "token_length": compact_meta["token_length"],
        "truncated": compact_meta["truncated"],
        "topic_used": bool(active_topic),
        "history_used": bool(session_summary),
        "constraints": constraints,
    }
    return embedding_query, metrics
