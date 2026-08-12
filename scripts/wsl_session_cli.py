#!/usr/bin/env python3
"""Direct command interface for LIARA's native-WSL session runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.simulation.wsl_session_runtime import WslSessionError, WslSessionManager
from services.tools.builtin.wsl_executor import WslExecutorTool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and use temporary native-WSL LIARA sessions")
    parser.add_argument("--request-id")
    parser.add_argument("--run-id")
    parser.add_argument("--trace-session-id")
    parser.add_argument("--source", default="wsl_session_cli")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("plan")

    create = sub.add_parser("create")
    create.add_argument("--label", default="simulation")

    for action in ("status", "collect", "destroy"):
        command = sub.add_parser(action)
        command.add_argument("session_id")

    execute = sub.add_parser("exec")
    execute.add_argument("session_id")
    execute.add_argument("--timeout", type=int, default=30)
    execute.add_argument("--stdin", action="store_true", help="Forward UTF-8 stdin to an enabled command such as tee or julia")
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


async def _run(args: argparse.Namespace) -> dict:
    manager = WslSessionManager()
    if args.action == "plan":
        return await asyncio.to_thread(manager.plan)
    if args.action == "create":
        return await asyncio.to_thread(
            manager.create,
            label=args.label,
            request_id=args.request_id,
            run_id=args.run_id,
            trace_session_id=args.trace_session_id,
            source=args.source,
        )
    if args.action == "status":
        return await asyncio.to_thread(manager.status, args.session_id)
    if args.action == "collect":
        return await asyncio.to_thread(manager.collect, args.session_id)
    if args.action == "destroy":
        return await asyncio.to_thread(manager.destroy, args.session_id)
    if args.action == "exec":
        argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
        if not argv:
            raise WslSessionError("exec requires a direct command after --")
        result = await WslExecutorTool().execute(
            command=argv[0],
            args=argv[1:],
            stdin_text=sys.stdin.read() if args.stdin else None,
            timeout=args.timeout,
            workspace_session_id=args.session_id,
            request_id=args.request_id,
            run_id=args.run_id,
            session_id=args.trace_session_id or args.session_id,
            source=args.source,
            context="wsl_session_cli_exec",
        )
        if result.get("status") != "success":
            raise WslSessionError(str(result.get("error") or "session command failed"))
        return result
    raise WslSessionError("unsupported action")


def main() -> None:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
        print(json.dumps({"ok": True, "action": args.action, "result": result}, indent=2))
    except (WslSessionError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "action": getattr(args, "action", None), "error": str(exc)},
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
