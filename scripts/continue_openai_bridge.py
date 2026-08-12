"""OpenAI-compatible bridge for Continue -> LIARA /chat.

Run:
    c:/ai/LIARA/.venv/Scripts/python.exe -m uvicorn scripts.continue_openai_bridge:app --host 127.0.0.1 --port 8011

Environment:
  LIARA_API_BASE_URL=http://127.0.0.1:8010
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


LIARA_API_BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
LIARA_TIMEOUT_SECONDS = float(os.getenv("LIARA_TIMEOUT_SECONDS", "300"))
SESSION_SALT = os.getenv("CONTINUE_SESSION_SALT", "liara-continue")
DEFAULT_USER_ID = os.getenv("CONTINUE_DEFAULT_USER_ID", "continue-user")
CONTINUE_BRIDGE_MAX_QUERY_CHARS = int(os.getenv("CONTINUE_BRIDGE_MAX_QUERY_CHARS", "12000"))
CONTINUE_BRIDGE_MAX_IMAGE_BYTES = int(os.getenv("CONTINUE_BRIDGE_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
CONTINUE_BRIDGE_LOG_INCLUDE_QUERY_TEXT = (
    os.getenv("CONTINUE_BRIDGE_LOG_INCLUDE_QUERY_TEXT", "1").strip().lower() not in {"0", "false", "no", "off"}
)
CONTINUE_BRIDGE_INCLUDE_SYSTEM_ROLE = (
    os.getenv("CONTINUE_BRIDGE_INCLUDE_SYSTEM_ROLE", "0").strip().lower() in {"1", "true", "yes", "on"}
)
CONTINUE_BRIDGE_INCLUDE_HISTORY = (
    os.getenv("CONTINUE_BRIDGE_INCLUDE_HISTORY", "1").strip().lower() in {"1", "true", "yes", "on"}
)

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "services"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_CONTINUE_BRIDGE_LOG_PATH = _LOG_DIR / "continue_bridge.jsonl"

app = FastAPI(title="liara-continue-openai-bridge")


def _bridge_model_ids() -> list[str]:
    raw = os.getenv("CONTINUE_BRIDGE_MODEL_IDS", "liara-agent,liara-chat,local")
    seen: set[str] = set()
    ids: list[str] = []
    for item in raw.split(","):
        model_id = item.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        ids.append(model_id)
    return ids or ["liara-agent"]


def _truncate_text(value: str, limit: int = 600) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + " ...[truncated]"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attachment_log_meta(attachment: dict[str, Any]) -> dict[str, Any]:
    text_content = str(attachment.get("text_content") or "")
    metadata = attachment.get("metadata") if isinstance(attachment.get("metadata"), dict) else {}
    return {
        "name": attachment.get("name"),
        "media_type": attachment.get("media_type"),
        "size_bytes": attachment.get("size_bytes"),
        "content_url": attachment.get("content_url"),
        "text_length": len(text_content),
        "text_sha256": _sha256_text(text_content) if text_content else None,
        "binary_payload_present": bool(attachment.get("content_base64")),
        "binary_sha256": metadata.get("sha256"),
    }


def _emit_bridge_audit(*, event: str, payload: dict[str, Any]) -> None:
    entry = {
        "timestamp": time.time(),
        "component": "continue_openai_bridge",
        "event": event,
        **payload,
    }
    try:
        with _CONTINUE_BRIDGE_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Bridge logging must never break request serving.
        return


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "input_text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "output_text":
                    parts.append(str(item.get("text", "")))
                elif item_type in {None, ""} and "text" in item:
                    parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _is_placeholder_value(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    return normalized in {"", "...", "n/a", "na", "none", "null", "unknown", "tbd", "todo"}


def _strip_placeholder_user_context_blocks(text: str) -> str:
    """Remove Continue template USER_CONTEXT blocks when all key values are placeholders."""
    if not text:
        return text

    lines = text.splitlines()
    output: list[str] = []
    i = 0

    while i < len(lines):
        if lines[i].strip().upper() != "USER_CONTEXT:":
            output.append(lines[i])
            i += 1
            continue

        j = i + 1
        block_lines: list[str] = []
        while j < len(lines):
            candidate = lines[j].strip()
            if not candidate:
                break
            if ":" not in candidate:
                break
            block_lines.append(candidate)
            j += 1

        key_values: dict[str, str] = {}
        for candidate in block_lines:
            key, value = candidate.split(":", 1)
            key_values[key.strip().lower()] = value.strip()

        has_user_context_shape = bool(key_values) and {"source", "path", "language", "content"}.issubset(
            set(key_values.keys())
        )
        is_placeholder_block = has_user_context_shape and all(
            _is_placeholder_value(key_values.get(name, "")) for name in ("path", "language", "content")
        )

        if is_placeholder_block:
            i = j
            while i < len(lines) and not lines[i].strip():
                i += 1
            continue

        output.append(lines[i])
        i += 1

    cleaned = "\n".join(output)
    compacted = "\n".join(line.rstrip() for line in cleaned.splitlines())
    while "\n\n\n" in compacted:
        compacted = compacted.replace("\n\n\n", "\n\n")
    return compacted.strip()


def _is_textual_media_type(media_type: str | None) -> bool:
    if not media_type:
        return False
    lowered = media_type.lower()
    return lowered.startswith("text/") or lowered in {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-sh",
    }


def _decode_data_uri_text(value: str) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        return None, None

    header, payload = value[5:].split(",", 1)
    media_type = header.split(";")[0] or None
    is_base64 = header.endswith(";base64") or ";base64;" in header

    try:
        raw = base64.b64decode(payload) if is_base64 else unquote_to_bytes(payload)
    except Exception:
        return None, media_type

    if not _is_textual_media_type(media_type):
        return None, media_type

    return raw.decode("utf-8", errors="replace"), media_type


def _decode_data_uri_payload(value: str) -> tuple[bytes | None, str | None]:
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        return None, None
    header, payload = value[5:].split(",", 1)
    media_type = header.split(";")[0] or None
    is_base64 = header.endswith(";base64") or ";base64;" in header
    try:
        raw = base64.b64decode(payload, validate=True) if is_base64 else unquote_to_bytes(payload)
    except Exception:
        return None, media_type
    return raw, media_type


def _attachment_from_content_item(item: dict[str, Any]) -> dict[str, Any] | None:
    item_type = str(item.get("type") or "").strip().lower()
    if item_type not in {"input_file", "file", "image_url", "input_image"}:
        return None

    attachment: dict[str, Any] = {
        "name": item.get("filename") or item.get("name"),
        "media_type": item.get("media_type") or item.get("mime_type"),
        "text_content": None,
        "content_base64": None,
        "content_url": None,
        "source": "continue-openai-bridge",
        "metadata": {},
    }

    if item.get("file_id"):
        attachment["metadata"]["file_id"] = item.get("file_id")

    if isinstance(item.get("size_bytes"), int):
        attachment["size_bytes"] = item.get("size_bytes")

    if item_type in {"image_url", "input_image"}:
        image_url = item.get("image_url") or {}
        url = image_url.get("url") if isinstance(image_url, dict) else image_url
        if isinstance(url, str) and url.startswith("data:"):
            raw, media_type = _decode_data_uri_payload(url)
            if media_type and not attachment.get("media_type"):
                attachment["media_type"] = media_type
            if raw is None:
                attachment["metadata"]["decode_error"] = True
            elif len(raw) > CONTINUE_BRIDGE_MAX_IMAGE_BYTES:
                attachment["metadata"]["decode_error"] = "image_too_large"
            else:
                attachment["content_base64"] = base64.b64encode(raw).decode("ascii")
                attachment["size_bytes"] = len(raw)
                attachment["metadata"]["content_url_kind"] = "data_uri"
                attachment["metadata"]["sha256"] = hashlib.sha256(raw).hexdigest()
        elif isinstance(url, str) and url:
            attachment["content_url"] = url
        return attachment

    inline_text = item.get("text")
    if isinstance(inline_text, str) and inline_text.strip():
        attachment["text_content"] = inline_text
        return attachment

    file_data = item.get("file_data")
    if isinstance(file_data, str):
        decoded_text, media_type = _decode_data_uri_text(file_data)
        if media_type and not attachment.get("media_type"):
            attachment["media_type"] = media_type
        if decoded_text:
            attachment["text_content"] = decoded_text
        elif _is_textual_media_type(attachment.get("media_type")) and not file_data.startswith("data:"):
            attachment["text_content"] = file_data
        else:
            attachment["metadata"]["file_data_present"] = True

    return attachment


def _extract_content_payload(content: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(content, str):
        return content, []

    attachments: list[dict[str, Any]] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                attachment = _attachment_from_content_item(item)
                if attachment is not None:
                    attachments.append(attachment)

    return _extract_text(content), attachments


def _attachment_prompt_block(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""

    parts: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        details: list[str] = []
        if attachment.get("name"):
            details.append(f"name={attachment['name']}")
        if attachment.get("media_type"):
            details.append(f"media_type={attachment['media_type']}")
        if attachment.get("size_bytes") is not None:
            details.append(f"size_bytes={attachment['size_bytes']}")

        header = f"[Attachment {index}"
        if details:
            header += f": {', '.join(details)}"
        header += "]"

        text_content = str(attachment.get("text_content") or "").strip()
        if text_content:
            parts.append(f"{header}\n{text_content}")
        elif attachment.get("content_url"):
            parts.append(f"{header}\nBinary or remote attachment provided.")
        else:
            parts.append(f"{header}\nAttachment metadata provided without inline text content.")

    return "\n\nBereitgestellte Dateien/Anhänge:\n" + "\n\n".join(parts)


# Roles that Continue uses for internal meta-requests (title gen, summarize, etc.).
# They are NOT directly user questions but internal Continue infrastructure.
_META_REQUEST_PATTERNS = (
    "give a title",
    "give this conversation a title",
    "name this conversation",
    "name the previous",
    "generate a short title",
    "in 3-5 words",
    "in five words",
    "in 5 words",
    "summarize the following in",
    "summarize this conversation in",
)


def _is_meta_request(messages: list[dict[str, Any]]) -> bool:
    """Return True if Continue is requesting a title/name/short-summary (not a real chat turn)."""
    user_msgs = [m for m in messages if str(m.get("role", "")).strip().lower() == "user"]
    if len(user_msgs) != 1:
        return False
    text = _extract_text(user_msgs[0].get("content", "")).strip().lower()
    return any(pattern in text for pattern in _META_REQUEST_PATTERNS)


def _messages_to_query_payload(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not messages:
        return "", []

    latest_user = ""
    transcript_lines: list[str] = []
    system_lines: list[str] = []
    all_attachments: list[dict[str, Any]] = []

    # Roles that belong to tool-call flows — flatten into the transcript but never treat as the
    # primary user query so they don't replace the actual user message.
    _SKIP_AS_LATEST = {"tool", "function", "ipython"}

    for msg in messages:
        role = str(msg.get("role", "user")).strip().lower() or "user"
        text, attachments = _extract_content_payload(msg.get("content", ""))
        text = _strip_placeholder_user_context_blocks(text)
        all_attachments.extend(attachments)
        # Keep attachments structured until LIARA's API trust boundary. The
        # API owns the one canonical prompt projection.
        combined = text.strip()
        if not combined and attachments:
            combined = "Bitte analysiere den bereitgestellten Anhang."
        if not combined:
            continue
        if role == "system":
            if CONTINUE_BRIDGE_INCLUDE_SYSTEM_ROLE:
                system_lines.append(combined)
            continue
        transcript_lines.append(f"{role.upper()}: {combined}")
        if role == "user":
            latest_user = combined
        # tool/function results are appended to the transcript but do NOT override latest_user.

    system_block = ""
    if CONTINUE_BRIDGE_INCLUDE_SYSTEM_ROLE and system_lines:
        system_block = "Systemanweisungen (aus Client):\n" + "\n\n".join(system_lines)

    if latest_user and CONTINUE_BRIDGE_INCLUDE_HISTORY and len(transcript_lines) > 1:
        history = "\n".join(transcript_lines[:-1])
        body = (
            "Kontext aus bisherigem Chatverlauf:\n"
            f"{history}\n\n"
            "Aktuelle Nutzeranfrage:\n"
            f"{latest_user}"
        )
        if system_block:
            body = f"{system_block}\n\n{body}"
        return body, all_attachments
    if latest_user:
        if system_block:
            return f"{system_block}\n\nAktuelle Nutzeranfrage:\n{latest_user}", all_attachments
        return latest_user, all_attachments
    if system_block:
        return system_block, all_attachments
    # No user turn at all (e.g. pure system messages): do not forward to LIARA.
    return "", all_attachments


def _responses_input_to_messages(raw_input: Any) -> list[dict[str, Any]]:
    if isinstance(raw_input, str):
        return [{"role": "user", "content": raw_input}]

    if not isinstance(raw_input, list):
        return []

    messages: list[dict[str, Any]] = []
    for item in raw_input:
        if isinstance(item, str):
            if item.strip():
                messages.append({"role": "user", "content": item})
            continue

        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"function_call_output", "tool_result"}:
            tool_result_content = item.get("output")
            if tool_result_content is None:
                tool_result_content = item.get("content")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or item.get("tool_call_id") or ""),
                    "name": str(item.get("name") or item.get("tool_name") or ""),
                    "content": _extract_text(tool_result_content),
                }
            )
            continue

        role = str(item.get("role") or item.get("type") or "user").strip().lower() or "user"
        content = item.get("content")

        if role.startswith("input_"):
            role = "user"

        normalized_content = content if content is not None else item
        if _extract_text(normalized_content).strip() or _extract_content_payload(normalized_content)[1]:
            messages.append({"role": role, "content": normalized_content})

    return messages


def _messages_to_query(messages: list[dict[str, Any]]) -> str:
    query, _attachments = _messages_to_query_payload(messages)
    return query


def _trim_query_text(query: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(query) <= max_chars:
        return query, False

    marker = "\n\n[... älterer Verlauf gekürzt ...]\n\n"
    # Keep most recent content to preserve the latest user intent.
    tail_limit = max(0, max_chars - len(marker))
    trimmed_tail = query[-tail_limit:] if tail_limit else ""
    return marker + trimmed_tail, True


def _extract_tool_results_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract role:tool entries and convert to LIARA tool_results format."""
    results: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role", "")).strip().lower()
        if role not in {"tool", "function"}:
            continue
        content = _extract_text(msg.get("content", ""))
        results.append(
            {
                "tool_call_id": str(msg.get("tool_call_id") or ""),
                "name": str(msg.get("name") or ""),
                "content": content,
            }
        )
    return results


