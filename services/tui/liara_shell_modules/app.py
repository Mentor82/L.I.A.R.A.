from __future__ import annotations

import argparse
import uuid

from services.cli.main import (
    DEFAULT_BASE_URL,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_USER_ID,
)

from .bootstrap import load_session_id, save_session_id, startup_preflight
from .commands import handle_command
from .constants import console
from .prompting import create_prompt_session, ensure_history_dir, read_input
from .state import ShellState
from .ui import print_shell_header


def run_shell(
    base_url: str = DEFAULT_BASE_URL,
    user_id: str = DEFAULT_USER_ID,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    session_id: str | None = None,
    mode: str = "stream",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    verbose: bool = False,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
) -> int:
    ensure_history_dir()
    persisted_session = load_session_id()
    resolved_session_id = session_id or persisted_session or f"session-{uuid.uuid4().hex[:8]}"
    state = ShellState(
        base_url=base_url.rstrip("/"),
        user_id=user_id,
        timeout=timeout,
        session_id=resolved_session_id,
        mode=mode,
        max_tokens=max_tokens,
        verbose=verbose,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
    )
    save_session_id(state.session_id)

    startup_preflight(state.base_url, state.timeout)

    prompt_session = create_prompt_session()
    print_shell_header(state)

    while True:
        try:
            message = read_input(prompt_session)
        except (KeyboardInterrupt, EOFError):
            console.print("[dim]bye[/dim]")
            return 0

        if not message:
            continue

        try:
            should_exit = handle_command(message, state)
        except Exception as exc:
            console.print(f"[red]command failed:[/red] {exc}")
            continue

        if should_exit:
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prompt Toolkit shell for liara-api")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--session-id")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--mode", choices=["chat", "stream"], default="stream")
    parser.add_argument("--provider", default=None, help="shell runtime provider profile")
    parser.add_argument("--model", default=None, help="shell runtime model profile")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    return run_shell(
        base_url=args.base_url,
        user_id=args.user_id,
        timeout=args.timeout,
        session_id=args.session_id,
        mode=args.mode,
        max_tokens=args.max_tokens,
        verbose=args.verbose,
        preferred_provider=args.provider,
        preferred_model=args.model,
    )
