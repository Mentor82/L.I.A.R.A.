from __future__ import annotations

import importlib
import os
from typing import Any

from .constants import HISTORY_FILE, KNOWN_COMMANDS, LIARA_DIR, SHELL_STYLE, console


def ensure_history_dir() -> None:
    os.makedirs(LIARA_DIR, exist_ok=True)


def create_prompt_session() -> Any:
    """Create prompt_toolkit session lazily with a clear error if dependency is missing."""
    try:
        prompt_toolkit = importlib.import_module("prompt_toolkit")
        completion = importlib.import_module("prompt_toolkit.completion")
        history = importlib.import_module("prompt_toolkit.history")
        styles = importlib.import_module("prompt_toolkit.styles")
    except ImportError as exc:
        raise RuntimeError(
            "prompt_toolkit is required for services.tui.liara_shell. "
            "Install it with: pip install prompt-toolkit"
        ) from exc

    PromptSession = getattr(prompt_toolkit, "PromptSession")
    NestedCompleter = getattr(completion, "NestedCompleter")
    FileHistory = getattr(history, "FileHistory")
    Style = getattr(styles, "Style")

    return PromptSession(
        history=FileHistory(HISTORY_FILE),
        completer=NestedCompleter.from_nested_dict(KNOWN_COMMANDS),
        style=Style.from_dict(SHELL_STYLE),
        complete_while_typing=True,
    )


def read_input(prompt_session: Any) -> str:
    """Read one message, supporting multiline blocks via /paste or triple quotes."""
    line = prompt_session.prompt("liara> ", multiline=False, mouse_support=True).strip()
    if line not in ('"""', "/paste"):
        return line

    console.print(
        '[dim]Multi-line mode: paste/type lines, then finish with [bold]"""[/bold] on its own line[/dim]'
    )
    collected: list[str] = []
    while True:
        part = prompt_session.prompt("... ", multiline=False, mouse_support=True)
        if part.strip() == '"""':
            break
        collected.append(part)
    return "\n".join(collected).strip()