def _normalize_tools_for_liara(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Pass OpenAI-format tool definitions through to LIARA unchanged."""
    if not isinstance(tools, list) or not tools:
        return None
    return tools


def _liara_pending_tool_calls_to_openai(
    pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert LIARA's pending_tool_calls to OpenAI tool_calls format."""
    result: list[dict[str, Any]] = []
    for tc in pending:
        tc_id = str(tc.get("id") or uuid.uuid4().hex[:24])
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        result.append(
            {
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": str(fn.get("name") or tc.get("name") or ""),
                    "arguments": (
                        json.dumps(fn.get("arguments", {}), ensure_ascii=False)
                        if isinstance(fn.get("arguments"), dict)
                        else str(fn.get("arguments") or "{}")
                    ),
                },
            }
        )
    return result


def _openai_tool_calls_response(
    *, model: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build an OpenAI chat.completion response with tool_calls (no text content)."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream_tool_calls(*, model: str, tool_calls: list[dict[str, Any]]):
    """Stream an OpenAI SSE response carrying tool_calls."""
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    # First chunk: role + tool_call deltas
    first = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(first, ensure_ascii=True)}\n\n"

    done = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
    }
    yield f"data: {json.dumps(done, ensure_ascii=True)}\n\n"
    yield "data: [DONE]\n\n"


