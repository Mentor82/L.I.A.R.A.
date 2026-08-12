from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


@dataclass
class InternalRunLogEntry:
    timestamp: float
    run_index: int
    ok: bool
    http_status: int | None
    duration_ms: float
    session_id: str
    request_message: str
    response_preview: str | None
    response_length: int | None
    tools_used: list[str]
    pending_tool_calls_count: int
    error: str | None


def post_json(url: str, payload: dict[str, Any], timeout_sec: int) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_sec) as resp:
        status = int(resp.status)
        body = resp.read().decode("utf-8", errors="replace")
    return status, json.loads(body)


def build_prompt(run_index: int) -> str:
    return (
        f"Interner Arbeitsplatz-Test Lauf {run_index}. "
        "Nutze deinen internen WSL-Debian Arbeitsplatz (nicht externe Tool Calls). "
        "Fuehre nacheinander folgende Kommandos aus: "
        "1) pwd "
        "2) python3 -c \"import sys; print(sys.version.split()[0])\" "
        "3) curl -s -m 5 http://127.0.0.1:8010/health "
        "Antworte am Ende genau in diesem Format:\n"
        "PWD=<pfad>\n"
        "PY=<version>\n"
        "HEALTH_OK=<yes|no>"
    )


def run_once(api_base: str, user_id: str, run_index: int, timeout_sec: int) -> InternalRunLogEntry:
    session_id = f"cont-liara-internal-{uuid.uuid4().hex[:10]}"
    message = build_prompt(run_index)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "allow_external_tool_calls": False,
        "max_tokens": 512,
    }

    started = time.perf_counter()
    try:
        status, response_json = post_json(
            f"{api_base.rstrip('/')}/chat",
            payload,
            timeout_sec=timeout_sec,
        )
        duration_ms = (time.perf_counter() - started) * 1000.0
        response_text = str(response_json.get("response") or "")
        tools_used = [str(t) for t in (response_json.get("tools_used") or [])]
        pending_count = len(response_json.get("pending_tool_calls") or [])
        return InternalRunLogEntry(
            timestamp=time.time(),
            run_index=run_index,
            ok=True,
            http_status=status,
            duration_ms=round(duration_ms, 3),
            session_id=session_id,
            request_message=message,
            response_preview=response_text[:700],
            response_length=len(response_text),
            tools_used=tools_used,
            pending_tool_calls_count=pending_count,
            error=None,
        )
    except error.HTTPError as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        detail = exc.read().decode("utf-8", errors="replace")
        return InternalRunLogEntry(
            timestamp=time.time(),
            run_index=run_index,
            ok=False,
            http_status=int(exc.code),
            duration_ms=round(duration_ms, 3),
            session_id=session_id,
            request_message=message,
            response_preview=None,
            response_length=None,
            tools_used=[],
            pending_tool_calls_count=0,
            error=f"HTTPError: {exc.code} {detail[:500]}",
        )
    except Exception as exc:  # noqa: BLE001
        duration_ms = (time.perf_counter() - started) * 1000.0
        return InternalRunLogEntry(
            timestamp=time.time(),
            run_index=run_index,
            ok=False,
            http_status=None,
            duration_ms=round(duration_ms, 3),
            session_id=session_id,
            request_message=message,
            response_preview=None,
            response_length=None,
            tools_used=[],
            pending_tool_calls_count=0,
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct LIARA internal-workspace probe (no bridge)")
    parser.add_argument("--api-base", default="http://127.0.0.1:8010", help="Base URL of liara-api")
    parser.add_argument("--runs", type=int, default=1, help="Number of probe runs")
    parser.add_argument("--timeout", type=int, default=90, help="HTTP timeout per run in seconds")
    parser.add_argument("--user-id", default="cont-liara-internal-user", help="User ID for requests")
    parser.add_argument(
        "--log-file",
        default="cont_liara/logs/internal_workspace_runs.jsonl",
        help="JSONL output file path",
    )
    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    ok_count = 0
    for i in range(1, int(args.runs) + 1):
        entry = run_once(
            api_base=str(args.api_base),
            user_id=str(args.user_id),
            run_index=i,
            timeout_sec=int(args.timeout),
        )
        if entry.ok:
            ok_count += 1

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

        if entry.ok:
            print(
                f"run={entry.run_index} ok status={entry.http_status} duration_ms={entry.duration_ms} "
                f"tools={entry.tools_used} resp_len={entry.response_length}"
            )
            if entry.response_preview:
                print("response_preview=")
                print(entry.response_preview)
        else:
            print(
                f"run={entry.run_index} fail status={entry.http_status} duration_ms={entry.duration_ms} "
                f"error={entry.error}"
            )

    print(f"summary ok={ok_count}/{args.runs} log={log_path}")
    return 0 if ok_count == int(args.runs) else 2


if __name__ == "__main__":
    raise SystemExit(main())
