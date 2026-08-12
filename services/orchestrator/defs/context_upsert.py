from typing import Any, Callable, Dict
import re
import time as _time_module


def build_context_upsert_metadata(
    *,
    content: str,
    artifact_type: str,
    validation_status: str,
    scope: str,
    metadata: Dict[str, Any] | None = None,
    detect_language_fn: Callable[[str], str],
) -> Dict[str, Any]:
    payload = dict(metadata or {})
    detected_language = detect_language_fn(content or "")
    payload.setdefault("source", "reasoning_loop")
    payload.setdefault("artifact_type", artifact_type)
    payload.setdefault("validation_status", validation_status)
    payload.setdefault("scope", scope)
    payload.setdefault("created_by", "liara")
    # Resolve language: prefer langdetect over heuristic if available
    try:
        from langdetect import detect as _langdetect
        _text = (content or "").strip()
        _lang = _langdetect(_text[:200]) if len(_text) >= 10 else (
            "de" if detected_language == "German" else "en"
        )
    except Exception:
        _lang = "de" if detected_language == "German" else "en"
    payload.setdefault("language", _lang)
    payload.setdefault("reasoning_step", 1)
    payload.setdefault("upserted_at", _time_module.time())
    return payload


def is_safe_for_context_upsert(content: str) -> bool:
    """Block obvious secret material from being persisted in context stores."""
    patterns = [
        r"(?i)api[_-]?key\s*[:=]",
        r"(?i)token\s*[:=]",
        r"(?i)authorization\s*:\s*bearer",
        r"(?i)password\s*[:=]",
        r"(?i)secret\s*[:=]",
        r"AKIA[0-9A-Z]{16}",
    ]
    return not any(re.search(pattern, content) for pattern in patterns)


async def touch_working_context_activity(
    *,
    session_id: str,
    run_id: str,
    ttl_seconds: int,
    set_fn: Callable[..., Any],
    session_tier: Any,
) -> tuple[int, float]:
    """Refresh active workflow heartbeat and return sliding TTL metadata."""
    import logging

    now = _time_module.time()
    expires_at = now + float(ttl_seconds)
    heartbeat_key = f"workflow_active:{session_id}"
    heartbeat_payload = {
        "session_id": session_id,
        "run_id": run_id,
        "last_seen_at": now,
        "expires_at": expires_at,
        "scope": "working_context",
    }

    try:
        await set_fn(
            session_tier,
            heartbeat_key,
            heartbeat_payload,
            ttl_seconds=ttl_seconds,
        )
    except Exception as exc:
        logging.debug(f"working context heartbeat touch failed ({heartbeat_key}): {exc}")

    return ttl_seconds, expires_at


async def upsert_temp_context_note(
    *,
    session_id: str,
    run_id: str,
    note_kind: str,
    content: str,
    metadata: Dict[str, Any] | None,
    get_fn: Callable[..., Any],
    set_fn: Callable[..., Any],
    session_tier: Any,
    temp_context_ttl_seconds: int,
    build_context_upsert_metadata_fn: Callable[..., Dict[str, Any]],
) -> None:
    """Best-effort TEMP write (session tier, e.g. Redis)."""
    import logging

    compact = (content or "").strip().replace("\n", " ")
    if not compact:
        return

    try:
        key = f"context_temp:{session_id}:{run_id}"
        existing = await get_fn(session_tier, key, default=[])
        notes = list(existing or [])
        upsert_metadata = build_context_upsert_metadata_fn(
            content=compact,
            artifact_type=note_kind,
            validation_status=str((metadata or {}).get("validation_status") or "unvalidated"),
            scope="session",
            metadata=metadata,
        )
        notes.append(
            {
                "kind": note_kind,
                "content": compact[:1200],
                "metadata": upsert_metadata,
            }
        )
        await set_fn(session_tier, key, notes[-20:], ttl_seconds=temp_context_ttl_seconds)
    except Exception as exc:
        logging.debug(f"temp context note failed ({note_kind}): {exc}")


async def upsert_working_context_doc(
    *,
    session_id: str,
    run_id: str,
    document_id: str,
    content: str,
    turn_index: int,
    metadata: Dict[str, Any] | None,
    is_safe_for_context_upsert_fn: Callable[[str], bool],
    touch_working_context_activity_fn: Callable[..., Any],
    build_context_upsert_metadata_fn: Callable[..., Dict[str, Any]],
    context_upsert_fn: Callable[..., Any],
    context_upsert_request_cls: Any,
    context_scope_cls: Any,
) -> None:
    """Best-effort WORKING_CONTEXT write (Chroma) with safety gate."""
    import logging

    compact = (content or "").strip().replace("\n", " ")
    if not compact:
        return
    if not is_safe_for_context_upsert_fn(compact):
        logging.debug(f"working context blocked by safety gate ({document_id})")
        return

    try:
        ttl_seconds, expires_at = await touch_working_context_activity_fn(
            session_id=session_id,
            run_id=run_id,
        )
        upsert_metadata = build_context_upsert_metadata_fn(
            content=compact,
            artifact_type=str((metadata or {}).get("artifact_type") or "working_context"),
            validation_status=str((metadata or {}).get("validation_status") or "validated"),
            scope="working_context",
            metadata=metadata,
        )
        await context_upsert_fn(
            context_upsert_request_cls(
                document_id=document_id,
                content=compact[:1200],
                scope=context_scope_cls(
                    session_id=session_id,
                    run_id=run_id,
                    turn_index=turn_index,
                    time_decay=0.9,
                ),
                memory_tier="working",
                ttl_seconds=ttl_seconds,
                expires_at=expires_at,
                promotion_state="none",
                metadata=upsert_metadata,
            )
        )
    except Exception as exc:
        logging.debug(f"working context upsert failed ({document_id}): {exc}")