def _extract_liara_response_text(liara: dict[str, Any]) -> tuple[str, str]:
    direct = _extract_text(liara.get("response", "")).strip()
    if direct:
        return direct, "response"

    metadata = liara.get("metadata") if isinstance(liara.get("metadata"), dict) else {}
    for path, value in [
        ("metadata.final_response", metadata.get("final_response") if isinstance(metadata, dict) else None),
        ("metadata.response", metadata.get("response") if isinstance(metadata, dict) else None),
        ("llm_generation.content", liara.get("llm_generation", {}).get("content") if isinstance(liara.get("llm_generation"), dict) else None),
    ]:
        candidate = _extract_text(value).strip()
        if candidate:
            return candidate, path

    validation = metadata.get("validation") if isinstance(metadata, dict) and isinstance(metadata.get("validation"), dict) else {}
    decision = str(validation.get("decision") or "").strip() if isinstance(validation, dict) else ""
    issues = validation.get("issues") if isinstance(validation, dict) else None
    issue_hint = ""
    if isinstance(issues, list) and issues:
        issue_hint = f" Hinweis: {str(issues[0])[:240]}"

    fallback = "Es wurde keine Antwort erzeugt. Bitte Anfrage erneut senden oder den Kontext kürzen."
    if decision:
        fallback += f" Validierung: {decision}."
    if issue_hint:
        fallback += issue_hint
    return fallback, "fallback"


