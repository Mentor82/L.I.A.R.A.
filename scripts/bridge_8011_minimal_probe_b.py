from __future__ import annotations

import json
import pathlib
import urllib.request
from typing import Any

BASE_URL = "http://127.0.0.1:8011"
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_PATH = WORKSPACE_ROOT / "artifacts" / "bridge_e2e" / "minimal_tool_probe_B.txt"

PROMPT = (
    "TRACE_ID: BRIDGE_TOOL_DEBUG_2026_04_26_B WICHTIG: Dies ist ein Minimaltest mit genau EINEM "
    "externen Tool-Call. Keine reine Textantwort. Aufgabe: Rufe exakt einmal write_file auf mit "
    "path=artifacts/bridge_e2e/minimal_tool_probe_B.txt Schreibe exakt diesen Inhalt in die Datei "
    "(inkl. Zeilenumbruechen): TRACE_ID=BRIDGE_TOOL_DEBUG_2026_04_26_B "
    "RESULT=MINIMAL_TOOL_CALL_OK Antworte danach nur mit: "
    "DONE artifacts/bridge_e2e/minimal_tool_probe_B.txt"
)

EXPECTED_CONTENT = "TRACE_ID=BRIDGE_TOOL_DEBUG_2026_04_26_B\nRESULT=MINIMAL_TOOL_CALL_OK\n"


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _run_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = str(fn.get("name") or "")
    args_raw = fn.get("arguments")
    if isinstance(args_raw, str):
        args = json.loads(args_raw) if args_raw else {}
    elif isinstance(args_raw, dict):
        args = args_raw
    else:
        args = {}

    if name != "write_file":
        return {
            "role": "tool",
            "tool_call_id": str(tc.get("id") or ""),
            "name": name,
            "content": json.dumps({"ok": False, "error": f"unexpected tool: {name}"}, ensure_ascii=False),
        }

    target = WORKSPACE_ROOT / str(args.get("path", "")).replace("\\", "/")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = str(args.get("content", ""))
    target.write_text(content, encoding="utf-8")

    return {
        "role": "tool",
        "tool_call_id": str(tc.get("id") or ""),
        "name": "write_file",
        "content": json.dumps({"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT)).replace('\\', '/')}, ensure_ascii=False),
    }


def main() -> int:
    if OUT_PATH.exists():
        OUT_PATH.unlink()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from workspace with 1-based line range",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
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

    messages: list[dict[str, Any]] = [{"role": "user", "content": PROMPT}]

    first = _post(
        "/v1/chat/completions",
        {
            "model": "liara-agent",
            "user": "bridge-minimal-b",
            "stream": False,
            "tools": tools,
            "messages": messages,
        },
    )
    first_msg = first.get("choices", [{}])[0].get("message", {})
    tool_calls = first_msg.get("tool_calls") or []
    print(f"first_tool_calls={len(tool_calls)}")
    if not tool_calls:
        print("FAIL: no tool call emitted")
        return 2

    first_tool = str((tool_calls[0].get("function") or {}).get("name") or "")
    print(f"first_tool_name={first_tool}")

    if first_tool != "write_file":
        print("FAIL: first tool is not write_file")
        return 3

    messages.append({"role": "assistant", "content": first_msg.get("content"), "tool_calls": tool_calls})
    messages.append(_run_tool_call(tool_calls[0]))

    second = _post(
        "/v1/chat/completions",
        {
            "model": "liara-agent",
            "user": "bridge-minimal-b",
            "stream": False,
            "tools": tools,
            "messages": messages,
        },
    )
    final_msg = second.get("choices", [{}])[0].get("message", {})
    final_text = str(final_msg.get("content") or "").strip()
    print(f"final_text={final_text}")

    if not OUT_PATH.exists():
        print("FAIL: output file missing")
        return 4

    content = OUT_PATH.read_text(encoding="utf-8")
    print("file_content=")
    print(content)

    if "TRACE_ID=BRIDGE_TOOL_DEBUG_2026_04_26_B" not in content:
        print("FAIL: trace id missing")
        return 5

    print("OK: MINIMAL_B_PROBE_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
