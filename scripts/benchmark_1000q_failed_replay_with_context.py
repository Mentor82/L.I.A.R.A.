"""Replay only failed turns from a 1000q run with stronger context framing.

Purpose:
- Do NOT run full 1000 again
- Replay only failed turns from an existing JSONL audit
- Inject alternative context framing (session summary + memory commit style)
- For recall turns, pre-prime KEY/VALUE in the same replay session

Env:
  LIARA_API_BASE_URL                 default http://127.0.0.1:8010
  BENCHMARK_FAIL_SOURCE_JSONL        source JSONL (default latest benchmark_1000q_primary_*.jsonl)
  BENCHMARK_FAIL_TIMEOUT_S           request timeout (default 120)
  BENCHMARK_FAIL_MAX_TOKENS          max tokens per request (default 256)
  BENCHMARK_FAIL_LIMIT               optional cap of replayed failed turns
  BENCHMARK_FAIL_DRY_RUN             1 = no HTTP
  BENCHMARK_FAIL_RECALL_ONLY         1 = replay only failed recall turns
  BENCHMARK_FAIL_AUDIT_DIR           output dir (default logs/tests)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import urllib.request
import socket


BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
TIMEOUT_S = int(os.getenv("BENCHMARK_FAIL_TIMEOUT_S", "120"))
MAX_TOKENS = int(os.getenv("BENCHMARK_FAIL_MAX_TOKENS", "256"))
DRY_RUN = os.getenv("BENCHMARK_FAIL_DRY_RUN", "0") == "1"
RECALL_ONLY = os.getenv("BENCHMARK_FAIL_RECALL_ONLY", "0") == "1"
LIMIT = int(os.getenv("BENCHMARK_FAIL_LIMIT", "0") or "0")
LANG_FILTER = (os.getenv("BENCHMARK_FAIL_LANGUAGE", "") or "").strip().lower()

_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
_DEFAULT_AUDIT_DIR = Path(__file__).resolve().parents[1] / "logs" / "tests"
AUDIT_DIR = Path(os.getenv("BENCHMARK_FAIL_AUDIT_DIR", str(_DEFAULT_AUDIT_DIR)))

DE_FACTS = {
    "name": "Mia",
    "klasse": "3b",
    "lieblingsfach": "Mathematik",
    "haustier": "Hamster",
    "lieblingsfarbe": "Gruen",
    "lieblingsobst": "Apfel",
    "lieblingsbuch": "Der kleine Drache",
    "hobby": "Malen",
    "schule": "Sonnenweg Grundschule",
    "stadt": "Koeln",
}

EN_FACTS = {
    "name": "Liam",
    "class": "4A",
    "favorite subject": "Math",
    "pet": "Rabbit",
    "favorite color": "Blue",
    "favorite fruit": "Banana",
    "favorite book": "Treasure Island",
    "hobby": "Drawing",
    "school": "Riverbank Primary",
    "city": "London",
}


@dataclass
class ReplayItem:
    turn_index: int
    user_id: str
    language: str
    topic: str
    difficulty: str
    message: str
    has_recall_check: bool


@dataclass
class ReplayResult:
    source_turn_index: int
    user_id: str
    language: str
    topic: str
    session_id: str
    replay_message: str
    elapsed_s: float
    response_excerpt: str
    stream_complete: bool
    required_stages_ok: bool
    response_nonempty: bool
    recall_ok: bool
    error: Optional[str]

    @property
    def passed(self) -> bool:
        return (
            self.stream_complete
            and self.required_stages_ok
            and self.response_nonempty
            and self.recall_ok
            and self.error is None
        )


def _configure_encoding() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _latest_source_jsonl() -> Path:
    explicit = os.getenv("BENCHMARK_FAIL_SOURCE_JSONL", "").strip()
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        raise FileNotFoundError(f"BENCHMARK_FAIL_SOURCE_JSONL not found: {p}")

    candidates = sorted((_DEFAULT_AUDIT_DIR).glob("benchmark_1000q_primary_*.jsonl"))
    if not candidates:
        raise FileNotFoundError("No benchmark_1000q_primary_*.jsonl found in logs/tests")
    return candidates[-1]


def _is_failed(row: dict) -> bool:
    return not (
        bool(row.get("stream_complete"))
        and bool(row.get("required_stages_ok"))
        and bool(row.get("response_nonempty"))
        and bool(row.get("latency_ok", True))
        and bool(row.get("recall_ok", True))
        and (row.get("error") is None)
    )


def _load_failed_items(path: Path) -> list[ReplayItem]:
    items: list[ReplayItem] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not _is_failed(row):
                continue
            has_recall = bool(row.get("has_recall_check")) or str(row.get("topic", "")).strip() == "memory_recall"
            if RECALL_ONLY and not has_recall:
                continue
            row_language = str(row.get("language") or "de").strip().lower()
            if LANG_FILTER and row_language != LANG_FILTER:
                continue
            items.append(
                ReplayItem(
                    turn_index=int(row.get("turn_index") or 0),
                    user_id=str(row.get("user_id") or ""),
                    language=row_language,
                    topic=str(row.get("topic") or ""),
                    difficulty=str(row.get("difficulty") or "easy"),
                    message=str(row.get("message") or ""),
                    has_recall_check=has_recall,
                )
            )

    if LIMIT > 0:
        return items[:LIMIT]
    return items


def _read_sse_stream(resp, *, timeout_s: int) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    start = time.monotonic()
    # Read incrementally and stop once stream signals completion.
    while True:
        if (time.monotonic() - start) > float(timeout_s):
            raise TimeoutError(f"SSE read timed out after {timeout_s}s")
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line.split(":", 1)[1].strip()
        events.append((current_event, payload))
        if current_event == "done":
            break
    return events


def _call_stream(session_id: str, user_id: str, message: str) -> dict:
    payload = json.dumps(
        {
            "session_id": session_id,
            "user_id": user_id,
            "message": message,
            "max_tokens": MAX_TOKENS,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            events = _read_sse_stream(resp, timeout_s=TIMEOUT_S)
    except socket.timeout as exc:
        raise TimeoutError(f"SSE socket timeout after {TIMEOUT_S}s") from exc
    progress_stages: list[str] = []
    chunks: list[str] = []
    final_payload: dict = {}
    has_final = False
    has_done = False

    for evt, data in events:
        try:
            obj = json.loads(data)
        except Exception:
            obj = {}

        if evt == "progress":
            stage = str(obj.get("stage") or "")
            if stage:
                progress_stages.append(stage)
        elif evt == "chunk":
            text = obj.get("text", "")
            if isinstance(text, str) and text:
                chunks.append(text)
        elif evt == "final":
            has_final = True
            final_payload = obj if isinstance(obj, dict) else {}
        elif evt == "done":
            has_done = True

    response_text = "".join(chunks).strip()
    if not response_text:
        response_text = str(final_payload.get("response") or "").strip()

    return {
        "progress_stages": progress_stages,
        "response_text": response_text,
        "has_final": has_final,
        "has_done": has_done,
        "has_chunks": bool(chunks),
    }


def _extract_key(message: str) -> str:
    msg = message or ""
    m = re.search(r"(?:SCHLUESSEL|KEY)\s*=\s*([^|?.]+)", msg, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"Welche\s+(.+?)\s+habe ich dir genannt", msg, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"What\s+(.+?)\s+did I tell you", msg, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    return ""


def _lookup_fact(language: str, key: str) -> str:
    if language == "de":
        return DE_FACTS.get(key, "")
    return EN_FACTS.get(key, "")


def _build_contextual_message(item: ReplayItem) -> tuple[Optional[str], str, str]:
    key = _extract_key(item.message)
    value = _lookup_fact(item.language, key)

    if item.language == "de":
        prime = (
            f"Mein {key} ist {value}."
            if item.has_recall_check and key and value
            else None
        )
    else:
        prime = (
            f"My {key} is {value}."
            if item.has_recall_check and key and value
            else None
        )

    # Always use the original verbatim question for the recall turn.
    # The prime is a plain factual statement (no reply instruction) so the
    # model is not conditioned to keep saying "noted" / "notiert".
    recall_msg = item.message

    return prime, recall_msg if item.has_recall_check else item.message, value.lower().strip()


def _replay_failed(items: list[ReplayItem]) -> list[ReplayResult]:
    results: list[ReplayResult] = []

    for i, item in enumerate(items, start=1):
        # Fresh session per turn — prevents primed values from one turn
        # contaminating the next turn's recall response.
        session_id = f"bench1000-rerunctx-{item.user_id}-t{item.turn_index}-{uuid.uuid4().hex[:8]}"
        prime_msg, replay_msg, expected = _build_contextual_message(item)

        print(f"[{i:04d}/{len(items)}] replay turn={item.turn_index} user={item.user_id} topic={item.topic}")

        if DRY_RUN:
            response = expected or "dry-run-response"
            results.append(
                ReplayResult(
                    source_turn_index=item.turn_index,
                    user_id=item.user_id,
                    language=item.language,
                    topic=item.topic,
                    session_id=session_id,
                    replay_message=replay_msg,
                    elapsed_s=0.0,
                    response_excerpt=response[:220],
                    stream_complete=True,
                    required_stages_ok=True,
                    response_nonempty=True,
                    recall_ok=(expected in response.lower()) if expected else True,
                    error=None,
                )
            )
            continue

        t0 = time.monotonic()
        error = None
        call_result: dict = {}

        try:
            if prime_msg:
                _call_stream(session_id, item.user_id, prime_msg)
            call_result = _call_stream(session_id, item.user_id, replay_msg)
        except Exception as exc:
            error = str(exc)

        elapsed = round(time.monotonic() - t0, 3)
        response_text = str(call_result.get("response_text") or "")
        recall_ok = True
        if item.has_recall_check and expected:
            recall_ok = expected in response_text.lower()

        rr = ReplayResult(
            source_turn_index=item.turn_index,
            user_id=item.user_id,
            language=item.language,
            topic=item.topic,
            session_id=session_id,
            replay_message=replay_msg,
            elapsed_s=elapsed,
            response_excerpt=response_text[:220],
            stream_complete=bool(call_result.get("has_chunks")) and bool(call_result.get("has_final")) and bool(call_result.get("has_done")),
            required_stages_ok=("accepted" in (call_result.get("progress_stages") or []) and "orchestration_complete" in (call_result.get("progress_stages") or [])),
            response_nonempty=bool(response_text.strip()),
            recall_ok=recall_ok,
            error=error,
        )
        results.append(rr)

        status = "PASS" if rr.passed else "FAIL"
        print(f"   -> {status} {rr.elapsed_s:.1f}s")

    return results


def _write_reports(source_file: Path, items: list[ReplayItem], results: list[ReplayResult]) -> tuple[Path, Path]:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    base = f"benchmark_1000q_failed_rerun_ctx_{_TS}"
    jsonl_path = AUDIT_DIR / f"{base}.jsonl"
    summary_path = AUDIT_DIR / f"{base}_summary.json"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in results:
            payload = asdict(row)
            payload["passed"] = row.passed
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    passed = sum(1 for r in results if r.passed)
    summary = {
        "started_from": str(source_file),
        "base_url": BASE_URL,
        "dry_run": DRY_RUN,
        "replayed_failed_turns": len(items),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate_pct": round((passed / len(results) * 100), 1) if results else 0.0,
        "recall_only_mode": RECALL_ONLY,
        "limit": LIMIT,
    }

    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    return jsonl_path, summary_path


def main() -> int:
    _configure_encoding()

    source = _latest_source_jsonl()
    items = _load_failed_items(source)

    print("Replay failed turns with alternate context")
    print(f"  Source:   {source}")
    print(f"  API:      {BASE_URL}")
    print(f"  DryRun:   {DRY_RUN}")
    print(f"  RecallOnly: {RECALL_ONLY}")
    print(f"  LanguageFilter: {LANG_FILTER or 'all'}")
    print(f"  Limit:    {LIMIT or 'all'}")
    print(f"  Failed turns selected: {len(items)}")

    if not items:
        print("No failed turns selected. Nothing to replay.")
        return 0

    results = _replay_failed(items)
    jsonl_path, summary_path = _write_reports(source, items, results)

    passed = sum(1 for r in results if r.passed)
    print("\nSUMMARY")
    print(f"  Replayed: {len(results)}")
    print(f"  Passed:   {passed}")
    print(f"  Failed:   {len(results) - passed}")
    print(f"  JSONL:    {jsonl_path}")
    print(f"  Summary:  {summary_path}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