def _stable_session_id(messages: list[dict[str, Any]], user_id: str) -> str:
    seed = json.dumps(messages[:-1], ensure_ascii=True, sort_keys=True)[:2000]
    digest = hashlib.sha256(f"{SESSION_SALT}:{user_id}:{seed}".encode("utf-8")).hexdigest()[:16]
    return f"continue-{user_id}-{digest}"


async def _call_liara_chat(
    *,
    session_id: str,
    user_id: str,
    message: str,
    max_tokens: int,
    attachments: list[dict[str, Any]] | None = None,
    available_tools: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    allow_external_tool_calls: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "max_tokens": max_tokens,
        "attachments": attachments or [],
    }
    if available_tools:
        payload["available_tools"] = available_tools
    if tool_results:
        payload["tool_results"] = tool_results
    if allow_external_tool_calls:
        payload["allow_external_tool_calls"] = True

    timeout = httpx.Timeout(
        timeout=LIARA_TIMEOUT_SECONDS,
        connect=min(10.0, LIARA_TIMEOUT_SECONDS),
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{LIARA_API_BASE_URL}/chat", json=payload)
    except httpx.ReadTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"LIARA backend timeout after {LIARA_TIMEOUT_SECONDS:.1f}s while calling /chat. "
                "Try reducing context size or increasing LIARA_TIMEOUT_SECONDS."
            ),
        ) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"LIARA backend unreachable at {LIARA_API_BASE_URL}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LIARA backend transport error: {exc}",
        ) from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _openai_response(*, model: str, content: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _responses_api_response(*, model: str, content: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": now,
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": content,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


def _responses_api_tool_calls_response(*, model: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    now = int(time.time())
    output_items: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        output_items.append(
            {
                "id": str(tool_call.get("id") or f"fc_{uuid.uuid4().hex}"),
                "type": "function_call",
                "status": "completed",
                "name": str(fn.get("name") or ""),
                "arguments": str(fn.get("arguments") or "{}"),
                "call_id": str(tool_call.get("id") or f"call_{uuid.uuid4().hex}"),
            }
        )

    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": now,
        "status": "completed",
        "model": model,
        "output": output_items,
        "output_text": "",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


def _stream_chunks(*, model: str, content: str):
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    first = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(first, ensure_ascii=True)}\n\n"

    step = 180
    for i in range(0, len(content), step):
        chunk = content[i : i + step]
        payload = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"

    done = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(done, ensure_ascii=True)}\n\n"
    yield "data: [DONE]\n\n"


def _stream_responses_api(*, model: str, content: str):
    now = int(time.time())
    response_id = f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"

    created = {
        "type": "response.created",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": now,
            "status": "in_progress",
            "model": model,
        },
    }
    yield f"event: response.created\ndata: {json.dumps(created, ensure_ascii=True)}\n\n"

    output_item_added = {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        },
    }
    yield f"event: response.output_item.added\ndata: {json.dumps(output_item_added, ensure_ascii=True)}\n\n"

    step = 180
    for i in range(0, len(content), step):
        chunk = content[i : i + step]
        delta = {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "delta": chunk,
        }
        yield f"event: response.output_text.delta\ndata: {json.dumps(delta, ensure_ascii=True)}\n\n"

    done = {
        "type": "response.output_text.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "text": content,
    }
    yield f"event: response.output_text.done\ndata: {json.dumps(done, ensure_ascii=True)}\n\n"

    completed = {
        "type": "response.completed",
        "response": _responses_api_response(model=model, content=content),
    }
    yield f"event: response.completed\ndata: {json.dumps(completed, ensure_ascii=True)}\n\n"
    yield "data: [DONE]\n\n"


