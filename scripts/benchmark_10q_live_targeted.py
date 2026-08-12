"""Targeted 10-question live chat runner.

Runs 10 focused chat/stream turns against the live LIARA API in one shared
session. The sequence is designed to probe memory store/recall behavior plus a
small amount of architecture-oriented knowledge retrieval.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
USER_ID = os.getenv("LIARA_BENCHMARK_USER_ID", "targeted-live")
SESSION_ID = os.getenv(
    "LIARA_BENCHMARK_SESSION_ID",
    f"benchmark-10q-targeted-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}",
)
MAX_TOKENS = int(os.getenv("LIARA_BENCHMARK_MAX_TOKENS", "512"))
TIMEOUT_S = int(os.getenv("LIARA_BENCHMARK_TIMEOUT_S", "180"))

QUESTIONS = [
    ("store_name", "Mein Name ist Nora."),
    ("store_color", "Meine Lieblingsfarbe ist Cyan."),
    ("store_city", "Ich wohne in Bremen."),
    ("store_hobby", "Mein Hobby ist Klettern."),
    ("recall_name", "Wie heisse ich?"),
    ("recall_color", "Was ist meine Lieblingsfarbe?"),
    ("recall_city", "Wo wohne ich?"),
    ("recall_hobby", "Was ist mein Hobby?"),
    ("arch_facts", "Erklaere kurz den Unterschied zwischen Postgres und Qdrant in LIARA."),
    ("arch_relations", "Welche Beziehung besteht zwischen Facts, semantic memory und dem Librarian-Routing?"),
]

EXPECTED = {
    "recall_name": "Nora",
    "recall_color": "Cyan",
    "recall_city": "Bremen",
    "recall_hobby": "Klettern",
}


def _configure_console_encoding() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _request_payload(message: str) -> bytes:
    return json.dumps(
        {
            "session_id": SESSION_ID,
            "user_id": USER_ID,
            "message": message,
            "max_tokens": MAX_TOKENS,
        }
    ).encode("utf-8")


def _read_sse_events(raw: bytes) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    for raw_line in raw.splitlines():
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            events.append((current_event, line.split(":", 1)[1].strip()))
    return events


def chat_stream(message: str) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=_request_payload(message),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        raw = response.read()

    events = _read_sse_events(raw)
    progress_stages: list[str] = []
    response_chunks: list[str] = []
    final_payload: dict = {}
    memory_effect_detected = False

    for event, data in events:
        try:
            obj = json.loads(data)
        except Exception:
            obj = {}

        if event == "progress":
            stage = str(obj.get("stage") or "")
            if stage:
                progress_stages.append(stage)
            if stage == "memory_effect_detected":
                memory_effect_detected = True
        elif event == "chunk":
            text = obj.get("text")
            if isinstance(text, str) and text:
                response_chunks.append(text)
        elif event == "final" and isinstance(obj, dict):
            final_payload = obj

    response_text = "".join(response_chunks).strip() or str(final_payload.get("response") or "").strip()
    metadata = final_payload.get("metadata") or {}

    return {
        "response": response_text,
        "memory_effect_detected": memory_effect_detected,
        "progress_stages": progress_stages,
        "context_debug": metadata.get("context_debug") or {},
        "validation": metadata.get("validation") or {},
        "tools_used": final_payload.get("tools_used") or [],
        "routing_reason": final_payload.get("routing_reason") or "",
    }


def main() -> None:
    _configure_console_encoding()
    print(f"Session: {SESSION_ID}")
    print(f"Base URL: {BASE_URL}")
    print("-" * 72)

    rows: list[dict] = []
    for index, (label, question) in enumerate(QUESTIONS, start=1):
        started = time.monotonic()
        error = None
        result: dict = {}
        try:
            result = chat_stream(question)
        except Exception as exc:
            error = str(exc)
        elapsed = round(time.monotonic() - started, 3)

        response = result.get("response", "") if not error else ""
        expected = EXPECTED.get(label)
        expected_hit = expected.lower() in response.lower() if expected and response else None

        row = {
            "index": index,
            "label": label,
            "question": question,
            "elapsed_s": elapsed,
            "error": error,
            "response": response,
            "memory_effect_detected": result.get("memory_effect_detected", False) if not error else False,
            "progress_stages": result.get("progress_stages", []) if not error else [],
            "context_debug": result.get("context_debug", {}) if not error else {},
            "validation": result.get("validation", {}) if not error else {},
            "tools_used": result.get("tools_used", []) if not error else [],
            "routing_reason": result.get("routing_reason", "") if not error else "",
            "expected": expected,
            "expected_hit": expected_hit,
        }
        rows.append(row)

        print(f"[{index:>2}/10] {label:<14} t={elapsed:>6}s mem={str(row['memory_effect_detected']).lower():<5} hit={str(expected_hit).lower() if expected_hit is not None else '-'}")
        if error:
            print(f"      ERROR: {error}")
        else:
            print(f"      Q: {question}")
            print(f"      A: {response[:220]}")

    out = {
        "session_id": SESSION_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "results": rows,
        "summary": {
            "errors": sum(1 for row in rows if row["error"]),
            "memory_effect_detected": sum(1 for row in rows if row["memory_effect_detected"]),
            "recall_hits": sum(1 for row in rows if row["expected_hit"] is True),
            "recall_total": sum(1 for row in rows if row["expected"] is not None),
        },
    }

    os.makedirs("logs/tests", exist_ok=True)
    out_path = f"logs/tests/benchmark_10q_targeted_{SESSION_ID}.json"
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print("-" * 72)
    print(f"JSON gespeichert: {out_path}")


if __name__ == "__main__":
    main()