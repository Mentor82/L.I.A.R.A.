from __future__ import annotations

import argparse
import uuid
from contextlib import suppress

from services.cli.main import (
    cmd_chat,
    cmd_diag,
    cmd_health,
    cmd_history,
    cmd_session,
    cmd_show_context,
    cmd_stream,
    cmd_sys,
    cmd_tool_info,
    cmd_tools,
)

from .constants import MAX_HISTORY_LIMIT, MIN_MAX_TOKENS, console
from .bootstrap import save_session_id
from .state import ShellState
from .ui import (
    print_help,
    print_state_change,
    print_status,
    print_unknown_command_error,
)


def _handle_provider_command(raw: str, state: ShellState) -> None:
    tail = raw[len("/provider"):].strip()
    if not tail or tail == "show":
        console.print(f"[cyan]provider[/cyan]: {state.preferred_provider or '-'}")
        return
    if tail.startswith("set "):
        value = tail.split(" ", 1)[1].strip()
        if not value:
            console.print("[red]usage: /provider set <provider>[/red]")
            return
        state.preferred_provider = value
        print_state_change("provider", value)
        return
    if tail == "reset":
        state.preferred_provider = None
        print_state_change("provider", "-")
        return
    console.print("[red]usage: /provider [show|set <provider>|reset][/red]")


def _handle_model_command(raw: str, state: ShellState) -> None:
    tail = raw[len("/model"):].strip()
    if not tail or tail == "show":
        console.print(f"[cyan]model[/cyan]: {state.preferred_model or '-'}")
        return
    if tail.startswith("set "):
        value = tail.split(" ", 1)[1].strip()
        if not value:
            console.print("[red]usage: /model set <model-name>[/red]")
            return
        state.preferred_model = value
        print_state_change("model", value)
        return
    if tail == "reset":
        state.preferred_model = None
        print_state_change("model", "-")
        return
    console.print("[red]usage: /model [show|set <model-name>|reset][/red]")


def make_args(
    *,
    base_url: str,
    timeout: float,
    session_id: str,
    user_id: str,
    max_tokens: int,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    message: str = "",
    verbose: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        base_url=base_url,
        timeout=timeout,
        session_id=session_id,
        user_id=user_id,
        max_tokens=max_tokens,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        message=message,
        verbose=verbose,
    )


def parse_limit(raw: str) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < 1:
        return None
    return min(value, MAX_HISTORY_LIMIT)