def _stream_responses_api_tool_calls(*, model: str, tool_calls: list[dict[str, Any]]):
    now = int(time.time())
    response_id = f"resp_{uuid.uuid4().hex}"

    created = {
        "type": "response.created",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": now,
            "status": "in_progress",
            "model": model,
        },
    }
    yield f"event: response.created\ndata: {json.dumps(created, ensure_ascii=True)}\n\n"

    for output_index, tool_call in enumerate(tool_calls):
        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        output_item_added = {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {
                "id": str(tool_call.get("id") or f"fc_{uuid.uuid4().hex}"),
                "type": "function_call",
                "status": "completed",
                "name": str(fn.get("name") or ""),
                "arguments": str(fn.get("arguments") or "{}"),
                "call_id": str(tool_call.get("id") or f"call_{uuid.uuid4().hex}"),
            },
        }
        yield f"event: response.output_item.added\ndata: {json.dumps(output_item_added, ensure_ascii=True)}\n\n"

    completed = {
        "type": "response.completed",
        "response": _responses_api_tool_calls_response(model=model, tool_calls=tool_calls),
    }
    yield f"event: response.completed\ndata: {json.dumps(completed, ensure_ascii=True)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "liara-continue-openai-bridge",
        "liara_api_base_url": LIARA_API_BASE_URL,
    }


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": now,
                "owned_by": "liara",
            }
            for model_id in _bridge_model_ids()
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    bridge_request_id = str(uuid.uuid4())
    model = str(body.get("model", "liara-agent"))
    messages = body.get("messages") or []
    tools_raw = body.get("tools") or []
    stream = bool(body.get("stream", False))
    user_id = str(body.get("user") or DEFAULT_USER_ID)

    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages must be a non-empty list")

    # Short-circuit Continue's internal title/meta requests — they must not go through LIARA.
    if _is_meta_request(messages):
        _emit_bridge_audit(
            event="meta_request_short_circuit",
            payload={"bridge_request_id": bridge_request_id, "endpoint": "/v1/chat/completions"},
        )
        stub = "LIARA"
        if stream:
            return StreamingResponse(_stream_chunks(model=model, content=stub), media_type="text/event-stream")
        return JSONResponse(_openai_response(model=model, content=stub))

    raw_query, attachments = _messages_to_query_payload(messages)
    if not raw_query.strip():
        # No user turn extracted (e.g. system-only message batch) — return empty gracefully.
        _emit_bridge_audit(
            event="no_user_query",
            payload={"bridge_request_id": bridge_request_id, "endpoint": "/v1/chat/completions"},
        )
        empty = ""
        if stream:
            return StreamingResponse(_stream_chunks(model=model, content=empty), media_type="text/event-stream")
        return JSONResponse(_openai_response(model=model, content=empty))

    available_tools = _normalize_tools_for_liara(tools_raw if isinstance(tools_raw, list) else None)
    tool_results = _extract_tool_results_from_messages(messages) or None

    query, query_trimmed = _trim_query_text(raw_query, CONTINUE_BRIDGE_MAX_QUERY_CHARS)
    max_tokens = int(body.get("max_tokens") or 1024)
    session_id = _stable_session_id(messages, user_id)
    _emit_bridge_audit(
        event="request_parsed",
        payload={
            "bridge_request_id": bridge_request_id,
            "endpoint": "/v1/chat/completions",
            "model": model,
            "user_id": user_id,
            "session_id": session_id,
            "stream": stream,
            "max_tokens": max_tokens,
            "message_count": len(messages),
            "available_tool_count": len(available_tools) if available_tools else 0,
            "tool_result_count": len(tool_results) if tool_results else 0,
            "query_original_length": len(raw_query),
            "query_length": len(query),
            "query_trimmed": query_trimmed,
            "query_sha256": _sha256_text(query),
            "query_preview": _truncate_text(query),
            "query_text": query if CONTINUE_BRIDGE_LOG_INCLUDE_QUERY_TEXT else None,
            "attachments": [_attachment_log_meta(a) for a in attachments],
        },
    )
    liara = await _call_liara_chat(
        session_id=session_id,
        user_id=user_id,
        message=query,
        max_tokens=max_tokens,
        attachments=attachments,
        available_tools=available_tools,
        tool_results=tool_results,
        allow_external_tool_calls=True,
    )
    content, content_source = _extract_liara_response_text(liara)
    pending_tool_calls_raw = liara.get("pending_tool_calls") or []
    pending_oa = _liara_pending_tool_calls_to_openai(pending_tool_calls_raw) if pending_tool_calls_raw else []
    _emit_bridge_audit(
        event="response_ready",
        payload={
            "bridge_request_id": bridge_request_id,
            "endpoint": "/v1/chat/completions",
            "session_id": session_id,
            "response_length": len(content),
            "response_sha256": _sha256_text(content),
            "response_source": content_source,
            "pending_tool_call_count": len(pending_oa),
            "liara_keys": sorted(liara.keys()),
        },
    )

    # If LIARA decided to call an external tool, return tool_calls instead of content.
    if pending_oa:
        if stream:
            return StreamingResponse(
                _stream_tool_calls(model=model, tool_calls=pending_oa), media_type="text/event-stream"
            )
        return JSONResponse(_openai_tool_calls_response(model=model, tool_calls=pending_oa))

    if stream:
        return StreamingResponse(_stream_chunks(model=model, content=content), media_type="text/event-stream")

    return JSONResponse(_openai_response(model=model, content=content))


