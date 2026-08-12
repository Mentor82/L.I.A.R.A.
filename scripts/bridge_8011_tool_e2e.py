from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "http://127.0.0.1:8011"
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT_FILE = WORKSPACE_ROOT / "artifacts" / "bridge_e2e" / "architecture_review.txt"


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_path(raw_path: str) -> pathlib.Path:
    normalized = raw_path.replace("\\", "/").lstrip("/")
    candidate = (WORKSPACE_ROOT / normalized).resolve()
    if WORKSPACE_ROOT not in [candidate, *candidate.parents]:
        raise ValueError(f"path escapes workspace: {raw_path}")
    return candidate


def _tool_read_file(args: dict[str, Any]) -> str:
    path = _safe_path(str(args.get("path", "")))
    start_line = int(args.get("start_line", 1))
    end_line = int(args.get("end_line", start_line + 120))
    if start_line < 1:
        start_line = 1
    if end_line < start_line:
        end_line = start_line

    if not path.exists() or not path.is_file():
        return json.dumps({"ok": False, "error": f"file not found: {path}"}, ensure_ascii=False)

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    segment = lines[start_line - 1 : end_line]
    return json.dumps(
        {
            "ok": True,
            "path": str(path.relative_to(WORKSPACE_ROOT)).replace("\\", "/"),
            "start_line": start_line,
            "end_line": end_line,
            "content": "\n".join(segment),
        },
        ensure_ascii=False,
    )


def _tool_write_file(args: dict[str, Any]) -> str:
    path = _safe_path(str(args.get("path", "")))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return json.dumps(
        {
            "ok": True,
            "path": str(path.relative_to(WORKSPACE_ROOT)).replace("\\", "/"),
            "bytes": len(content.encode("utf-8")),
        },
        ensure_ascii=False,
    )


def _run_tool(name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"invalid tool args: {exc}"}, ensure_ascii=False)

    try:
        if name == "read_file":
            return _tool_read_file(args)
        if name == "write_file":
            return _tool_write_file(args)
        return json.dumps({"ok": False, "error": f"unsupported tool: {name}"}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def _run_chat_completions_flow(tools: list[dict[str, Any]], user_prompt: str) -> tuple[bool, str]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    max_rounds = 6
    saw_tool_call = False

    for _ in range(max_rounds):
        response = _post_json(
            f"{BASE_URL}/v1/chat/completions",
            {
                "model": "liara-agent",
                "user": "bridge-e2e-tool-test-chat",
                "stream": False,
                "tools": tools,
                "messages": messages,
            },
        )

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls") or []

        if tool_calls:
            saw_tool_call = True
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
            )
            for tc in tool_calls:
                tc_id = str(tc.get("id", ""))
                fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name", ""))
                arguments = str(fn.get("arguments", "{}"))
                tool_output = _run_tool(name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": name,
                        "content": tool_output,
                    }
                )
            continue

        final_text = str(message.get("content") or "").strip()
        return saw_tool_call, final_text

    return saw_tool_call, "FAIL: tool loop exceeded max rounds"


def _run_responses_flow(tools: list[dict[str, Any]], user_prompt: str) -> tuple[bool, str]:
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_prompt}],
        }
    ]
    max_rounds = 6
    saw_tool_call = False

    for _ in range(max_rounds):
        response = _post_json(
            f"{BASE_URL}/v1/responses",
            {
                "model": "liara-agent",
                "user": "bridge-e2e-tool-test-responses",
                "stream": False,
                "tools": tools,
                "input": messages,
            },
        )

        output_items = response.get("output") or []
        tool_items = [item for item in output_items if item.get("type") == "function_call"]
        if tool_items:
            saw_tool_call = True
            for item in tool_items:
                call_id = str(item.get("call_id") or item.get("id") or "")
                name = str(item.get("name") or "")
                arguments = str(item.get("arguments") or "{}")
                tool_output = _run_tool(name, arguments)
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "name": name,
                        "output": tool_output,
                    }
                )
            continue

        final_text = str(response.get("output_text") or "").strip()
        return saw_tool_call, final_text

    return saw_tool_call, "FAIL: tool loop exceeded max rounds"


def main() -> int:
    try:
        health = _get_json(f"{BASE_URL}/health")
    except urllib.error.URLError as exc:
        print(f"FAIL: bridge unreachable: {exc}")
        return 1

    print(f"bridge health: {health}")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from workspace with 1-based line range",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create or overwrite a text file in workspace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]

    user_prompt = (
        "Nutze zwingend die verfuegbaren Tools. "
        "1) Lies docs/ARCHITECTURE.md (mindestens die ersten 220 Zeilen). "
        "2) Erstelle eine kurze Analyse mit 5 Bulletpoints. "
        "3) Schreibe das Ergebnis in artifacts/bridge_e2e/architecture_review.txt per write_file. "
        "Wenn erledigt, antworte mit DONE und nenne den Dateipfad."
    )

    chat_saw_tool, chat_text = _run_chat_completions_flow(tools, user_prompt)
    print(f"chat_completions_final: {chat_text}")

    responses_saw_tool, responses_text = _run_responses_flow(tools, user_prompt)
    print(f"responses_final: {responses_text}")

    if not chat_saw_tool and not responses_saw_tool:
        print("FAIL: no tool_calls were emitted by either bridge endpoint")
        return 2

    if not OUTPUT_FILE.exists():
        print(f"FAIL: output file not created: {OUTPUT_FILE}")
        return 3

    content = OUTPUT_FILE.read_text(encoding="utf-8", errors="replace").strip()
    preview = "\n".join(content.splitlines()[:8])
    print(f"OK: output file created: {OUTPUT_FILE}")
    print("output_preview:")
    print(preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