def parse_float(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def run_user_message(message: str, state: ShellState) -> None:
    args = make_args(
        base_url=state.base_url,
        timeout=state.timeout,
        session_id=state.session_id,
        user_id=state.user_id,
        max_tokens=state.max_tokens,
        preferred_provider=state.preferred_provider,
        preferred_model=state.preferred_model,
        message=message,
        verbose=state.verbose,
    )
    if state.mode == "stream":
        cmd_stream(args)
    else:
        cmd_chat(args)


def handle_session_command(raw: str, state: ShellState) -> None:
    tail = raw[len("/session"):].strip()
    if not tail or tail == "show":
        cmd_session(
            argparse.Namespace(
                base_url=state.base_url,
                timeout=state.timeout,
                session_id=state.session_id,
                user_id=state.user_id,
            )
        )
        return
    if tail == "new":
        state.session_id = f"session-{uuid.uuid4().hex[:8]}"
        save_session_id(state.session_id)
        print_state_change("session", state.session_id)
        return
    if tail.startswith("set "):
        new_session = tail.split(" ", 1)[1].strip()
        if not new_session:
            console.print("[red]usage: /session set <session-id>[/red]")
            return
        state.session_id = new_session
        save_session_id(state.session_id)
        print_state_change("session", state.session_id)
        return
    console.print("[red]usage: /session [show|new|set <id>][/red]")


def handle_set_command(raw: str, state: ShellState) -> None:
    payload = raw.split(" ", 1)[1].strip() if " " in raw else ""
    key, _, value = payload.partition(" ")
    key = key.strip().lower()
    value = value.strip()

    if key == "user":
        if not value:
            console.print("[red]usage: /set user <id>[/red]")
            return
        state.user_id = value
        print_state_change("user", state.user_id)
        return

    if key == "timeout":
        parsed = parse_float(value)
        if parsed is None:
            console.print("[red]usage: /set timeout <positive_seconds>[/red]")
            return
        state.timeout = parsed
        print_state_change("timeout", f"{state.timeout:.1f}s")
        return

    if key == "base-url":
        if not value:
            console.print("[red]usage: /set base-url <url>[/red]")
            return
        state.base_url = value.rstrip("/")
        print_state_change("base_url", state.base_url)
        return

    console.print("[red]usage: /set <user|timeout|base-url> <value>[/red]")


def handle_command(message: str, state: ShellState) -> bool:
    if message in {"/quit", "/exit"}:
        console.print("[dim]bye[/dim]")
        return True

    if message in {"/clear", "/cls"}:
        with suppress(Exception):
            console.clear()
        return False

    if message in {"/help", "/?"}:
        print_help()
        return False

    if message in {"/status", "/show-config"}:
        print_status(state)
        return False

    if message == "/health":
        cmd_health(state.base_url, state.timeout)
        return False

    if message.startswith("/history"):
        parts = message.split(" ", 1)
        limit = 20
        if len(parts) > 1 and parts[1].strip():
            parsed_limit = parse_limit(parts[1].strip())
            if parsed_limit is None:
                console.print("[red]usage: /history [positive_limit][/red]")
                return False
            limit = parsed_limit
        cmd_history(
            argparse.Namespace(
                base_url=state.base_url,
                timeout=state.timeout,
                session_id=state.session_id,
                limit=limit,
                include_tool_messages=True,
                run_id=None,
            )
        )
        return False

    if message == "/context":
        cmd_show_context(state.base_url, state.timeout, state.session_id)
        return False

    if message.startswith("/session"):
        handle_session_command(message, state)
        return False

    if message.startswith("/mode "):
        requested = message.split(" ", 1)[1].strip().lower()
        if requested not in {"chat", "stream"}:
            console.print("[red]unknown mode; use chat or stream[/red]")
            return False
        state.mode = requested
        print_state_change("mode", state.mode)
        return False

    if message.startswith("/provider"):
        _handle_provider_command(message, state)
        return False

    if message.startswith("/model"):
        _handle_model_command(message, state)
        return False

    if message.startswith("/max-tokens "):
        raw_value = message.split(" ", 1)[1].strip()
        parsed_limit = parse_limit(raw_value)
        if parsed_limit is None:
            console.print("[red]invalid max token value[/red]")
            return False
        if parsed_limit < MIN_MAX_TOKENS:
            console.print(f"[red]max tokens must be at least {MIN_MAX_TOKENS}[/red]")
            return False
        state.max_tokens = parsed_limit
        print_state_change("max_tokens", str(state.max_tokens))
        return False

    if message.startswith("/set "):
        handle_set_command(message, state)
        return False

    if message == "/tools":
        cmd_tools()
        return False

    if message.startswith("/tools "):
        tool_name = message.split(" ", 1)[1].strip()
        if not tool_name:
            cmd_tools()
        else:
            cmd_tool_info(tool_name, state.base_url, state.timeout)
        return False

    if message == "/sys":
        console.print("[red]usage: /sys <command> [args...] [/red]")
        return False

    if message.startswith("/sys "):
        raw = message.split("/sys ", 1)[1].strip()
        if not raw:
            console.print("[red]usage: /sys <command> [args...] [/red]")
            return False
        parts = raw.split()
        command = parts[0]
        cmd_sys(
            command,
            parts[1:],
            state.base_url,
            state.timeout,
            session_id=state.session_id,
            context="tui.shell.sys",
            source="tui",
        )
        return False

    if message == "/diag":
        cmd_diag()
        return False

    if message.startswith("/"):
        print_unknown_command_error(message)
        return False

    run_user_message(message, state)
    return False