@app.post("/v1/responses")
async def responses(request: Request):
    body = await request.json()
    bridge_request_id = str(uuid.uuid4())
    model = str(body.get("model", "liara-agent"))
    raw_input = body.get("input")
    tools_raw = body.get("tools") or []
    stream = bool(body.get("stream", False))
    user_id = str(body.get("user") or DEFAULT_USER_ID)

    messages = _responses_input_to_messages(raw_input)
    if not messages:
        raise HTTPException(status_code=400, detail="input must contain at least one message")

    # Short-circuit meta requests (title gen, summarize) — same as /v1/chat/completions.
    if _is_meta_request(messages):
        _emit_bridge_audit(
            event="meta_request_short_circuit",
            payload={"bridge_request_id": bridge_request_id, "endpoint": "/v1/responses"},
        )
        stub = "LIARA"
        if stream:
            return StreamingResponse(_stream_responses_api(model=model, content=stub), media_type="text/event-stream")
        return JSONResponse(_responses_api_response(model=model, content=stub))

    raw_query, attachments = _messages_to_query_payload(messages)
    if not raw_query.strip():
        _emit_bridge_audit(
            event="no_user_query",
            payload={"bridge_request_id": bridge_request_id, "endpoint": "/v1/responses"},
        )
        empty = ""
        if stream:
            return StreamingResponse(_stream_responses_api(model=model, content=empty), media_type="text/event-stream")
        return JSONResponse(_responses_api_response(model=model, content=empty))

    query, query_trimmed = _trim_query_text(raw_query, CONTINUE_BRIDGE_MAX_QUERY_CHARS)

    available_tools = _normalize_tools_for_liara(tools_raw if isinstance(tools_raw, list) else None)
    tool_results = _extract_tool_results_from_messages(messages) or None
    max_tokens = int(body.get("max_output_tokens") or body.get("max_tokens") or 1024)
    session_id = _stable_session_id(messages, user_id)
    _emit_bridge_audit(
        event="request_parsed",
        payload={
            "bridge_request_id": bridge_request_id,
            "endpoint": "/v1/responses",
            "model": model,
            "user_id": user_id,
            "session_id": session_id,
            "stream": stream,
            "max_tokens": max_tokens,
            "message_count": len(messages),
            "available_tool_count": len(available_tools) if available_tools else 0,
            "tool_result_count": len(tool_results) if tool_results else 0,
            "query_original_length": len(raw_query),
            "query_length": len(query),
            "query_trimmed": query_trimmed,
            "query_sha256": _sha256_text(query),
            "query_preview": _truncate_text(query),
            "query_text": query if CONTINUE_BRIDGE_LOG_INCLUDE_QUERY_TEXT else None,
            "attachments": [_attachment_log_meta(a) for a in attachments],
        },
    )
    liara = await _call_liara_chat(
        session_id=session_id,
        user_id=user_id,
        message=query,
        max_tokens=max_tokens,
        attachments=attachments,
        available_tools=available_tools,
        tool_results=tool_results,
        allow_external_tool_calls=True,
    )
    content, content_source = _extract_liara_response_text(liara)
    pending_tool_calls_raw = liara.get("pending_tool_calls") or []
    pending_oa = _liara_pending_tool_calls_to_openai(pending_tool_calls_raw) if pending_tool_calls_raw else []
    _emit_bridge_audit(
        event="response_ready",
        payload={
            "bridge_request_id": bridge_request_id,
            "endpoint": "/v1/responses",
            "session_id": session_id,
            "response_length": len(content),
            "response_sha256": _sha256_text(content),
            "response_source": content_source,
            "pending_tool_call_count": len(pending_oa),
            "liara_keys": sorted(liara.keys()),
        },
    )

    if pending_oa:
        if stream:
            return StreamingResponse(
                _stream_responses_api_tool_calls(model=model, tool_calls=pending_oa), media_type="text/event-stream"
            )
        return JSONResponse(_responses_api_tool_calls_response(model=model, tool_calls=pending_oa))

    if stream:
        return StreamingResponse(_stream_responses_api(model=model, content=content), media_type="text/event-stream")

    return JSONResponse(_responses_api_response(model=model, content=content))
