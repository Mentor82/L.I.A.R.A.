from __future__ import annotations

import argparse
import getpass
import os
import uuid
from pathlib import Path

from textual_chat import run_textual_chat
from textual_chat.models import ChatSettings


DEFAULT_BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010")


def _default_user_id() -> str:
    env_user = os.getenv("LIARA_USER_ID")
    if env_user:
        return env_user
    # Prefer Windows username conventions first, then generic fallback.
    return os.getenv("USERNAME") or os.getenv("USER") or getpass.getuser() or "cli-user"


DEFAULT_USER_ID = _default_user_id()


def _default_workspace_root() -> str:
    env_root = os.getenv("LIARA_HOME") or os.getenv("LIARA_WORKSPACE_ROOT")
    if env_root:
        return env_root

    wsl_candidates = [
        r"\\wsl.localhost\Debian\home\liara",
        r"\\wsl$\Debian\home\liara",
        r"\\wsl.localhost\Alpine\home\liara",
        r"\\wsl$\Alpine\home\liara",
    ]
    for candidate in wsl_candidates:
        if os.path.exists(candidate):
            return candidate

    return str(Path(__file__).resolve().parents[2])


def _default_timeout() -> float:
    raw_value = os.getenv("LIARA_HTTP_TIMEOUT", "90")
    try:
        return max(5.0, float(raw_value))
    except ValueError:
        return 90.0


def _default_max_tokens() -> int:
    raw_value = os.getenv("LIARA_MAX_TOKENS", "32768")
    try:
        return max(256, int(raw_value))
    except ValueError:
        return 32768


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LIARA Textual frontend (frontend/tex-ui clone)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session-id", default=f"session-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--max-tokens", type=int, default=_default_max_tokens())
    parser.add_argument("--timeout", type=float, default=_default_timeout())
    parser.add_argument("--workspace-root", default=_default_workspace_root())
    parser.add_argument("--sandbox-root", default="/home/liara/workspace")
    parser.add_argument("--workspace-session-id", default=None)
    parser.add_argument("--mode", choices=["chat", "stream"], default="stream")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--cache-dir", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = ChatSettings(
        base_url=args.base_url,
        timeout=args.timeout,
        session_id=args.session_id,
        user_id=args.user_id,
        max_tokens=args.max_tokens,
        workspace_root=args.workspace_root,
        sandbox_root=args.sandbox_root,
        workspace_session_id=args.workspace_session_id,
        mode=args.mode,
        verbose=args.verbose,
        cache_dir=args.cache_dir,
    )
    return run_textual_chat(settings)


if __name__ == "__main__":
    raise SystemExit(main())
